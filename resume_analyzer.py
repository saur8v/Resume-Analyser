"""
Core logic for the resume analyser & improver.

Refactored from the original script into an importable module so it can be
called from a FastAPI backend. Behavior is unchanged: it still scrapes a
job posting URL with Selenium, extracts resume text with PyPDF2, and uses
Gemini (via the OpenAI-compatible API) to extract the job description and
compare it against the resume.
"""

import os
import re

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
import PyPDF2

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# Configuration
# ============================================================

load_dotenv(override=True)

# GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENAI_MODEL = "gpt-6-luna"
# Smaller, faster model for structured extraction (job descriptions, resume -> JSON).
EXTRACTION_MODEL = "gpt-5.4-nano"

API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY is not set. Add it to your .env file."
    )
if API_KEY.strip() != API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY has leading/trailing whitespace — check your .env file."
    )

client = OpenAI()


# ============================================================
# Selenium
# ============================================================

def get_driver():
    options = Options()

    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")

    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )

    return webdriver.Chrome(options=options)


def fetch_website_contents(url, wait_selector="body", timeout=10):
    driver = get_driver()

    try:
        driver.get(url)

        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
        )

        html = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(
        ["script", "style", "img", "input", "noscript", "svg", "footer", "nav"]
    ):
        tag.decompose()

    return soup.get_text(separator="\n", strip=True)


# ============================================================
# Job Description Extraction
# ============================================================

JOB_SYSTEM_PROMPT = """
You are an expert job search assistant who can analyze the contents
of a webpage and extract the exact job description.

Extract:
- Role/title
- Responsibilities
- Skills required
- Experience requirements
- Education requirements
- Information about the company
- Salary, if disclosed
- Location
- Other relevant information

Always start the response with exactly these three lines, before anything else:

**Company:** <hiring company name>
**Role:** <job title>
**Location:** <location>

Use the actual hiring company's name. If the posting is from a recruiter or
job board on behalf of an undisclosed client, write "Undisclosed (via
<recruiter name>)". Write "Not specified" for anything the page doesn't state.
Put only the plain value on each line, with no extra commentary.

If the content is not a single job posting (for example a sign-in or sign-up
page, a captcha or "verify you are human" check, an error or "page not found"
message, a cookie wall, a list of many jobs, or a page with no job details),
respond with exactly NO_JOB_POSTING and nothing else. Never invent a job
description from such a page.

Avoid writing the report whit a lot of bullet points as it increases the length of the report. Use bullet points only where necessary for lists of skills, responsibilities, or requirements.

Do not invent information that is not present in the webpage.

Respond in markdown.
Do not wrap the markdown in a code block.
Respond only with the markdown.
"""

JOB_USER_PROMPT = """
Here are the contents of a recruiter webpage.

Extract the exact job description for this role.

Do not add information that is not present in the provided content.

Webpage contents:

"""


def messages_for_job_description(website_text):
    return [
        {"role": "system", "content": JOB_SYSTEM_PROMPT},
        {"role": "user", "content": JOB_USER_PROMPT + website_text},
    ]


NO_JOB_SENTINEL = "NO_JOB_POSTING"
# A real posting has far more readable text than a login wall or an empty shell page.
MIN_PAGE_TEXT_CHARS = 300


class NotAJobPosting(ValueError):
    """The page (or pasted text) didn't contain a job description we can analyse."""


def _require_job_posting(markdown):
    """Reject extraction output that isn't a real job description."""
    text = (markdown or "").strip()
    if not text or text.upper().startswith(NO_JOB_SENTINEL):
        raise NotAJobPosting("The content isn't a job posting.")

    def field(label):
        match = re.search(rf"^\**{label}:?\**:?\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip(" *") if match else ""

    def unknown(value):
        return not value or value.lower().startswith("not specified")

    if unknown(field("Company")) and unknown(field("Role")):
        raise NotAJobPosting("No company or role could be found.")
    return text


def extract_job_description_from_text(website_text):
    """Send already-obtained text (scraped OR pasted by the user) to the LLM
    for structured extraction into the standard job-description markdown.
    Raises NotAJobPosting if the text isn't a job posting."""
    response = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=messages_for_job_description(website_text),
    )

    return _require_job_posting(response.choices[0].message.content)


def get_job_description(url, wait_selector="body"):
    """Scrape a job posting URL and return the extracted job description (markdown).
    Raises NotAJobPosting when the page has no usable job description (login
    walls, captchas, empty pages), so no analysis runs on a made-up posting."""
    website_text = fetch_website_contents(url, wait_selector=wait_selector)
    if len(website_text.strip()) < MIN_PAGE_TEXT_CHARS:
        raise NotAJobPosting("The page has almost no readable text.")
    return extract_job_description_from_text(website_text)


# ============================================================
# Resume Extraction
# ============================================================

def extract_text_from_pdf_bytes(pdf_bytes):
    """Extract text from PDF bytes (used for uploaded files in the API)."""
    import io

    pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))

    resume_text = ""
    for page in pdf_reader.pages:
        page_text = page.extract_text()
        if page_text:
            resume_text += page_text + "\n"

    return resume_text


def extract_text_from_pdf(pdf_file):
    """Extract text from a PDF file path (kept for CLI / script use)."""
    with open(pdf_file, "rb") as file:
        return extract_text_from_pdf_bytes(file.read())


# ============================================================
# Resume -> Structured Profile JSON
# ============================================================

PROFILE_SYSTEM_PROMPT = """
You convert resume text into a structured JSON profile.

Rules:
- Copy facts exactly as they appear in the resume. Do not invent, infer,
  embellish or rephrase achievements.
- Use an empty string or empty list for anything the resume does not state.
- Keep each bullet point from the resume as one item in "highlights".
- Group skills under the category headings used in the resume; if there are
  none, use a single "Skills" category.
- Dates stay in the resume's own format (e.g. "May 2025"). Set is_current to
  true only if the role is marked as present/current.
- Leave additional_context empty; it is reserved for the candidate.
"""

_UNSUPPORTED_SCHEMA_KEYS = {"maxLength", "minLength", "maxItems", "minItems"}


def _strict_schema(model):
    """Strict JSON schema for a Pydantic model, minus length keywords that
    strict structured outputs may reject. Lengths are still enforced locally
    when the result is validated."""
    from openai.lib._pydantic import to_strict_json_schema

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(to_strict_json_schema(model))


def _structured_call(system_prompt, user_content, model, name):
    """One strict-structured-output call on the extraction model; returns the parsed JSON."""
    import json

    response = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": _strict_schema(model)},
        },
    )

    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise ValueError(f"The model declined the request: {message.refusal}")
    return json.loads(message.content)


def parse_resume_to_profile(resume_text):
    """Turn raw resume text into a validated profile dict (see profile_schema)."""
    from profile_schema import Profile, normalize_profile

    raw = _structured_call(PROFILE_SYSTEM_PROMPT, "Resume text:\n\n" + resume_text, Profile, "profile")
    return normalize_profile(raw)


# ============================================================
# Job Insights: at-a-glance facts + short skills match
# ============================================================

INSIGHTS_SYSTEM_PROMPT = """
You compare a job description with a candidate's profile (JSON) and return
structured facts. Be brief and literal.

at_a_glance: copy facts from the job description only.
- company: the hiring company. If a recruiter or job board posts for a client
  it doesn't name, write "Undisclosed (via <recruiter>)".
- role: the job title.
- location: city / region / country as stated.
- pay: as stated, with currency and period (e.g. "$4.28–$17.15 / hour").
- work_mode: where the work happens, copied in the posting's exact words
  (e.g. "On-site", "Remote", "Hybrid", "Work from office"). Don't reword or
  translate it.
Write "Not specified" for anything the job description doesn't state.

Skills: list EVERY skill, tool, technology and qualification the role asks for
(required and preferred), not just a few. Use short names of 1-4 words, one
skill per item; don't merge different skills into one item. Skip generic soft
skills unless the job stresses them.
- skills_matched: the skills the profile clearly shows. evidence = a short
  exact quote (at most 12 words) copied from the profile that shows it.
- skills_missing: the skills the profile does not show.
Order both lists from most to least important for this role. Put every skill
in exactly one list. Never count a skill as matched without real evidence in
the profile.
"""


def get_job_insights(profile_json, job_description, profile):
    """At-a-glance job facts and a short, evidence-checked skills match.

    `profile_json` is the serialized profile sent to the LLM; `profile` is the
    same profile as a dict, used to verify the evidence the model quotes.
    """
    from job_insights import JobInsights, clean_insights

    user_content = f"## Job Description\n\n{job_description}\n\n## Candidate Profile (JSON)\n\n{profile_json}"
    raw = _structured_call(INSIGHTS_SYSTEM_PROMPT, user_content, JobInsights, "job_insights")
    return clean_insights(raw, profile)


# ============================================================
# Resume Analysis
# ============================================================

RESUME_SYSTEM_PROMPT = """
You are an expert career coach who helps write better resumes
and cover letters.

Analyze the provided resume and job description thoroughly and
determine whether the resume is suitable for the job.

Identify gaps such as:
- Missing skills
- Inadequate experience
- Missing relevant projects
- Missing keywords
- Missing domain knowledge
- Missing qualifications
- Other relevant gaps

Suggest specific changes to the resume, including:
- Skills the user should add if genuinely supported
- Projects that would strengthen the application
- Keywords that should be incorporated
- Relevant information that should be emphasized
- Changes to the About/Intro section, if one exists

Prepare the report in 1-2 pages. don't write extensive reports that goes on for pages. No tables.


Do not suggest falsely claiming skills or experience.

Respond in markdown.
Do not wrap the markdown in a code block.
Respond only with the markdown.
"""


def messages_for_resume(resume_text, job_description):
    """`resume_text` is either raw resume text or the profile JSON from
    profile_schema.profile_for_llm()."""
    user_prompt = f"""
Here is the job I want to apply for and my resume.

Analyze the gaps and suggest specific improvements.

## Job Description

{job_description}

## Resume

{resume_text}
"""

    return [
        {"role": "system", "content": RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def get_resume_improvements_from_text(resume_text, job_description):
    """Analyze already-extracted resume text against a job description."""
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        reasoning_effort="none",
        messages=messages_for_resume(resume_text, job_description),
    )

    return response.choices[0].message.content


def get_resume_improvements(resume_file, job_description):
    """Kept for CLI / script use: reads a PDF path, then analyzes it."""
    resume_text = extract_text_from_pdf(resume_file)
    return get_resume_improvements_from_text(resume_text, job_description)
