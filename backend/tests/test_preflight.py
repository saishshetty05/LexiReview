"""Tests for the deterministic preflight gate."""
from app.preflight import MAX_UPLOAD_BYTES, run_preflight


def test_missing_file():
    result = run_preflight("/nonexistent/file.pdf")
    assert not result.accepted
    assert "not found" in result.reason.lower()


def test_empty_file(tmp_path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    result = run_preflight(str(p))
    assert not result.accepted


def test_wrong_type_rejected(tmp_path):
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"just some text pretending to be a pdf")
    result = run_preflight(str(p))
    assert not result.accepted
    assert "unsupported" in result.reason.lower() or "unrecognized" in result.reason.lower()


def test_oversized_rejected(tmp_path):
    p = tmp_path / "big.pdf"
    with open(p, "wb") as f:
        f.seek(MAX_UPLOAD_BYTES + 1)
        f.write(b"\0")
    result = run_preflight(str(p))
    assert not result.accepted
    assert "limit" in result.reason.lower()


def test_valid_small_pdf_accepted(tmp_path):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 10, "LEASE AGREEMENT between PERSON_1 and PERSON_2. "
                          "Monthly rent: AMOUNT_1. Termination requires 60 days notice.")
    p = tmp_path / "ok.pdf"
    pdf.output(str(p))

    result = run_preflight(str(p))
    assert result.accepted, result.reason
    assert result.metadata["file_type"] == "pdf"
    assert result.metadata["page_count"] == 1
    assert result.metadata["estimated_tokens"] > 0
    assert "sha256" in result.metadata
