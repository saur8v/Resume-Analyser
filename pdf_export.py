"""
The downloadable PDF report, in the same "desk" design as the web app:
serif headings, red-pen notes, yellow highlights.

Pages: the role and the analysis, then the marked-up resume (see
resume_markup.py), then the job description as an appendix.

Uses xhtml2pdf (pure Python, installs cleanly on Windows) with the web fonts
embedded from fonts/ so the PDF looks the same everywhere. All text comes from
users, LLMs or scraped pages, so it is escaped, and the renderer may only load
those font files: nothing remote.
"""

import io
import os
from datetime import datetime
from html import escape

from markdown import markdown as md_to_html
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xhtml2pdf import default as pisa_default
from xhtml2pdf import pisa

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
NOT_SPECIFIED = "Not specified"

# CSS family -> (regular file, bold file). Registered with ReportLab directly:
# xhtml2pdf's @font-face copies fonts to temp files that Windows can't reopen.
_FONTS = {
    "rdbody": ("DMSans-Regular.ttf", "DMSans-Bold.ttf"),
    "rdserif": ("Newsreader-Medium.ttf", "Newsreader-SemiBold.ttf"),
    "rdserifitalic": ("Newsreader-MediumItalic.ttf", "Newsreader-MediumItalic.ttf"),
    "rdpen": ("Caveat-SemiBold.ttf", "Caveat-SemiBold.ttf"),
}


def _register_fonts():
    for family, (regular, bold) in _FONTS.items():
        if family in pdfmetrics.getRegisteredFontNames():
            continue
        pdfmetrics.registerFont(TTFont(family, os.path.join(FONT_DIR, regular)))
        pdfmetrics.registerFont(TTFont(f"{family}-bold", os.path.join(FONT_DIR, bold)))
        for italic in (0, 1):
            addMapping(family, 0, italic, family)
            addMapping(family, 1, italic, f"{family}-bold")
        pisa_default.DEFAULT_FONT[family] = family


_register_fonts()


def _css():
    return """
<style>
  @page {
    size: letter portrait;
    margin: 0.7in 0.7in 0.85in 0.7in;
    @frame footer { -pdf-frame-content: footer; bottom: 0.4in; margin-left: 0.7in; margin-right: 0.7in; height: 0.3in; }
  }
  body { font-family: rdbody; font-size: 10pt; line-height: 1.45; color: #2e2a23; }
  #footer { font-size: 8pt; color: #8a8070; }

  .mast td { vertical-align: bottom; }
  .wordmark { font-family: rdserif; font-weight: bold; font-size: 17pt; color: #1d1b17; }
  .desk { font-family: rdserifitalic; color: #b5321f; }
  .mast-right { text-align: right; font-size: 8.5pt; color: #5c5548; }
  .rule { border-top: 1px solid #e6dccb; margin: 8px 0 14px; }

  .kicker { font-size: 8pt; font-weight: bold; letter-spacing: 1.2px; color: #5c5548; margin: 0 0 2px; }
  h1 { font-family: rdserif; font-size: 24pt; line-height: 1.15; color: #1d1b17; margin: 0 0 4px; }
  .meta { font-size: 9pt; color: #5c5548; margin: 0 0 14px; }
  .part { font-family: rdserif; font-weight: bold; font-size: 15pt; color: #1d1b17; margin: 16px 0 6px; }

  .box { border: 1px solid #e6dccb; background-color: #fffdf8; padding: 10px 12px; vertical-align: top; }
  .box-title { font-family: rdserif; font-weight: bold; font-size: 11.5pt; color: #1d1b17; margin: 0 0 6px; }
  .glance td { font-size: 9pt; padding: 1px 0; vertical-align: top; }
  .glance .k { color: #5c5548; width: 32%; }
  .glance .v { font-weight: bold; color: #1d1b17; }
  .glance .v-missing { color: #8a8070; }
  .chip-label { font-size: 7.5pt; font-weight: bold; letter-spacing: 1px; color: #46403a; margin: 0 0 3px; }
  .chip-label-gap { color: #b5321f; margin-top: 7px; }
  .chips { font-size: 9pt; margin: 0; line-height: 1.8; }
  .chip-ok { background-color: #f7e37a; color: #1d1b17; font-weight: bold; }
  .chip-gap { color: #8f2617; font-weight: bold; text-decoration: underline; }

  .md h1 { font-size: 14pt; margin: 12px 0 4px; }
  .md h2 { font-family: rdserif; font-weight: bold; font-size: 13pt; color: #1d1b17; margin: 12px 0 4px; }
  .md h3, .md h4 { font-family: rdserif; font-weight: bold; font-size: 11.5pt; color: #1d1b17; margin: 10px 0 3px; }
  .md p { margin: 4px 0; }
  .md ul, .md ol { margin: 4px 0; padding-left: 16px; }
  .md li { margin: 3px 0; }
  .md strong { background-color: #f7e37a; color: #1d1b17; }
  .md hr { border-top: 1px solid #e6dccb; }

  .legend { font-size: 9pt; color: #46403a; margin: 0 0 12px; }
  .hl { background-color: #f7e37a; color: #1d1b17; }
  .fx { color: #8f2617; text-decoration: underline; }
  .n-good { font-size: 7.5pt; font-weight: bold; color: #46403a; }
  .n-fix { font-size: 7.5pt; font-weight: bold; color: #b5321f; }

  .sheet-name { font-family: rdserif; font-weight: bold; font-size: 20pt; color: #1d1b17; margin: 16px 0 0; text-align: center; }
  .sheet-headline { font-size: 10pt; color: #2e2a23; margin: 2px 0 0; text-align: center; }
  .sheet-contact { font-size: 8.5pt; color: #5c5548; margin: 2px 0 8px; text-align: center; }
  .sec { font-size: 8pt; font-weight: bold; letter-spacing: 1.4px; color: #5c5548; margin: 12px 0 2px; border-bottom: 1px solid #eee6d8; padding-bottom: 2px; -pdf-keep-with-next: true; }
  .entry-table td { vertical-align: top; }
  .entry-cell { padding: 4px 10px 4px 0; }
  .notes-cell { padding: 4px 0 4px 10px; border-left: 1px solid #eee6d8; }
  .e-head { font-weight: bold; font-size: 10pt; color: #1d1b17; margin: 0; }
  .e-dates { font-weight: normal; color: #5c5548; font-size: 8.5pt; }
  .e-sub { font-size: 9pt; color: #46403a; margin: 0; }
  .e-lines { margin: 2px 0 0; padding-left: 13px; }
  .e-lines li { font-size: 9.2pt; margin: 1.5px 0; }
  .e-text { font-size: 9.2pt; margin: 2px 0 0; }
  .note { font-family: rdpen; font-size: 12.5pt; line-height: 1.05; color: #b5321f; margin: 0 0 4px; }
</style>
"""


def _safe_markdown(text):
    # Escape raw HTML from LLM output / scraped pages before rendering markdown.
    return md_to_html((text or "").replace("<", "&lt;"), extensions=["extra"])


def _no_remote_resources(uri, rel):
    """xhtml2pdf link callback: allow only the bundled font files."""
    path = os.path.abspath(uri.replace("file:///", "").replace("file://", ""))
    return path if path.startswith(FONT_DIR) and os.path.isfile(path) else ""


def _date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%B %d, %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return ""


def _known(value):
    return bool(value) and value != NOT_SPECIFIED


def _mast(right):
    return f"""<table class="mast"><tr>
      <td class="wordmark">resume<span class="desk">desk</span></td>
      <td class="mast-right">{escape(right)}</td></tr></table><div class="rule"></div>"""


def _cover(analysis):
    insights = analysis.get("insights") or {}
    glance = insights.get("at_a_glance", {})
    company, _, role = analysis["title"].partition(" - ")
    role = glance.get("role") if _known(glance.get("role")) else (role or analysis["title"])
    kicker = " · ".join(
        v for v in (glance.get("company") if _known(glance.get("company")) else company,
                    glance.get("location"), glance.get("work_mode")) if _known(v)
    )

    side = ""
    if insights:
        # All five fields always appear; missing ones say "Not disclosed".
        rows = "".join(
            f'<tr><td class="k">{label}</td><td class="v">{escape(glance[key])}</td></tr>'
            if _known(glance.get(key)) else
            f'<tr><td class="k">{label}</td><td class="v-missing">Not disclosed</td></tr>'
            for key, label in (("company", "Company"), ("role", "Role"), ("location", "Location"),
                               ("pay", "Pay"), ("work_mode", "Type"))
        )
        matched = "&nbsp; ".join(f'<span class="chip-ok">&nbsp;{escape(s["skill"])}&nbsp;</span>'
                                 for s in insights.get("skills_matched", [])) or "None"
        missing = "&nbsp; ".join(f'<span class="chip-gap">{escape(s)}</span>'
                                 for s in insights.get("skills_missing", [])) or "None"
        side = f"""
        <table><tr>
          <td width="48%" class="box"><p class="box-title">At a glance</p><table class="glance">{rows}</table></td>
          <td width="4%"></td>
          <td width="48%" class="box"><p class="box-title">Key skills for this role</p>
            <p class="chip-label">ON YOUR PROFILE</p><p class="chips">{matched}</p>
            <p class="chip-label chip-label-gap">NOT SHOWN YET</p><p class="chips">{missing}</p></td>
        </tr></table>"""

    return f"""
      {_mast("Resume analysis")}
      <p class="kicker">{escape(kicker.upper())}</p>
      <h1>{escape(role)}</h1>
      <p class="meta">Analysed {escape(_date(analysis["created_at"]))} · profile version {analysis["profile_version"]}</p>
      {side}
      <p class="part">The analysis</p>
      <div class="md">{_safe_markdown(analysis["improvements"])}</div>
    """


def _segments(line):
    out = []
    for seg in line:
        text = escape(seg["text"])
        mark = seg["mark"]
        if not mark:
            out.append(text)
        elif mark["kind"] == "good":
            out.append(f'<span class="hl">{text}</span><sup class="n-good">{mark["n"]}</sup>')
        else:
            out.append(f'<span class="fx">{text}</span><sup class="n-fix">{mark["n"]}</sup>')
    return "".join(out)


def _notes(notes):
    return "".join(
        f'<p class="note">{(str(n["n"]) + ". ") if n["n"] else "– "}{escape(n["text"])}</p>'
        for n in notes
    )


def _marked_resume(analysis):
    markup = analysis["markup"]
    header, counts = markup["header"], markup["counts"]
    contact = " · ".join(header["contact"] + header["links"])

    body = []
    for section in markup["sections"]:
        body.append(f'<p class="sec">{escape(section["title"].upper())}</p>')
        if section["notes"]:
            body.append(f'<table class="entry-table"><tr><td width="66%" class="entry-cell"></td>'
                        f'<td width="34%" class="notes-cell">{_notes(section["notes"])}</td></tr></table>')
        for entry in section["entries"]:
            head = ""
            if entry["heading"]:
                dates = f' <span class="e-dates">· {escape(entry["dates"])}</span>' if entry["dates"] else ""
                head = f'<p class="e-head">{escape(entry["heading"])}{dates}</p>'
            sub = f'<p class="e-sub">{escape(entry["sub"])}</p>' if entry["sub"] else ""
            if entry["style"] == "bullets":
                lines = "<ul class=\"e-lines\">" + "".join(f"<li>{_segments(l)}</li>" for l in entry["lines"]) + "</ul>"
            else:
                lines = "".join(f'<p class="e-text">{_segments(l)}</p>' for l in entry["lines"])
            body.append(
                f'<table class="entry-table"><tr><td width="66%" class="entry-cell">{head}{sub}{lines}</td>'
                f'<td width="34%" class="notes-cell">{_notes(entry["notes"])}</td></tr></table>'
            )

    return f"""
      <pdf:nextpage />
      {_mast("Marked-up resume")}
      <p class="kicker">YOUR RESUME, MARKED UP FOR THIS ROLE</p>
      <h1>What works, and what to fix</h1>
      <p class="legend"><span class="hl">&nbsp;Highlighted&nbsp;</span> already works for this role ({counts["good"]}) &nbsp;·&nbsp;
        <span class="fx">Red pen</span> needs work ({counts["fix"]}). Built from the profile this analysis used.</p>
      <p class="sheet-name">{escape(header["name"] or "Your resume")}</p>
      {f'<p class="sheet-headline">{escape(header["headline"])}</p>' if header["headline"] else ""}
      {f'<p class="sheet-contact">{escape(contact)}</p>' if contact else ""}
      {"".join(body)}
    """


def _appendix(analysis):
    return f"""
      <pdf:nextpage />
      {_mast("Appendix")}
      <p class="kicker">APPENDIX</p>
      <h1>The job description</h1>
      <div class="md">{_safe_markdown(analysis["job_description"])}</div>
    """


def build_report_pdf(analysis) -> bytes:
    """Render a stored analysis (with its "markup") into PDF bytes with real, selectable text."""
    html = f"""<html><head><meta charset="utf-8">{_css()}</head><body>
      <div id="footer">resumedesk · {escape(analysis["title"])} · page <pdf:pagenumber /> of <pdf:pagecount /></div>
      {_cover(analysis)}
      {_marked_resume(analysis) if analysis.get("markup") else ""}
      {_appendix(analysis)}
    </body></html>"""

    buffer = io.BytesIO()
    status = pisa.CreatePDF(html, dest=buffer, encoding="utf-8", link_callback=_no_remote_resources)
    if status.err:
        raise RuntimeError("Failed to render the PDF report.")
    return buffer.getvalue()
