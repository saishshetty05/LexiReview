"""Tests for extraction.py. All fixtures are SYNTHETIC documents built in-test
(fpdf2 / python-docx) — never real documents (CLAUDE.md rule: SYNTHETIC_ONLY)."""

from __future__ import annotations

import io
import os

import pytest

from app.extraction import ExtractionError, extract_text


def _make_pdf_bytes(body: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, body)
    return bytes(pdf.output())


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_unsupported_type_rejected():
    with pytest.raises(ExtractionError) as exc_info:
        extract_text(b"whatever", "txt")
    assert exc_info.value.category == "unsupported_type"


def test_pdf_extracts_text():
    body = "LEASE AGREEMENT between PERSON_1 and PERSON_2. Monthly rent: AMOUNT_1."
    result = extract_text(_make_pdf_bytes(body), "pdf")
    assert result.page_count == 1
    assert "PERSON_1" in result.text
    assert "AMOUNT_1" in result.text


def test_docx_extracts_text():
    paragraphs = [
        "NON-DISCLOSURE AGREEMENT between PERSON_1 and PERSON_2.",
        "Confidential information shall not be disclosed for a period of 2 years.",
    ]
    result = extract_text(_make_docx_bytes(paragraphs), "docx")
    assert result.paragraph_count == 2
    assert "NON-DISCLOSURE AGREEMENT" in result.text
    assert "2 years" in result.text


def test_temp_dir_purged_on_success(monkeypatch):
    created_dirs = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_dirs.append(path)
        return path

    monkeypatch.setattr("app.extraction.tempfile.mkdtemp", spy_mkdtemp)

    body = "TERMINATION clause: either party may terminate with 60 days notice."
    extract_text(_make_pdf_bytes(body), "pdf")

    assert len(created_dirs) == 1
    assert not os.path.exists(created_dirs[0])


def test_temp_dir_purged_on_failure(monkeypatch):
    created_dirs = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_dirs.append(path)
        return path

    monkeypatch.setattr("app.extraction.tempfile.mkdtemp", spy_mkdtemp)

    with pytest.raises(ExtractionError):
        extract_text(b"this is not a real pdf file", "pdf")

    assert len(created_dirs) == 1
    assert not os.path.exists(created_dirs[0])
