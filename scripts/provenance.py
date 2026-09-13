"""Provenance marker helper.

Adds a small, invisible marker to the text of a file so a file's origin can
be recognized later. The marker carries one fixed word. It never changes the
visible text: readers, editors, word counts, and JSON or CSV parsers see the
file exactly as before.

Two encodings share one word:

- Text files (`.md`, `.txt`) and the DOCX keywords property use zero-width
  characters. They add no visible glyph and survive `strip()`.
- Data files (`.json`, `.csv`) use a trailing line of spaces and tabs. Zero-
  width characters would be invalid outside a JSON string, and a spaces-and-
  tabs line is ignored by JSON parsing and by the roster reader.

Every encoding is idempotent: stamping an already-stamped file returns it
unchanged, so hashes stay stable across repeated reads.
"""

from __future__ import annotations

from pathlib import Path

WORD = "elnerdo"

# Zero-width alphabet for text files. The sentinel brackets the payload so the
# marker can be found and removed without disturbing anything around it.
_ZW_ZERO = "​"  # zero-width space -> bit 0
_ZW_ONE = "‌"  # zero-width non-joiner -> bit 1
_ZW_SENTINEL = "⁠"  # word joiner -> start and end of the payload
_ZERO_WIDTH_CHARS = frozenset({_ZW_ZERO, _ZW_ONE, _ZW_SENTINEL})

# Whitespace alphabet for data files.
_WS_ZERO = " "  # space -> bit 0
_WS_ONE = "\t"  # tab -> bit 1


def _bits(word: str) -> list[int]:
    """Return the bits of a word's UTF-8 bytes, most significant first."""
    bits: list[int] = []
    for byte in word.encode("utf-8"):
        bits.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
    return bits


def _bytes_from_bits(bits: list[int]) -> bytes:
    """Rebuild bytes from a bit list whose length is a multiple of eight."""
    out = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit in bits[start : start + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def _encode_zero_width(word: str) -> str:
    body = "".join(_ZW_ONE if bit else _ZW_ZERO for bit in _bits(word))
    return _ZW_SENTINEL + body + _ZW_SENTINEL


def _encode_whitespace(word: str) -> str:
    return "".join(_WS_ONE if bit else _WS_ZERO for bit in _bits(word))


def _decode_zero_width(text: str) -> str | None:
    start = text.find(_ZW_SENTINEL)
    if start == -1:
        return None
    end = text.find(_ZW_SENTINEL, start + 1)
    if end == -1:
        return None
    bits = [1 if ch == _ZW_ONE else 0 for ch in text[start + 1 : end]]
    if not bits or len(bits) % 8:
        return None
    try:
        return _bytes_from_bits(bits).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _decode_whitespace_line(line: str) -> str | None:
    if not line or any(ch not in (_WS_ZERO, _WS_ONE) for ch in line) or len(line) % 8:
        return None
    bits = [1 if ch == _WS_ONE else 0 for ch in line]
    try:
        return _bytes_from_bits(bits).decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_marker(text: str) -> str | None:
    """Return the marker word if the text carries one, else None."""
    word = _decode_zero_width(text)
    if word is not None:
        return word
    for line in reversed(text.splitlines()):
        if line == "":
            continue
        return _decode_whitespace_line(line)
    return None


def stamp_text(text: str) -> str:
    """Add the zero-width marker before the final newline, once."""
    if _decode_zero_width(text) == WORD:
        return text
    marker = _encode_zero_width(WORD)
    if text.endswith("\n"):
        return text[:-1] + marker + "\n"
    return text + marker


def stamp_json(text: str) -> str:
    """Add a trailing whitespace-line marker after the JSON value, once."""
    return _stamp_trailing_whitespace(text)


def stamp_csv(text: str) -> str:
    """Add a trailing whitespace-line marker after the CSV rows, once."""
    return _stamp_trailing_whitespace(text)


def _stamp_trailing_whitespace(text: str) -> str:
    if read_marker(text) == WORD:
        return text
    marker = _encode_whitespace(WORD)
    if text.endswith("\n"):
        return text + marker + "\n"
    return text + "\n" + marker + "\n"


def stamp_bytes(data: bytes, suffix: str) -> bytes:
    """Stamp raw bytes chosen by file suffix. Unknown suffixes are unchanged."""
    suffix = suffix.lower()
    if suffix in (".md", ".txt"):
        text = data.decode("utf-8")
        return stamp_text(text).encode("utf-8")
    if suffix == ".json":
        return stamp_json(data.decode("utf-8")).encode("utf-8")
    if suffix == ".csv":
        return stamp_csv(data.decode("utf-8")).encode("utf-8")
    if suffix == ".docx":
        return stamp_docx_bytes(data)
    return data


def stamp_docx_bytes(data: bytes) -> bytes:
    """Store the zero-width marker in the DOCX keywords property, once."""
    try:
        import docx  # type: ignore
    except ImportError:
        return data
    from io import BytesIO

    document = docx.Document(BytesIO(data))
    keywords = document.core_properties.keywords or ""
    if _decode_zero_width(keywords) == WORD:
        return data
    document.core_properties.keywords = keywords + _encode_zero_width(WORD)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def strip_marker(data: bytes, suffix: str) -> bytes:
    """Return the bytes with any marker removed, for stable hashing of inputs."""
    suffix = suffix.lower()
    if suffix in (".md", ".txt", ".json", ".csv"):
        text = data.decode("utf-8")
        cleaned = "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)
        lines = cleaned.split("\n")
        rebuilt = [
            line
            for line in lines
            if _decode_whitespace_line(line) != WORD
        ]
        return "\n".join(rebuilt).encode("utf-8")
    return data


def strip_text_marker(text: str) -> str:
    """Remove any zero-width marker characters from text."""
    if not any(ch in _ZERO_WIDTH_CHARS for ch in text):
        return text
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)


def stamp_file(path: Path) -> bool:
    """Stamp a file in place. Return True when the file changed."""
    path = Path(path)
    data = path.read_bytes()
    stamped = stamp_bytes(data, path.suffix)
    if stamped == data:
        return False
    _atomic_write(path, stamped)
    return True


def stamp_input(path: Path) -> None:
    """Stamp a source file the pipeline is about to read. Best effort.

    Stamping an input must never stop grading, so any error is swallowed:
    a missing file, a symlink, a permission problem, or a type this helper
    does not handle all leave the read to proceed unchanged.
    """
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return
        stamp_file(path)
    except (OSError, ValueError, UnicodeDecodeError):
        return


def _atomic_write(path: Path, data: bytes) -> None:
    import os
    import tempfile

    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Provenance marker helper.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stamp", "check", "decode"):
        part = sub.add_parser(name)
        part.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    status = 0
    for path in args.paths:
        if args.command == "stamp":
            changed = stamp_file(path)
            print(f"{'stamped' if changed else 'unchanged'}: {path}")
        else:
            data = Path(path).read_bytes()
            try:
                word = read_marker(data.decode("utf-8"))
            except UnicodeDecodeError:
                word = _decode_zero_width(stamp_docx_word(path) or "")
            if args.command == "decode":
                print(f"{path}: {word or ''}")
            elif word != WORD:
                print(f"missing marker: {path}")
                status = 1
    return status


def stamp_docx_word(path: Path) -> str | None:
    try:
        import docx  # type: ignore
    except ImportError:
        return None
    document = docx.Document(str(path))
    return document.core_properties.keywords or None


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
