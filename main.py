"""
FastAPI backend for the Resume Analyser & Improver dashboard.

Auth (session cookie):
  POST   /api/auth/signup            { email, password, full_name }
  POST   /api/auth/login             { email, password }
  POST   /api/auth/logout
  GET    /api/auth/me

Profile (structured JSON, see profile_schema.py):
  GET    /api/profile                                  -> { profile, version, updated_at, resume, can_undo_upload }
  PUT    /api/profile                { profile, version } -> same; 409 if version is stale
  POST   /api/profile/resume         multipart: resume (PDF), version
                                     -> parses the PDF, merges it into the profile (profile_merge.py)
                                        and saves both; adds merge_summary to the response
  POST   /api/profile/resume/undo    { version } -> restores the profile from before the last upload
  GET    /api/profile/resume         -> the stored resume PDF

Job descriptions:
  POST   /api/job-description        { url }   -> { job_description }
  POST   /api/job-description-manual { text }  -> { job_description }

Analyses (history, each with a snapshot of the profile used):
  POST   /api/analyses               { job_description, title, job_url? } -> analysis
  GET    /api/analyses               -> [summary]
  GET    /api/analyses/{id}          -> analysis
  DELETE /api/analyses/{id}
  GET    /api/analyses/{id}/pdf      -> PDF report download

Serves the static frontend (static/index.html etc.) at "/".
"""

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

import auth
import db
import resume_analyzer as ra
from job_insights import insights_title, reconcile_skills
from pdf_export import build_report_pdf
from resume_markup import build_markup
from profile_merge import merge_profiles
from profile_schema import empty_profile, profile_for_llm, profile_has_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resume-dashboard")

MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_JD_TEXT_CHARS = 60_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="Resume Analyser & Improver")
db.init_db()


def _validation_detail(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"Invalid profile data at '{location}': {first['msg']}"


# ============================================================
# Auth
# ============================================================

class SignupRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


@app.post("/api/auth/signup")
def signup(req: SignupRequest, response: Response):
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")

    profile = empty_profile()
    profile["basics"]["full_name"] = req.full_name.strip()
    profile["basics"]["email"] = email

    user_id = db.create_user(email, auth.hash_password(req.password), profile)
    if user_id is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    auth.start_session(response, user_id)
    return {"email": email}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response):
    user = auth.authenticate(req.email.strip().lower(), req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    auth.start_session(response, user["id"])
    return {"email": user["email"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.end_session(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(auth.current_user)):
    return {"email": user["email"]}


# ============================================================
# Profile
# ============================================================

class ProfileUpdateRequest(BaseModel):
    profile: dict
    version: int


@app.get("/api/profile")
def get_profile(user=Depends(auth.current_user)):
    return db.get_profile(user["id"])


@app.put("/api/profile")
def update_profile(req: ProfileUpdateRequest, user=Depends(auth.current_user)):
    try:
        return db.save_profile(user["id"], req.profile, req.version)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    except db.ProfileConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/profile/resume")
def upload_resume(
    resume: UploadFile = File(...),
    version: int = Form(...),
    user=Depends(auth.current_user),
):
    if resume.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload a PDF resume.")

    pdf_bytes = resume.file.read(MAX_RESUME_BYTES + 1)
    if len(pdf_bytes) > MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="Resume PDF must be 5 MB or smaller.")

    try:
        resume_text = ra.extract_text_from_pdf_bytes(pdf_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Couldn't read that PDF.") from exc
    if not resume_text.strip():
        raise HTTPException(
            status_code=400,
            detail="No extractable text found in the PDF (it may be a scanned image).",
        )

    inputs = db.get_merge_inputs(user["id"])
    if inputs["version"] != version:
        # Fail before spending an LLM call; save_profile would reject it anyway.
        raise HTTPException(status_code=409, detail=str(db.ProfileConflict(inputs["version"])))

    # Profiles saved before merging existed have no baseline. If the previous
    # resume's text is stored, re-parse it (in parallel) to rebuild one, so
    # fields the user never touched can still be updated from the new resume.
    rebuild_baseline = inputs["baseline"] is None and bool(inputs["resume_text"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        parsed_future = pool.submit(ra.parse_resume_to_profile, resume_text)
        baseline_future = pool.submit(ra.parse_resume_to_profile, inputs["resume_text"]) if rebuild_baseline else None

        try:
            parsed = parsed_future.result()
        except Exception as exc:
            logger.exception("Failed to parse resume into profile JSON")
            raise HTTPException(status_code=502, detail=f"Couldn't parse the resume: {exc}") from exc

        baseline = inputs["baseline"]
        if baseline_future:
            try:
                baseline = baseline_future.result()
            except Exception:
                logger.exception("Couldn't rebuild the baseline; merging without one")

    # Merge instead of replacing: hand edits, entries missing from the new
    # resume and additional context are all kept.
    merged, summary = merge_profiles(inputs["profile"], parsed, baseline)

    try:
        record = db.save_profile(
            user["id"], merged, version,
            resume={"filename": resume.filename or "resume.pdf", "pdf": pdf_bytes,
                    "text": resume_text, "baseline": parsed},
        )
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail=_validation_detail(exc)) from exc
    except db.ProfileConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**record, "merge_summary": summary}


class UndoUploadRequest(BaseModel):
    version: int


@app.post("/api/profile/resume/undo")
def undo_resume_upload(req: UndoUploadRequest, user=Depends(auth.current_user)):
    try:
        return db.undo_resume_upload(user["id"], req.version)
    except db.NothingToUndo as exc:
        raise HTTPException(status_code=404, detail="There's no resume upload to undo.") from exc
    except db.ProfileConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/profile/resume")
def download_resume(user=Depends(auth.current_user)):
    row = db.get_resume_file(user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="No resume uploaded yet.")
    return StreamingResponse(
        io.BytesIO(row["resume_pdf"]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(row['resume_filename'])}"},
    )


# ============================================================
# Job descriptions
# ============================================================

class JobDescriptionRequest(BaseModel):
    url: str = Field(max_length=2000)
    wait_selector: str = "body"


class ManualJobDescriptionRequest(BaseModel):
    text: str


@app.post("/api/job-description-manual")
def job_description_manual(req: ManualJobDescriptionRequest, user=Depends(auth.current_user)):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste a job description first.")
    if len(text) > MAX_JD_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="That job description is too long.")

    try:
        markdown = ra.extract_job_description_from_text(text)
    except ra.NotAJobPosting as exc:
        raise HTTPException(
            status_code=422,
            detail="That text doesn't look like a job description. Paste the full posting: "
                   "the role, responsibilities and requirements.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to extract job description from pasted text")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"job_description": markdown}


SCRAPE_FAILED = (
    "We couldn't read a job description from that link. The site may need a login "
    "or block automated access. Paste the job description below instead."
)


@app.post("/api/job-description")
def job_description(req: JobDescriptionRequest, user=Depends(auth.current_user)):
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Enter a full http(s) URL.")

    # Guardrail: no analysis runs unless a real job posting was read from the page.
    # Login walls, captchas and empty pages fail here instead of producing a
    # made-up job description.
    try:
        markdown = ra.get_job_description(req.url, wait_selector=req.wait_selector)
    except ra.NotAJobPosting as exc:
        logger.info("No job posting found at %s: %s", req.url, exc)
        raise HTTPException(status_code=422, detail=SCRAPE_FAILED) from exc
    except Exception as exc:
        logger.exception("Failed to scrape %s", req.url)
        raise HTTPException(status_code=422, detail=SCRAPE_FAILED) from exc

    return {"job_description": markdown}


# ============================================================
# Analyses
# ============================================================

class AnalysisRequest(BaseModel):
    job_description: str = Field(min_length=1, max_length=MAX_JD_TEXT_CHARS)
    title: str = Field(min_length=1, max_length=200)
    job_url: str | None = Field(default=None, max_length=2000)


@app.post("/api/analyses")
def create_analysis(req: AnalysisRequest, user=Depends(auth.current_user)):
    current = db.get_profile(user["id"])
    profile = current["profile"]
    if not profile_has_content(profile):
        raise HTTPException(
            status_code=400,
            detail="Your profile is empty. Upload a resume or fill in your profile first.",
        )

    profile_json = profile_for_llm(profile)
    # The report and the (small, fast) insights call run in parallel, so the
    # insights add no waiting time. Insights are optional: if that call fails
    # the analysis is still saved, just without them.
    with ThreadPoolExecutor(max_workers=2) as pool:
        report_future = pool.submit(ra.get_resume_improvements_from_text, profile_json, req.job_description)
        insights_future = pool.submit(ra.get_job_insights, profile_json, req.job_description, profile)

        try:
            improvements = report_future.result()
        except Exception as exc:
            logger.exception("Failed to analyze resume")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            insights = insights_future.result()
        except Exception:
            logger.exception("Failed to extract job insights; saving the analysis without them")
            insights = None

    # Prefer the structured company/role for the history title when known.
    title = insights_title(insights) or req.title.strip()

    # Snapshot the exact profile used so old reports stay accurate after edits.
    return _with_markup(db.create_analysis(
        user["id"], title[:200], req.job_url, req.job_description,
        improvements, profile, current["version"], insights,
    ))


def _with_markup(analysis):
    """Attach the rule-based marked-up resume. It's derived from the stored
    profile snapshot and insights, so it's computed on read rather than stored:
    older analyses get it too, and rule tweaks apply to every report."""
    analysis["insights"] = reconcile_skills(analysis.get("insights"), analysis["profile_snapshot"])
    analysis["markup"] = build_markup(analysis["profile_snapshot"], analysis["insights"])
    return analysis


@app.get("/api/analyses")
def list_analyses(user=Depends(auth.current_user)):
    return db.list_analyses(user["id"])


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: int, user=Depends(auth.current_user)):
    analysis = db.get_analysis(user["id"], analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return _with_markup(analysis)


@app.delete("/api/analyses/{analysis_id}")
def delete_analysis(analysis_id: int, user=Depends(auth.current_user)):
    if not db.delete_analysis(user["id"], analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return {"ok": True}


@app.get("/api/analyses/{analysis_id}/pdf")
def analysis_pdf(analysis_id: int, user=Depends(auth.current_user)):
    analysis = db.get_analysis(user["id"], analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")

    try:
        pdf_bytes = build_report_pdf(_with_markup(analysis))
    except Exception as exc:
        logger.exception("Failed to build PDF summary")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = re.sub(r'[\\/:*?"<>|]', "-", analysis["title"]).strip() or "resume-analysis"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}.pdf"},
    )


# Serve the frontend last so it doesn't shadow the /api routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
