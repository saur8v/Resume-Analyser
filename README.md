# Resume Desk

A dashboard for the resume analyser & improver. Create an account, save your
professional profile once (auto-filled from your resume), then paste any job
posting URL to get a gap analysis with concrete suggestions. Every analysis is
saved to your history.

## Project structure

```
Resume_Analyser/
├── main.py              # FastAPI app: auth, profile, job description, analyses; serves static/
├── auth.py              # Email + password auth (scrypt hashes, HttpOnly session cookie)
├── db.py                # SQLite storage: users, sessions, profiles, analysis history
├── profile_schema.py    # Pydantic schema for the structured profile JSON
├── profile_merge.py     # Merges a re-uploaded resume into the existing profile
├── tests/               # python -m unittest discover tests
├── resume_analyzer.py   # Scraping, resume -> JSON parsing, LLM analysis
├── pdf_export.py        # PDF report export
├── requirements.txt
├── data/                # Created on first run; holds resume_desk.db (git-ignored)
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Setup

1. **Install Chrome + chromedriver** (needed for Selenium). Selenium Manager
   usually downloads the matching driver automatically the first time it runs.

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API key** in `.env`:
   ```
   OPENAI_API_KEY=sk-...
   ```
   Optional: `RESUME_DESK_DB` to change the database path, and `COOKIE_SECURE=1`
   when serving over HTTPS.

4. **Run the server:**
   ```bash
   uvicorn main:app --reload
   ```

5. Open **http://127.0.0.1:8000**, create an account, and set up your profile.

## How it works

- **Profile.** Uploading a resume PDF extracts its text and asks the
  extraction model to convert it into the structured JSON defined in
  `profile_schema.py` (strict structured output). You then review and edit it
  in the profile form, and can add *additional context* that isn't on your
  resume. The "View profile JSON" panel shows exactly what is sent to the LLM.
- **Re-uploading a resume merges, never replaces.** The new resume is parsed
  on its own, then merged into your profile in code (`profile_merge.py`):
  fields you edited by hand are kept, fields that came from the last resume
  are updated, new entries are added, entries missing from the new resume are
  kept, and entries you deleted aren't brought back. To tell hand edits from
  resume data, the app stores what it parsed from your last resume. The upload
  message lists what changed, and **Undo last upload** restores the previous
  profile and PDF (one level, and only until you edit the profile again).
- **Reliable updates.** Every write (form save or resume upload) is validated
  against the schema, stored in a single SQLite transaction, and versioned.
  Saving with an out-of-date version (e.g. from a second tab) is rejected with
  a 409 instead of silently overwriting newer data.
- **Analysis.** You paste a job posting URL (or the description text). It is
  scraped and structured, then compared against your saved profile JSON.
- **History.** Each analysis stores the job description, the report, and a
  snapshot of the profile used, so old reports stay accurate after you edit
  your profile.
- **Marked-up resume.** Each report has a "Marked-up resume" tab: the profile
  snapshot laid out as a resume, with yellow highlights for what already works
  for the role and red-pen notes for what to fix. It uses no LLM call: the
  skills match supplies verified quotes, and simple rules in
  `resume_markup.py` catch weak openers, missing numbers, first person, long
  bullets, missing links and more.
- **PDF report.** The download matches the web design (fonts bundled in
  `fonts/`, OFL-licensed) and contains the analysis, the marked-up resume and
  the job description.

## Known limitation: LinkedIn scraping

LinkedIn (and some other job boards) actively block headless/automated
browsers with logins, captchas, or rate limits. If a URL fails to scrape, try
the company's own careers page, or paste the job description text directly.
