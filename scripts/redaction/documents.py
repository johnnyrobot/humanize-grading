"""Layer 3: read submission text, surface document metadata, name outputs.

Output files are named by token. A real name must never reach a filename.

A docx carries names outside its visible text: the author, last-editor,
title, subject and comments properties, plus running headers and footers.
Every one of those strings must reach the caller as metadata, never
silently dropped and never silently folded into the body.

A docx table is body content too. Dropping it would silently grade an
incomplete essay, and a table-only submission would look identical to a
blank one, so an empty body is reported explicitly rather than inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class Extracted:
    """Text pulled from a submission, plus anything the container revealed.

    `metadata` holds identity-bearing strings the container carried outside
    the body -- document properties, headers, footers -- so the caller can
    tokenize them too. `empty_body` is True when the container read cleanly
    but the body itself is blank; that is a fact for the instructor to see,
    not an error.
    """

    text: str = ""
    metadata: tuple[str, ...] = ()
    empty_body: bool = False
    error: str = ""


def _docx_metadata(document) -> tuple[str, ...]:
    """Collect every identity-bearing string a docx carries outside the body."""
    core = document.core_properties
    values = [
        core.author,
        core.last_modified_by,
        core.title,
        core.subject,
        core.comments,
    ]
    for section in document.sections:
        values.extend(paragraph.text for paragraph in section.header.paragraphs)
        values.extend(paragraph.text for paragraph in section.footer.paragraphs)
    return tuple(value for value in values if value)


def _extract_docx(path: Path) -> Extracted:
    try:
        import docx  # type: ignore
    except ImportError:
        return Extracted(
            error="Reading .docx needs python-docx. Install it or convert to Markdown."
        )
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - report any container failure
        return Extracted(error=f"Could not read {path.name}: {exc}")

    body_parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    for table in document.tables:
        rows = (" | ".join(cell.text for cell in row.cells) for row in table.rows)
        row_text = "\n".join(row for row in rows if row)
        if row_text:
            body_parts.append(row_text)
    text = "\n\n".join(body_parts)

    metadata = _docx_metadata(document)
    return Extracted(text=text, metadata=metadata, empty_body=text.strip() == "")


def extract(path: Path) -> Extracted:
    """Return the text of a submission, or an error explaining why not."""
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return Extracted(
                error=f"{path.name} is not valid UTF-8. Re-save it as UTF-8 and try again."
            )
        except OSError as exc:
            return Extracted(error=f"Could not read {path.name}: {exc}")
        return Extracted(text=text, empty_body=text.strip() == "")

    if suffix == ".docx":
        return _extract_docx(path)

    if suffix == ".pdf":
        return Extracted(
            error=f"{path.name} is a PDF. Convert it to Markdown first; "
            "scanned submissions are out of scope."
        )

    return Extracted(error=f"{path.name} has an unsupported type: {suffix}")


def output_name(token: str, suffix: str = ".md") -> str:
    """Name a redacted file by its token so no real name reaches disk."""
    if "/" in suffix or "\\" in suffix or ".." in suffix:
        raise ValueError(f"Unsafe output suffix: {suffix!r}")
    return f"{token}{suffix}"
