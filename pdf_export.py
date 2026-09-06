"""
Builds a real, text-based (not rasterized) PDF summary from the job
description and analysis markdown, with the job description on its own
page(s) followed by the analysis on the next.

Uses xhtml2pdf (pure Python, no system dependencies like Cairo/GTK — unlike
WeasyPrint — so it installs cleanly on Windows) to render HTML+CSS straight
to PDF. Headings use Times-Roman and body text uses Helvetica (reportlab's
built-in base-14 fonts), so no font files need to be bundled or downloaded.
"""

import io

from markdown import markdown as md_to_html
from xhtml2pdf import pisa

PDF_CSS = """
<style>
  @page { size: letter; margin: 0.75in; }
  body { font-family: Helvetica; font-size: 10pt; color: #1E2430; line-height: 1.5; }
  h1 { font-family: Times-Roman; font-size: 18pt; color: #2C3E66; margin: 0 0 12px; }
  h2, h3 { font-family: Times-Roman; color: #2C3E66; margin-top: 14px; margin-bottom: 6px; }
  p { margin: 4px 0; }
  strong { color: #1E2430; }
  .doc-tab {
    font-size: 8pt;
    color: #2C3E66;
    border: 1px solid #2C3E66;
    padding: 2px 6px;
  }
  .doc-tab-flag { color: #C98A2C; border-color: #C98A2C; }
  .doc-section { page-break-after: always; }
  .doc-section-last { page-break-after: avoid; }
  ul { margin: 6px 0; padding-left: 16px; }
  li { margin: 4px 0; }
  hr { border: none; border-top: 1px solid #DAD6CC; margin: 14px 0; }
</style>
"""


def _section_html(tab_label, tab_class, heading, body_markdown, is_last=False):
    body_html = md_to_html(body_markdown, extensions=["extra"])
    section_class = "doc-section doc-section-last" if is_last else "doc-section"

    return f"""
    <div class="{section_class}">
      <span class="doc-tab {tab_class}">{tab_label}</span>
      <h1>{heading}</h1>
      {body_html}
    </div>
    """


def build_summary_pdf(job_description_md: str, improvements_md: str) -> bytes:
    """Render the job description + analysis markdown into a two-part PDF.

    Returns raw PDF bytes with real, selectable text (not an image snapshot).
    """
    jd_section = _section_html(
        "JOB POSTING", "", "Extracted description", job_description_md
    )
    analysis_section = _section_html(
        "ANALYSIS",
        "doc-tab-flag",
        "Gaps &amp; suggested improvements",
        improvements_md,
        is_last=True,
    )

    full_html = f"""
    <html>
    <head>{PDF_CSS}</head>
    <body>
      {jd_section}
      {analysis_section}
    </body>
    </html>
    """

    buffer = io.BytesIO()
    status = pisa.CreatePDF(full_html, dest=buffer)

    if status.err:
        raise RuntimeError("Failed to render PDF summary.")

    return buffer.getvalue()
