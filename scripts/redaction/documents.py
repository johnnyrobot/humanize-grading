"""Layer 3: read submission text, surface document metadata, name outputs.

Output files are named by token. A real name must never reach a filename.
The docx author field must be returned so the caller can tokenize it, never
silently dropped and never silently kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class Extracted:
    """Text pulled from a submission, plus anything the container revealed."""

    text: str = ""
    author: str = ""
    error: str = ""


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
    text = "\n\n".join(paragraph.text for paragraph in document.paragraphs)
    author = document.core_properties.author or ""
    return Extracted(text=text, author=author)


def extract(path: Path) -> Extracted:
    """Return the text of a submission, or an error explaining why not."""
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        try:
            return Extracted(text=path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            return Extracted(error=f"Could not read {path.name}: {exc}")

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
    return f"{token}{suffix}"
