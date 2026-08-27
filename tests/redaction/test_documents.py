"""Layer 3 must read text, surface doc metadata, and never leak a filename."""

import hashlib
import os
from zipfile import ZipFile as RealZipFile

import pytest

from scripts.redaction.documents import extract, output_name, read_submission_snapshot


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


def test_text_submission_over_the_public_limit_is_refused_before_reading(tmp_path):
    path = tmp_path / "oversized.md"
    with path.open("wb") as handle:
        handle.truncate(26 * 1024 * 1024)

    result = extract(path)

    assert "too large" in result.error.lower()


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
        assert value not in result.text


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


def test_merged_table_cells_are_not_duplicated(tmp_path):
    """python-docx hands back the same cell for every grid position a merge
    spans; a merged header must render once, not once per spanned column."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(1, 0).text = "C"
    table.cell(1, 1).text = "D"
    document.save(str(path))

    result = extract(path)

    assert result.text == "A B\nC | D"
    assert result.text.count("A B") == 1


def test_document_order_is_preserved_across_paragraphs_and_tables(tmp_path):
    """A table between two paragraphs must not trail both of them: an essay
    that says "as the table below shows" has to read in the order it was
    written."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("FIRST paragraph.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "TBL-a"
    table.cell(0, 1).text = "TBL-b"
    document.add_paragraph("LAST paragraph.")
    document.save(str(path))

    result = extract(path)

    first = result.text.index("FIRST paragraph.")
    table_pos = result.text.index("TBL-a")
    last = result.text.index("LAST paragraph.")
    assert first < table_pos < last


def test_nested_table_content_is_extracted(tmp_path):
    """A table nested inside another table's cell must not vanish."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    table = document.add_table(rows=1, cols=1)
    outer_cell = table.cell(0, 0)
    outer_cell.paragraphs[0].text = "OUTER"
    inner_table = outer_cell.add_table(rows=1, cols=2)
    inner_table.cell(0, 0).text = "IN-a"
    inner_table.cell(0, 1).text = "IN-b"
    document.save(str(path))

    result = extract(path)

    assert "IN-a" in result.text
    assert "IN-b" in result.text


def test_metadata_is_deduplicated_and_drops_library_defaults(tmp_path):
    """The same name often sits in both author and last_modified_by, and a
    fresh docx pre-fills comments with a python-docx default -- neither
    should reach the caller as if it were two different people."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("Body text.")
    document.core_properties.author = "Maya Lin"
    document.core_properties.last_modified_by = "Maya Lin"
    document.save(str(path))

    result = extract(path)

    assert result.metadata == ("Maya Lin",)


def test_docx_body_and_metadata_share_one_character_budget(tmp_path, monkeypatch):
    """Metadata must not bypass the public extracted-text limit."""
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("1234567890")
    document.core_properties.author = "Maya Lin"
    document.save(str(path))
    monkeypatch.setattr("scripts.redaction.documents.MAX_EXTRACTED_CHARACTERS", 12)

    result = extract(path)

    assert "too much extracted text" in result.error


def test_docx_character_budget_includes_metadata_separators(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    document = docx.Document()
    document.add_paragraph("1234567890")
    document.core_properties.author = "Maya Lin"
    document.save(str(path))
    monkeypatch.setattr("scripts.redaction.documents.MAX_EXTRACTED_CHARACTERS", 18)

    result = extract(path)

    assert "too much extracted text" in result.error


def test_docx_preflight_and_parser_use_the_same_byte_snapshot(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    path = tmp_path / "paper.docx"
    original_document = docx.Document()
    original_document.add_paragraph("ORIGINAL BODY")
    original_document.save(str(path))
    original_bytes = path.read_bytes()
    replacement = tmp_path / "replacement.docx"
    replacement_document = docx.Document()
    replacement_document.add_paragraph("REPLACEMENT BODY")
    replacement_document.save(str(replacement))
    replacement_bytes = replacement.read_bytes()

    class SwapAfterPreflight:
        def __init__(self, value):
            self.archive = RealZipFile(value)

        def __enter__(self):
            return self.archive

        def __exit__(self, exc_type, exc, traceback):
            self.archive.close()
            path.write_bytes(replacement_bytes)

    monkeypatch.setattr(
        "scripts.redaction.documents.ZipFile", lambda value: SwapAfterPreflight(value)
    )

    result = extract(path)

    assert result.text == "ORIGINAL BODY"
    assert result.source_sha256 == hashlib.sha256(original_bytes).hexdigest()


def test_text_extraction_exposes_the_exact_snapshot_digest(tmp_path):
    path = tmp_path / "paper.md"
    data = b"Platforms curate public attention."
    path.write_bytes(data)

    result = extract(path)

    assert result.text == data.decode("utf-8")
    assert result.source_sha256 == hashlib.sha256(data).hexdigest()


def test_submission_snapshot_rejects_an_existing_hard_link(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("Body text.", encoding="utf-8")
    os.link(path, tmp_path / "second-name.md")

    with pytest.raises(ValueError, match="single link"):
        read_submission_snapshot(path)


def test_submission_snapshot_rejects_a_hard_link_added_during_read(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.md"
    path.write_bytes(b"x" * (128 * 1024))
    original_read = os.read
    linked = False

    def link_during_read(descriptor, size):
        nonlocal linked
        chunk = original_read(descriptor, size)
        if chunk and not linked:
            linked = True
            os.link(path, tmp_path / "late-link.md")
        return chunk

    monkeypatch.setattr("scripts.redaction.documents.os.read", link_during_read)

    with pytest.raises(ValueError, match="changed during reading"):
        read_submission_snapshot(path)


def test_submission_snapshot_rejects_modify_and_unlink_between_read_chunks(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.md"
    path.write_bytes(b"x" * (128 * 1024))
    original_read = os.read
    changed = False

    def change_inode_during_read(descriptor, size):
        nonlocal changed
        chunk = original_read(descriptor, size)
        if chunk and not changed:
            changed = True
            link = tmp_path / "temporary-link.md"
            os.link(path, link)
            with path.open("r+b") as handle:
                handle.seek(96 * 1024)
                handle.write(b"y")
                handle.flush()
                os.fsync(handle.fileno())
            link.unlink()
        return chunk

    monkeypatch.setattr(
        "scripts.redaction.documents.os.read", change_inode_during_read
    )

    with pytest.raises(ValueError, match="changed during reading"):
        read_submission_snapshot(path)
