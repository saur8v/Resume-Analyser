"""
Core logic for the resume analyser & improver.

Refactored from the original script into an importable module so it can be
called from a FastAPI backend. Behavior is unchanged: it still scrapes a
job posting URL with Selenium, extracts resume text with PyPDF2, and uses
Gemini (via the OpenAI-compatible API) to extract the job description and
compare it against the resume.
"""

import os

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

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = "gemini-3.1-flash-lite"

API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "GOOGLE_API_KEY is not set. Add it to your .env file."
    )
if API_KEY.strip() != API_KEY:
    raise RuntimeError(
        "GOOGLE_API_KEY has leading/trailing whitespace — check your .env file."
    )

client = OpenAI(
    base_url=GEMINI_BASE_URL,
    api_key=API_KEY,
)


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


def get_job_description(url, wait_selector="body"):
    """Scrape a job posting URL and return the extracted job description (markdown)."""
    website_text = fetch_website_contents(url, wait_selector=wait_selector)

    response = client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=messages_for_job_description(website_text),
    )

    return response.choices[0].message.content


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

Do not suggest falsely claiming skills or experience.

Respond in markdown.
Do not wrap the markdown in a code block.
Respond only with the markdown.
"""


def messages_for_resume(resume_text, job_description):
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
        model=GEMINI_MODEL,
        messages=messages_for_resume(resume_text, job_description),
    )

    return response.choices[0].message.content


def get_resume_improvements(resume_file, job_description):
    """Kept for CLI / script use: reads a PDF path, then analyzes it."""
    resume_text = extract_text_from_pdf(resume_file)
    return get_resume_improvements_from_text(resume_text, job_description)
