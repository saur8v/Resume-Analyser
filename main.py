"""
FastAPI backend for the Resume Analyser & Improver dashboard.

Endpoints:
  POST /api/job-description   { url }                       -> { job_description }
  POST /api/analyze           multipart: resume (PDF), job_description (str)
                                                              -> { improvements }
  POST /api/pdf-summary        { job_description, improvements } -> PDF file download

Serves the static frontend (static/index.html etc.) at "/".
"""

import io
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import resume_analyzer as ra
from pdf_export import build_summary_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resume-dashboard")

app = FastAPI(title="Resume Analyser & Improver")

# Allow the static frontend to call the API even if served from a different
# origin during development (e.g. a Vite/live-server dev port).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobDescriptionRequest(BaseModel):
    url: str
    wait_selector: str = "body"


@app.post("/api/job-description")
def job_description(req: JobDescriptionRequest):
    try:
        markdown = ra.get_job_description(req.url, wait_selector=req.wait_selector)
    except Exception as exc:
        logger.exception("Failed to extract job description")
        raise HTTPException(
            status_code=502,
            detail=(
                "Couldn't scrape that URL. Some sites (e.g. LinkedIn) block "
                f"headless scraping or require login. Original error: {exc}"
            ),
        ) from exc

    return {"job_description": markdown}


@app.post("/api/analyze")
async def analyze(
    resume: UploadFile = File(...),
    job_description: str = Form(...),
):
    if resume.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload a PDF resume.")

    pdf_bytes = await resume.read()

    try:
        resume_text = ra.extract_text_from_pdf_bytes(pdf_bytes)
        if not resume_text.strip():
            raise ValueError(
                "No extractable text found in the PDF (it may be a scanned image)."
            )

        improvements = ra.get_resume_improvements_from_text(
            resume_text, job_description
        )
    except Exception as exc:
        logger.exception("Failed to analyze resume")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"improvements": improvements}


class PdfSummaryRequest(BaseModel):
    job_description: str
    improvements: str


@app.post("/api/pdf-summary")
def pdf_summary(req: PdfSummaryRequest):
    try:
        pdf_bytes = build_summary_pdf(req.job_description, req.improvements)
    except Exception as exc:
        logger.exception("Failed to build PDF summary")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=resume-analysis-summary.pdf"
        },
    )


# Serve the frontend last so it doesn't shadow the /api routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
