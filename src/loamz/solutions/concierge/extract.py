"""Text from PDF and Office files, when the optional readers are installed (`pip install 'loamz-solutions[documents]'`).

    available()            # {".pdf", ".docx", ".xlsx", ".pptx"} minus whatever has no reader installed here
    extract(path)          # the text, or None when the file cannot be read

A missing reader is not an error: the file is counted and listed on the map as "not readable yet". A damaged file yields None.
"""

from __future__ import annotations

import logging
from pathlib import Path

logging.getLogger("pypdf").setLevel(
    logging.ERROR
)  # damaged PDFs are handled; do not print their warnings

MAX_CHARS = 20_000


def pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:60])


def docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    book = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    for sheet in book.worksheets[:5]:
        parts.append(f"Sheet {sheet.title}:")
        for n, row in enumerate(sheet.iter_rows(values_only=True)):
            if n >= 60:
                break
            parts.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(parts)


def pptx(path: Path) -> str:
    from pptx import Presentation

    parts = []
    for n, slide in enumerate(Presentation(str(path)).slides, 1):
        texts = [
            shape.text_frame.text
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        if texts:
            parts.append(f"Slide {n}: " + " / ".join(texts))
    return "\n".join(parts)


READERS = {
    ".pdf": ("pypdf", pdf),
    ".docx": ("docx", docx),
    ".xlsx": ("openpyxl", xlsx),
    ".pptx": ("pptx", pptx),
}


def available() -> set[str]:
    import importlib.util

    return {ext for ext, (module, _) in READERS.items() if importlib.util.find_spec(module)}


def extract(path: Path) -> str | None:
    reader = READERS.get(path.suffix.lower())
    if reader is None or path.suffix.lower() not in available():
        return None
    try:
        text = reader[1](path)
    except Exception:  # noqa: BLE001 - a damaged or password-protected file is skipped, not fatal
        return None
    return text.strip()[:MAX_CHARS] or None
