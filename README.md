# Canvas Quiz Export Converter

Convert Canvas LMS QTI quiz exports into readable text and PDF files.

## Prerequisites

- Python 3.6+
- Google Chrome (for automatic PDF generation) — if Chrome is not installed, the script will generate an HTML file you can open in any browser and print to PDF

## Usage

1. In Canvas, go to **Settings > Export Course Content**, select **Quizzes**, and download the `.zip` file.
2. Unzip the downloaded file. You should get a folder (e.g., `s26-ee-060-01-quiz-export`).
3. Run the script, passing the folder path:

```bash
python3 qti_to_text.py <export_folder>
```

For example:

```bash
python3 qti_to_text.py s26-ee-060-01-quiz-export
```

This will create a `<export_folder>-output/` directory containing:

- **Individual `.txt` files** — one per quiz (e.g., `Quiz-1.txt`, `Quiz-2.txt`)
- **`all-quizzes.txt`** — all quizzes combined in plain text
- **`all-quizzes.html`** — all quizzes combined with formatted math (KaTeX)
- **`all-quizzes.pdf`** — all quizzes combined as a PDF

### Options

| Flag | Description |
|------|-------------|
| `--no-pdf` | Skip PDF generation (still produces `.txt` and `.html`) |

## Features

- Parses QTI XML format used by Canvas quiz exports
- Renders LaTeX math equations via KaTeX in HTML/PDF output
- Marks correct answers with `*` (text) or a green badge (PDF)
- Includes question feedback when available
- Supports multiple quiz types (multiple choice, true/false, etc.)
