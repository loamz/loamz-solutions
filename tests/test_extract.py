"""PDF and Office files are read when the optional readers are present, and counted as unreadable when they are damaged."""

from pathlib import Path

from loamz.solutions.concierge import DataConcierge, DataIndex, FolderConnector
from loamz.solutions.concierge.extract import available, extract


def pdf_bytes(text: str) -> bytes:
    """A minimal valid one-page PDF whose page shows `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return out


def make_files(root: Path) -> None:
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation

    root.mkdir(parents=True, exist_ok=True)
    (root / "agreement.pdf").write_bytes(pdf_bytes("The indemnity cap is two million dollars"))
    document = Document()
    document.add_paragraph("Board minutes: approve the hiring plan.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Owner", "Dana"
    document.save(str(root / "minutes.docx"))
    book = Workbook()
    book.active.append(["month", "revenue"])
    book.active.append(["Sep", 18250])
    book.save(str(root / "revenue.xlsx"))
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Q3 review"
    slide.placeholders[1].text = "Performance driven by two positions"
    deck.save(str(root / "q3.pptx"))
    (root / "broken.pdf").write_bytes(b"not really a pdf")


def test_all_four_formats_are_available_in_the_dev_environment():
    assert available() == {".pdf", ".docx", ".xlsx", ".pptx"}


def test_each_format_yields_its_text(tmp_path):
    make_files(tmp_path)
    assert "indemnity cap is two million dollars" in extract(tmp_path / "agreement.pdf")
    docx = extract(tmp_path / "minutes.docx")
    assert "approve the hiring plan" in docx and "Owner | Dana" in docx
    assert "Sep | 18250" in extract(tmp_path / "revenue.xlsx")
    assert "Q3 review" in extract(tmp_path / "q3.pptx") and "two positions" in extract(
        tmp_path / "q3.pptx"
    )
    assert extract(tmp_path / "broken.pdf") is None  # damaged: skipped, not fatal
    assert extract(tmp_path / "notes.txt") is None  # not one of these formats


def test_the_concierge_finds_what_is_inside_them_and_still_counts_the_broken_one(tmp_path):
    make_files(tmp_path / "docs")
    c = DataConcierge(DataIndex(), FolderConnector([tmp_path / "docs"]))
    report = c.scan()
    assert report.files_seen == 4 and report.unreadable == {".pdf": 1}
    assert {h["title"] for h in c.search("indemnity")} == {"agreement.pdf"}
    assert {h["title"] for h in c.search("revenue Sep")} == {"revenue.xlsx"}
    assert c.search("hiring plan")[0]["title"] == "minutes.docx"
    assert "1 .pdf files" in c.inventory()
