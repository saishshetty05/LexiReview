"""
preflight.py — the deterministic pre-flight gate.

Every uploaded document passes through this module BEFORE anything else happens:
before storage bookkeeping completes, before a job is enqueued, and long before
any LLM sees a single token. Plain Python decides, deterministically, whether a
document is even eligible for processing.

Design rule (PRD / decision log): never use AI for a decision code can make.
Layer 1 of the gate is Nginx (byte size, rate limit). This module is Layer 2:
metadata checks that require opening the file. Layer 3 (extraction quality)
lives in the worker.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass, field
from typing import Optional

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# ── Caps (env-overridable; see .env.example) ──────────────────────────────
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Rough chars-per-token heuristic for the context-cap estimate.
CHARS_PER_TOKEN = 4
MAX_TOKENS_ESTIMATE = 150_000  # ~100 pages of dense legal text

PDF_MAGIC = b"%PDF"
ZIP_MAGIC = b"PK\x03\x04"  # DOCX is a ZIP container


@dataclass
class PreflightResult:
    """Structured verdict returned to the upload endpoint."""

    verdict: str  # "accept" | "reject"
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.verdict == "accept"


def _reject(reason: str, **meta) -> PreflightResult:
    return PreflightResult(verdict="reject", reason=reason, metadata=meta)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_type(path: str) -> Optional[str]:
    """Identify the REAL file type by magic bytes — never trust the extension."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        # A DOCX is a ZIP that contains [Content_Types].xml and a word/ folder.
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "[Content_Types].xml" in names and any(
                    n.startswith("word/") for n in names
                ):
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


def _check_pdf(path: str) -> PreflightResult:
    try:
        reader = PdfReader(path)
    except PdfReadError:
        return _reject("File is not a readable PDF (corrupted?).")
    except Exception as exc:  # defensive: pypdf can raise various parse errors
        return _reject(f"PDF could not be parsed: {type(exc).__name__}.")

    if reader.is_encrypted:
        return _reject(
            "PDF is password-protected. Please upload a decrypted copy."
        )

    pages = len(reader.pages)
    if pages == 0:
        return _reject("PDF contains no pages.")
    if pages > MAX_PAGES:
        return _reject(
            f"Document has {pages} pages; the current limit is {MAX_PAGES}. "
            "Larger documents are not supported in this version.",
            page_count=pages,
        )

    # Estimate token load from a text sample (first + middle + last pages).
    sample_idx = {0, pages // 2, pages - 1}
    sample_chars = 0
    sampled = 0
    for i in sorted(sample_idx):
        try:
            sample_chars += len(reader.pages[i].extract_text() or "")
            sampled += 1
        except Exception:
            continue
    if sampled and sample_chars == 0:
        # No extractable text at all -> likely a pure scan. v1 has no OCR.
        return _reject(
            "No extractable text found — this looks like a scanned document. "
            "Scanned documents are not supported in this version.",
            page_count=pages,
        )
    est_tokens = (
        int((sample_chars / max(sampled, 1)) * pages / CHARS_PER_TOKEN)
        if sampled
        else 0
    )
    if est_tokens > MAX_TOKENS_ESTIMATE:
        return _reject(
            "Document text exceeds the analysis context limit.",
            page_count=pages,
            estimated_tokens=est_tokens,
        )

    return PreflightResult(
        verdict="accept",
        metadata={"file_type": "pdf", "page_count": pages,
                  "estimated_tokens": est_tokens},
    )


def _check_docx(path: str) -> PreflightResult:
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad is not None:
                return _reject("DOCX archive is corrupted.")
            doc_xml_size = sum(
                info.file_size for info in z.infolist()
                if info.filename.startswith("word/") and info.filename.endswith(".xml")
            )
    except zipfile.BadZipFile:
        return _reject("File is not a readable DOCX (corrupted?).")

    # XML is verbose; rough conversion factor to text chars, then to tokens.
    est_tokens = int(doc_xml_size * 0.5 / CHARS_PER_TOKEN)
    if est_tokens > MAX_TOKENS_ESTIMATE:
        return _reject(
            "Document text exceeds the analysis context limit.",
            estimated_tokens=est_tokens,
        )
    return PreflightResult(
        verdict="accept",
        metadata={"file_type": "docx", "estimated_tokens": est_tokens},
    )


def run_preflight(path: str) -> PreflightResult:
    """Main entry point: metadata verdict for an uploaded file.

    Order matters — each check is cheaper than the next, and we stop at the
    first failure so rejection costs as little as possible.
    """
    if not os.path.isfile(path):
        return _reject("Upload not found on disk.")

    size = os.path.getsize(path)
    if size == 0:
        return _reject("Uploaded file is empty.")
    if size > MAX_UPLOAD_BYTES:
        return _reject(
            f"File is {size / (1024 * 1024):.1f} MB; the limit is {MAX_UPLOAD_MB} MB.",
            size_bytes=size,
        )

    ftype = _detect_type(path)
    if ftype is None:
        return _reject(
            "Unsupported or unrecognized file type. Please upload a PDF or DOCX."
        )

    result = _check_pdf(path) if ftype == "pdf" else _check_docx(path)
    if not result.accepted:
        return result

    # Passed everything -> stamp identity metadata used for versioning/dedup.
    result.metadata.update(size_bytes=size, sha256=_sha256(path))
    return result
