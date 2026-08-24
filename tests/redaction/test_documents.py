"""Layer 3 must read text, surface doc metadata, and never leak a filename."""

import pytest

from scripts.redaction.documents import extract, output_name


def test_markdown_is_read(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("Maya Lin wrote this.", encoding="utf-8")
    result = extract(path)
    assert result.text == "Maya Lin wrote this."
    assert result.author == ""
    assert result.error == ""


def test_plain_text_is_read(tmp_path):
    path = tmp_path / "paper.txt"
    path.write_text("Body text.", encoding="utf-8")
    assert extract(path).text == "Body text."


def test_pdf_is_refused(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4")
    result = extract(path)
    assert result.text == ""
    assert "convert" in result.error.lower()


def test_unknown_suffix_is_refused(tmp_path):
    path = tmp_path / "paper.pages"
    path.write_bytes(b"x")
    assert extract(path).error != ""


def test_unreadable_file_reports_an_error(tmp_path):
    path = tmp_path / "paper.md"
    path.write_bytes(b"\xff\xfe\x00bad")
    result = extract(path)
    assert result.error != ""


def test_output_is_named_by_token():
    """A filename must never carry a real name."""
    assert output_name("STUDENT_07") == "STUDENT_07.md"
    assert output_name("STUDENT_07", ".txt") == "STUDENT_07.txt"


def test_a_docx_author_is_returned_not_dropped(tmp_path):
    """A Word file carries the author in metadata even when the body text is clean."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("Body text.")
    document.core_properties.author = "Maya Lin"
    document.save(str(path))

    result = extract(path)

    assert result.text == "Body text."
    assert result.author == "Maya Lin"


def test_a_docx_with_no_author_returns_an_empty_author(tmp_path):
    """python-docx's blank template pre-fills author as "python-docx"; clear it
    explicitly to simulate a document that truly carries no author."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("Body text.")
    document.core_properties.author = ""
    document.save(str(path))

    result = extract(path)

    assert result.author == ""
    assert result.error == ""
