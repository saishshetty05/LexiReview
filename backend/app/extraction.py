"""extraction.py — plain-text extraction from PDF/DOCX bytes.

All file I/O happens inside a per-job random temp directory (tempfile.mkdtemp),
purged in a `finally` block regardless of outcome (CLAUDE.md rule 5). Errors
never carry document text or filenames — only a machine-readable category and
the exception class name (CLAUDE.md rule 2).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

SUPPORTED_TYPES = ("pdf", "docx")


class ExtractionError(Exception):
    """Raised on any extraction failure. category+message only — never document content."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(f"{category}: {message}")


@dataclass
class ExtractionResult:
    text: str
    page_count: int | None = None
    paragraph_count: int | None = None


def extract_text(file_bytes: bytes, file_type: str) -> ExtractionResult:
    """Extract plain text from PDF or DOCX bytes.

    Writes `file_bytes` into a fresh per-call temp directory, extracts, and
    always removes that directory before returning (or raising).
    """
    if file_type not in SUPPORTED_TYPES:
        raise ExtractionError("unsupported_type", f"file_type must be one of {SUPPORTED_TYPES}")

    job_dir = tempfile.mkdtemp(prefix="lexireview-extract-")
    try:
        suffix = ".pdf" if file_type == "pdf" else ".docx"
        doc_path = Path(job_dir) / f"source{suffix}"
        doc_path.write_bytes(file_bytes)

        if file_type == "pdf":
            return _extract_pdf(doc_path)
        return _extract_docx(doc_path)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise ExtractionError("pdf_read_error", type(exc).__name__) from exc
    except Exception as exc:  # defensive: pypdf can raise various parse errors
        raise ExtractionError("pdf_read_error", type(exc).__name__) from exc

    pages_text: list[str] = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception as exc:
            raise ExtractionError("pdf_page_extract_error", type(exc).__name__) from exc

    return ExtractionResult(text="\n\n".join(pages_text), page_count=len(reader.pages))


def _extract_docx(path: Path) -> ExtractionResult:
    try:
        doc = DocxDocument(str(path))
    except PackageNotFoundError as exc:
        raise ExtractionError("docx_read_error", type(exc).__name__) from exc
    except Exception as exc:  # defensive: python-docx can raise various parse errors
        raise ExtractionError("docx_read_error", type(exc).__name__) from exc

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return ExtractionResult(text="\n\n".join(paragraphs), paragraph_count=len(paragraphs))
