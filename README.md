# Resume Desk

A dashboard for the resume analyser & improver: fetch a job posting, upload
your resume, and get a gap analysis with concrete suggestions.

## Project structure

```
resume-dashboard/
├── main.py              # FastAPI app: /api/job-description, /api/analyze, serves static/
├── resume_analyzer.py   # Your original logic, refactored into importable functions
├── requirements.txt
├── .env.example
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Setup

1. **Install Chrome + chromedriver** (needed for Selenium — same as your original script).
   Selenium Manager (bundled with recent `selenium` versions) will usually
   download the matching driver automatically the first time it runs.

2. **Create a virtual environment and install dependencies:**
   ```bash
   uv venv
   .venv\Scripts\Activate.ps1   # PowerShell; use .venv/bin/activate on macOS/Linux
   uv pip install -r requirements.txt
   ```

3. **Set your API key:**
   ```bash
   cp .env.example .env
   # then edit .env and paste your GOOGLE_API_KEY
   ```

4. **Run the server:**
   ```bash
   uvicorn main:app --reload
   ```

5. Open **http://127.0.0.1:8000**, create an account, and set up your profile.

## How it works

- **Fetch job description** — sends the URL to `/api/job-description`, which
  runs your Selenium scraper + Gemini extraction exactly as before, and
  returns markdown that's rendered in the "Job Posting" panel.
- **Analyze against job description** — uploads the PDF as `multipart/form-data`
  to `/api/analyze`, which extracts resume text in memory (no temp files),
  runs the same Gemini comparison prompt, and renders the markdown result
  in the "Analysis" panel.
- You can also skip the scraper entirely and paste a job description directly
  into the textarea on the left — the "Analyze" button accepts either source.

## Known limitation: LinkedIn scraping

LinkedIn (and some other job boards) actively block headless/automated
browsers with logins, captchas, or rate limits. If a URL fails to scrape:
- Try a company's own careers page instead, which is usually more permissive.
- Or just paste the job description text directly into the textarea —
  the analysis step works the same either way.

## Notes on this refactor

- `resume_analyzer.py` is your original script's logic, split into functions
  that take/return data (bytes, strings) instead of file paths and `print()`,
  so it can be called from a web request.
- `extract_text_from_pdf_bytes()` is new — it lets the API read the uploaded
  file straight from memory rather than needing it saved to disk first.
  `extract_text_from_pdf()` (the original, path-based version) is still there
  if you want to keep using this as a CLI script too.
- Nothing about your prompts, model choice, or Selenium config was changed.
