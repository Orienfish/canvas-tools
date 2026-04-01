#!/usr/bin/env python3
"""Convert Canvas QTI quiz export to readable text and PDF files.

Usage:
    python3 qti_to_text.py <export_folder> [--no-pdf]

Processes all quizzes in the given Canvas quiz export folder,
generates individual .txt files, a combined .txt file, and a
combined PDF (via HTML with KaTeX math rendering).

Use --no-pdf to skip PDF generation.
"""

import os
import re
import sys
import glob
import shutil
import subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from html import unescape, escape

# QTI namespaces
NS_QTI = "{http://www.imsglobal.org/xsd/ims_qtiasiv1p2}"
NS_META = "{http://canvas.instructure.com/xsd/cccv1p0}"

# Sentinel used to mark LaTeX in plain-text output vs HTML output
LATEX_DELIM = "\x00LATEX:"


class HTMLToText(HTMLParser):
    """Strip HTML tags, extract LaTeX from images/spans, decode entities."""

    def __init__(self, keep_latex_markers=False):
        super().__init__()
        self._parts = []
        self._keep_latex_markers = keep_latex_markers

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "img":
            eq = attrs_dict.get("data-equation-content")
            if not eq:
                alt = attrs_dict.get("alt", "")
                if alt.startswith("LaTeX:"):
                    eq = alt[len("LaTeX:"):].strip()
            if eq:
                if self._keep_latex_markers:
                    self._parts.append(f"{LATEX_DELIM}{eq}\x00")
                else:
                    self._parts.append(eq)
                return
            alt = attrs_dict.get("alt", "")
            if alt:
                self._parts.append(alt)
        if tag == "span":
            math = attrs_dict.get("data-math")
            if math:
                if self._keep_latex_markers:
                    self._parts.append(f"{LATEX_DELIM}{math}\x00")
                else:
                    self._parts.append(math)

    def handle_data(self, data):
        self._parts.append(data)

    def handle_entityref(self, name):
        self._parts.append(unescape(f"&{name};"))

    def handle_charref(self, name):
        self._parts.append(unescape(f"&#{name};"))

    def get_text(self):
        return "".join(self._parts).strip()


def html_to_text(html_str, keep_latex_markers=False):
    """Convert HTML string to plain text."""
    if not html_str:
        return ""
    parser = HTMLToText(keep_latex_markers=keep_latex_markers)
    parser.feed(unescape(html_str))
    text = parser.get_text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[\u200b\u200c\u200d\u00a0\ufeff]", " ", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def strip_latex_markers(text):
    """Remove LATEX_DELIM markers, leaving just the raw LaTeX strings."""
    return text.replace(LATEX_DELIM, "").replace("\x00", "")


def text_to_html_with_katex(text):
    """Convert text with LATEX_DELIM markers into HTML with KaTeX spans."""
    parts = re.split(r"\x00LATEX:(.*?)\x00", text)
    html_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            html_parts.append(escape(part))
        else:
            html_parts.append(f'<span class="math">\\({part}\\)</span>')
    return "".join(html_parts)


def get_metadata_field(item_el, field_name):
    """Get a metadata field value from a QTI item element."""
    for field in item_el.iter(f"{NS_QTI}qtimetadatafield"):
        label = field.find(f"{NS_QTI}fieldlabel")
        entry = field.find(f"{NS_QTI}fieldentry")
        if label is not None and label.text == field_name and entry is not None:
            return entry.text
    return None


def find_correct_answer_id(item_el):
    """Find the answer ID that gives SCORE=100."""
    for respcond in item_el.iter(f"{NS_QTI}respcondition"):
        setvar = respcond.find(f"{NS_QTI}setvar")
        if setvar is not None and setvar.text and setvar.text.strip() == "100":
            varequal = respcond.find(f".//{NS_QTI}varequal")
            if varequal is not None and varequal.text:
                return varequal.text.strip()
    return None


def parse_quiz(qti_xml_path, meta_xml_path):
    """Parse a single quiz from its QTI XML and metadata XML files."""
    meta_tree = ET.parse(meta_xml_path)
    meta_root = meta_tree.getroot()
    title_el = meta_root.find(f"{NS_META}title")
    quiz_title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled Quiz"

    points_el = meta_root.find(f"{NS_META}points_possible")
    total_points = points_el.text if points_el is not None else "?"

    time_el = meta_root.find(f"{NS_META}time_limit")
    time_limit = time_el.text if time_el is not None and time_el.text else None

    tree = ET.parse(qti_xml_path)
    root = tree.getroot()

    questions = []
    for item in root.iter(f"{NS_QTI}item"):
        q_title = item.get("title", "Question")
        q_type = get_metadata_field(item, "question_type") or "unknown"
        q_points = get_metadata_field(item, "points_possible") or "?"

        mat = item.find(f".//{NS_QTI}presentation/{NS_QTI}material/{NS_QTI}mattext")
        raw_html = mat.text if mat is not None else ""

        # Plain text version (no markers)
        q_text = html_to_text(raw_html, keep_latex_markers=False)
        # Rich version (with LaTeX markers for HTML output)
        q_text_rich = html_to_text(raw_html, keep_latex_markers=True)

        correct_id = find_correct_answer_id(item)
        choices = []
        for resp_label in item.iter(f"{NS_QTI}response_label"):
            choice_id = resp_label.get("ident", "")
            choice_mat = resp_label.find(f"{NS_QTI}material/{NS_QTI}mattext")
            raw = choice_mat.text if choice_mat is not None else ""
            choice_text = html_to_text(raw, keep_latex_markers=False)
            choice_text_rich = html_to_text(raw, keep_latex_markers=True)
            is_correct = (choice_id == correct_id)
            choices.append((choice_text, choice_text_rich, is_correct))

        feedbacks = {}
        feedbacks_rich = {}
        for fb in item.iter(f"{NS_QTI}itemfeedback"):
            fb_id = fb.get("ident", "")
            fb_mat = fb.find(f".//{NS_QTI}mattext")
            if fb_mat is not None and fb_mat.text:
                feedbacks[fb_id] = html_to_text(fb_mat.text, keep_latex_markers=False)
                feedbacks_rich[fb_id] = html_to_text(fb_mat.text, keep_latex_markers=True)

        general_fb = feedbacks.get("general_fb", "")
        general_fb_rich = feedbacks_rich.get("general_fb", "")

        questions.append({
            "title": q_title,
            "type": q_type,
            "points": q_points,
            "text": q_text,
            "text_rich": q_text_rich,
            "choices": choices,
            "feedback": general_fb,
            "feedback_rich": general_fb_rich,
            "feedbacks": feedbacks,
            "feedbacks_rich": feedbacks_rich,
        })

    return {
        "title": quiz_title,
        "total_points": total_points,
        "time_limit": time_limit,
        "questions": questions,
    }


def format_quiz_text(quiz):
    """Format a parsed quiz as readable plain text."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  {quiz['title']}")
    lines.append(f"  Total Points: {quiz['total_points']}")
    if quiz["time_limit"]:
        lines.append(f"  Time Limit: {quiz['time_limit']} minutes")
    lines.append("=" * 70)
    lines.append("")

    for i, q in enumerate(quiz["questions"], 1):
        lines.append(f"Question {i} ({q['points']} pts) [{q['type'].replace('_', ' ')}]")
        lines.append("-" * 50)
        lines.append(q["text"])
        lines.append("")

        for j, (choice_text, _, is_correct) in enumerate(q["choices"]):
            letter = chr(ord("A") + j)
            marker = " *" if is_correct else ""
            lines.append(f"  {letter}) {choice_text}{marker}")

        lines.append("")

        if q["feedback"]:
            lines.append(f"  Feedback: {q['feedback']}")
        else:
            for fb_id, fb_text in q["feedbacks"].items():
                if fb_text:
                    lines.append(f"  Feedback ({fb_id}): {fb_text}")

        lines.append("")
        lines.append("")

    lines.append("* = correct answer")
    lines.append("")
    return "\n".join(lines)


def generate_html(quizzes, output_path):
    """Generate an HTML file with KaTeX-rendered math."""
    parts = []
    parts.append("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Quiz Export</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js"
        onload="renderMathInElement(document.body, {delimiters:[{left:'\\\\(',right:'\\\\)',display:false},{left:'\\\\[',right:'\\\\]',display:true}]});"></script>
<style>
  body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
         max-width: 800px; margin: 40px auto; padding: 0 20px;
         line-height: 1.6; color: #333; }
  h1 { border-bottom: 2px solid #2c3e50; padding-bottom: 8px; color: #2c3e50; }
  .quiz-info { color: #666; margin-bottom: 20px; }
  .question { background: #f8f9fa; border-left: 4px solid #3498db;
              padding: 15px 20px; margin: 20px 0; border-radius: 0 8px 8px 0; }
  .question-header { font-weight: bold; color: #2c3e50; margin-bottom: 8px; }
  .choices { margin: 10px 0 10px 10px; }
  .choice { padding: 4px 0; }
  .choice.correct { color: #27ae60; font-weight: bold; }
  .choice .marker { background: #27ae60; color: white; font-size: 0.75em;
                    padding: 1px 6px; border-radius: 3px; margin-left: 6px; }
  .feedback { margin-top: 10px; padding: 10px 15px; background: #eaf4fe;
              border-radius: 6px; font-size: 0.92em; color: #555; }
  .feedback strong { color: #2c3e50; }
  .feedback-label { font-weight: bold; color: #2980b9; }
  hr { border: none; border-top: 1px solid #ddd; margin: 30px 0; }
  @media print {
    .question { break-inside: avoid; }
    body { margin: 20px; }
  }
</style>
</head>
<body>
""")

    for quiz in quizzes:
        parts.append(f"<h1>{escape(quiz['title'])}</h1>")
        info = f"Total Points: {quiz['total_points']}"
        if quiz["time_limit"]:
            info += f" &nbsp;|&nbsp; Time Limit: {quiz['time_limit']} min"
        parts.append(f'<div class="quiz-info">{info}</div>')

        for i, q in enumerate(quiz["questions"], 1):
            q_type_label = q["type"].replace("_", " ").title()
            parts.append('<div class="question">')
            parts.append(f'<div class="question-header">Question {i} ({q["points"]} pts) &mdash; {q_type_label}</div>')
            parts.append(f'<div class="question-text">{text_to_html_with_katex(q["text_rich"])}</div>')

            parts.append('<div class="choices">')
            for j, (_, choice_rich, is_correct) in enumerate(q["choices"]):
                letter = chr(ord("A") + j)
                cls = "choice correct" if is_correct else "choice"
                marker = '<span class="marker">correct</span>' if is_correct else ""
                parts.append(f'<div class="{cls}">{letter}) {text_to_html_with_katex(choice_rich)}{marker}</div>')
            parts.append("</div>")

            # Feedback
            if q["feedback_rich"]:
                parts.append(f'<div class="feedback">{text_to_html_with_katex(q["feedback_rich"])}</div>')
            else:
                for fb_id, fb_rich in q["feedbacks_rich"].items():
                    if fb_rich:
                        parts.append(f'<div class="feedback"><span class="feedback-label">{escape(fb_id)}:</span> {text_to_html_with_katex(fb_rich)}</div>')

            parts.append("</div>")

        parts.append("<hr>")

    parts.append("</body></html>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def html_to_pdf(html_path, pdf_path):
    """Convert HTML to PDF using available system tools."""
    # Try Chrome/Chromium headless
    for chrome in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ]:
        if chrome and os.path.isfile(chrome):
            try:
                subprocess.run(
                    [chrome, "--headless", "--disable-gpu",
                     "--print-to-pdf=" + pdf_path,
                     "--no-pdf-header-footer",
                     "file://" + os.path.abspath(html_path)],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=30,
                )
                return True
            except (subprocess.CalledProcessError, FileNotFoundError,
                    subprocess.TimeoutExpired, OSError):
                continue

    # Try wkhtmltopdf
    wk = shutil.which("wkhtmltopdf")
    if wk:
        try:
            subprocess.run(
                [wk, "--enable-javascript", "--javascript-delay", "2000",
                 html_path, pdf_path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired, OSError):
            pass

    return False


def main():
    make_pdf = True
    export_dir = None

    for arg in sys.argv[1:]:
        if arg in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        elif arg == "--no-pdf":
            make_pdf = False
        elif export_dir is None:
            export_dir = arg
        else:
            print(f"Unknown argument: {arg}")
            sys.exit(1)

    if export_dir is None:
        print("Usage: python3 qti_to_text.py <export_folder> [--no-pdf]")
        print("  <export_folder>  Path to the unzipped Canvas quiz export folder")
        sys.exit(1)

    export_dir = os.path.abspath(export_dir)
    if not os.path.isdir(export_dir):
        print(f"Error: '{export_dir}' is not a directory")
        sys.exit(1)

    folder_name = os.path.basename(export_dir)

    # Find all quiz directories
    quiz_dirs = []
    for entry in sorted(os.listdir(export_dir)):
        full = os.path.join(export_dir, entry)
        if os.path.isdir(full):
            meta = os.path.join(full, "assessment_meta.xml")
            qti_files = glob.glob(os.path.join(full, "g*.xml"))
            qti_files = [f for f in qti_files if "assessment_meta" not in f]
            if os.path.isfile(meta) and qti_files:
                quiz_dirs.append((qti_files[0], meta))

    if not quiz_dirs:
        print(f"No QTI quizzes found in {export_dir}")
        sys.exit(1)

    # Create output directory alongside the export folder
    output_dir = os.path.join(os.path.dirname(export_dir), f"{folder_name}-output")
    os.makedirs(output_dir, exist_ok=True)

    quizzes = []
    all_text = []

    for qti_path, meta_path in quiz_dirs:
        quiz = parse_quiz(qti_path, meta_path)
        quizzes.append(quiz)
        text = format_quiz_text(quiz)
        all_text.append(text)

        txt_name = f"{quiz['title'].strip().replace(' ', '-')}.txt"
        txt_path = os.path.join(output_dir, txt_name)
        with open(txt_path, "w") as f:
            f.write(text)
        print(f"Written: {txt_path}")

    # Write combined text file
    combined_path = os.path.join(output_dir, "all-quizzes.txt")
    with open(combined_path, "w") as f:
        f.write("\n\n".join(all_text))
    print(f"Written: {combined_path}")

    # Generate PDF via HTML + KaTeX (default behavior)
    if make_pdf:
        html_path = os.path.join(output_dir, "all-quizzes.html")
        pdf_path = os.path.join(output_dir, "all-quizzes.pdf")

        generate_html(quizzes, html_path)
        print(f"Written: {html_path}")

        if html_to_pdf(html_path, pdf_path):
            print(f"Written: {pdf_path}")
        else:
            print(f"\nCould not auto-convert to PDF (no Chrome or wkhtmltopdf found).")
            print(f"Open {html_path} in your browser and print/save as PDF.")
            print(f"  Tip: install Chrome for automatic conversion.")

    print(f"\nAll output saved to: {output_dir}")


if __name__ == "__main__":
    main()
