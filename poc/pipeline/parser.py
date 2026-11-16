"""
Document parser: converts uploaded file bytes into plain text.

Supports: PDF (pdfplumber), DOCX (python-docx), TXT (direct).
"""

import re


def parse(file_bytes: bytes, file_type: str) -> str:
    """Return plain UTF-8 text from file bytes. file_type: pdf | docx | txt."""
    if file_type == "pdf":
        return _parse_pdf(file_bytes)
    elif file_type == "docx":
        return _parse_docx(file_bytes)
    elif file_type == "txt":
        return file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {file_type}")


def _parse_pdf(data: bytes) -> str:
    import io
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return _clean(_join(text_parts))


def _parse_docx(data: bytes) -> str:
    import io
    from docx import Document
    from docx.table import Table

    # Walk paragraphs AND tables in document order. doc.paragraphs alone skips
    # every table, and requirement docs (BRDs, SOWs) keep most of their content
    # in tables: the MediBook BRD went from 300 to ~1,250 words with this.
    doc = Document(io.BytesIO(data))
    parts = []
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            parts.extend(_table_rows(block))
        elif block.text.strip():
            parts.append(block.text)
    return _clean(_join(parts))


def _table_rows(table) -> list[str]:
    """One block per row, cells joined with ' | ', so the chunker can split a
    long table between rows. Merged cells repeat in python-docx, so
    consecutive duplicates are dropped."""
    rows = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = cell.text.strip()
            if text and (not cells or cells[-1] != text):
                cells.append(text)
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _join(parts: list[str]) -> str:
    return "\n\n".join(parts)


def _clean(text: str) -> str:
    # Collapse runs of blank lines to at most two
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace per line
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()
