"""Layer 3 must read text, surface doc metadata, and never leak a filename."""

import pytest

from scripts.redaction.documents import extract, output_name


def test_markdown_is_read(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("Maya Lin wrote this.", encoding="utf-8")
    result = extract(path)
    assert result.text == "Maya Lin wrote this."
    assert result.metadata == ()
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
    assert "Maya Lin" in result.metadata


def test_a_docx_with_no_author_returns_an_empty_author(tmp_path):
    """python-docx's blank template pre-fills author and comments; clear both
    explicitly to simulate a document that truly carries no identity metadata."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("Body text.")
    document.core_properties.author = ""
    document.core_properties.comments = ""
    document.save(str(path))

    result = extract(path)

    assert result.metadata == ()
    assert result.error == ""


def test_docx_table_content_is_extracted(tmp_path):
    """A table is body content. Dropping it grades an incomplete essay."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("Body paragraph one.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Policy"
    table.cell(0, 1).text = "Effect"
    table.cell(1, 0).text = "DMCA 1201"
    table.cell(1, 1).text = "Chills repair"
    document.save(str(path))

    result = extract(path)

    assert "Body paragraph one." in result.text
    assert "Policy" in result.text
    assert "Effect" in result.text
    assert "DMCA 1201" in result.text
    assert "Chills repair" in result.text
    assert result.error == ""


def test_a_table_only_docx_is_not_mistaken_for_empty(tmp_path):
    """A submission that is entirely a table must not read as a blank one."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Policy"
    table.cell(0, 1).text = "Effect"
    document.save(str(path))

    result = extract(path)

    assert "Policy" in result.text
    assert "Effect" in result.text
    assert result.empty_body is False
    assert result.error == ""


def test_a_truly_empty_docx_reports_an_empty_body(tmp_path):
    """No paragraphs, no tables: a real blank submission, not a crash."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.save(str(path))

    result = extract(path)

    assert result.empty_body is True
    assert result.error == ""


def test_docx_headers_footers_and_properties_reach_metadata(tmp_path):
    """Every identity-bearing string outside the body must reach metadata,
    and none of it must leak into the graded text."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("The body text stays on topic.")
    document.core_properties.author = "Maya Lin"
    document.core_properties.last_modified_by = "Jane Smith"
    document.core_properties.title = "Essay by Maya Lin"
    document.core_properties.comments = "Reviewed by Prof Chen"
    section = document.sections[0]
    section.header.paragraphs[0].text = "Header: Maya Lin, Section 3"
    section.footer.paragraphs[0].text = "Footer: page prepared for Jane Smith"
    document.save(str(path))

    result = extract(path)

    for value in (
        "Maya Lin",
        "Jane Smith",
        "Essay by Maya Lin",
        "Reviewed by Prof Chen",
        "Header: Maya Lin, Section 3",
        "Footer: page prepared for Jane Smith",
    ):
        assert value in result.metadata

    assert "Header: Maya Lin, Section 3" not in result.text
    assert "Footer: page prepared for Jane Smith" not in result.text


def test_a_utf8_bom_is_stripped(tmp_path):
    """A Windows-authored file's BOM must not corrupt the first character."""
    path = tmp_path / "paper.md"
    path.write_bytes(b"\xef\xbb\xbfMaya Lin wrote this.")
    result = extract(path)
    assert result.text == "Maya Lin wrote this."
    assert result.text.startswith("Maya")


def test_output_name_rejects_a_path_separator():
    with pytest.raises(ValueError):
        output_name("STUDENT_07", "../evil")
    with pytest.raises(ValueError):
        output_name("STUDENT_07", "/abs")
