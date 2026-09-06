# Resume Desk

A dashboard that tells you exactly why your resume isn't landing an interview.
Paste a job posting, upload your resume, and get a gap analysis with concrete,
specific suggestions — not generic "add more keywords" advice.

Built on top of a Selenium + Gemini backend that scrapes the job description,
parses your resume, and compares the two.

## Screenshots
Job description scraped from a URL and rendered.
<img width="1735" height="975" alt="extraction" src="https://github.com/user-attachments/assets/f06a71ee-90c4-4b79-98b9-01413e0de7db" />

Gap analysis and improvement suggestions after comparing against a resume.
<img width="1801" height="972" alt="analysis" src="https://github.com/user-attachments/assets/44c921a7-0863-4b3c-ac20-4b93e2912c46" />

## Features

- **Scrapes a live job posting** via headless Selenium + BeautifulSoup, then
  extracts a structured job description with Gemini.
- **Parses your resume PDF** in memory (no temp files) and compares it
  against the job description, identifying missing skills, keywords, and
  experience gaps — without ever suggesting you claim something you don't have.
- **Paste-JD fallback** — skip the scraper entirely and paste a job
  description directly, for sites that block automated browsers.
- **Download a real PDF summary** — a genuinely selectable-text PDF (not a
  screenshot), with the job description on page 1 and the analysis starting
  fresh on page 2.

## Project structure

```
resume-dashboard/
├── main.py              # FastAPI app: /api/job-description, /api/analyze, /api/pdf-summary, serves static/
├── resume_analyzer.py   # Scraping + Gemini logic, refactored into importable functions
├── pdf_export.py         # Builds the downloadable PDF summary (real text, not an image)
├── requirements.txt
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Setup

1. **Install Chrome + chromedriver** (needed for Selenium).
   Selenium Manager (bundled with recent `selenium` versions) will usually
   download the matching driver automatically the first time it runs.

2. **Create a virtual environment and install dependencies:**
   ```bash
   uv venv
   .venv\Scripts\Activate.ps1   # PowerShell; use .venv/bin/activate on macOS/Linux
   uv pip install -r requirements.txt
   ```

3. **Set your API key.** This repo intentionally does **not** ship a
   `.env.example` file, to keep it obvious that this is a real secret you
   have to create yourself rather than a template you might forget to edit.
   Create a file named `.env` in the project root with exactly one line:
   ```
   GOOGLE_API_KEY=your-gemini-api-key-here
   ```
   Get a key from [Google AI Studio](https://aistudio.google.com/apikey) if
   you don't already have one. This file is already listed in `.gitignore`
   and will never be committed.

4. **Run the server:**
   ```bash
   uvicorn main:app --reload
   ```

5. Open **http://127.0.0.1:8000** in your browser.

## How it works

- **Fetch job description** — sends the URL to `/api/job-description`, which
  runs a Selenium scraper + Gemini extraction, and returns markdown that's
  rendered in the "Job Posting" panel.
- **Analyze against job description** — uploads the PDF as `multipart/form-data`
  to `/api/analyze`, which extracts resume text in memory, runs a Gemini
  comparison prompt, and renders the markdown result in the "Analysis" panel.
- You can also skip the scraper entirely and paste a job description directly
  into the textarea on the left — the "Analyze" button accepts either source.
- **Download PDF summary** — appears once analysis is complete. Sends the
  raw markdown for both sections to `/api/pdf-summary`, which renders them
  into a real, selectable-text PDF using `xhtml2pdf`, with the job
  description on page 1 and the analysis starting fresh on page 2.

## Known limitation: LinkedIn scraping

LinkedIn (and some other job boards) actively block headless/automated
browsers with logins, captchas, or rate limits. If a URL fails to scrape:
- Try a company's own careers page instead, which is usually more permissive.
- Or just paste the job description text directly into the textarea —
  the analysis step works the same either way.

## Notes

- `extract_text_from_pdf_bytes()` reads the uploaded resume straight from
  memory rather than needing it saved to disk first.
  `extract_text_from_pdf()` (path-based) is still available if you want to
  run parts of this as a standalone script.
- `pdf_export.py` uses reportlab's built-in Times-Roman/Helvetica fonts
  (via `xhtml2pdf`) rather than the Fraunces/IBM Plex fonts used on the web
  page — those are web fonts and would need to be bundled as font files for
  a PDF renderer to use them. This keeps the PDF dependency-free, at the
  cost of matching the on-screen typography exactly.
- The static frontend is served with `Cache-Control: no-cache` so that CSS/JS
  changes always show up on a normal refresh during development.
