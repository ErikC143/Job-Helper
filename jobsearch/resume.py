"""The user's resume, saved once as inputs/resume.txt and shared by every page.

It only changes when the user uploads a replacement or edits their candidate notes. The notes
are kept at the end of the same file, after NOTES_HEADER, so every comparison sees them as part
of the resume. The work authorization picked from WORK_AUTHORIZATIONS is their first line:

    === CANDIDATE NOTES ===
    Work authorization: F-1 student visa (CPT / OPT / STEM OPT)

    Free-form notes...
"""

import hashlib
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

INPUTS_DIR = Path(__file__).resolve().parent.parent / "inputs"
RESUME_TXT = INPUTS_DIR / "resume.txt"
# Older versions of the app kept the uploaded PDF here; it's converted on first load.
LEGACY_PDF_DIR = INPUTS_DIR / "resume"
NOTES_HEADER = "=== CANDIDATE NOTES ==="
WORK_AUTH_PREFIX = "Work authorization: "
WORK_AUTHORIZATIONS = [
    "U.S. citizen",
    "U.S. permanent resident (green card)",
    "F-1 student visa (CPT / OPT / STEM OPT)",
    "J-1 exchange visitor",
    "H-1B visa",
    "H-4 dependent with EAD",
    "L-1 visa, or L-2 dependent with EAD",
    "TN visa (Canadian or Mexican citizen)",
    "E-3 visa (Australian citizen)",
    "O-1 visa",
    "Asylee or refugee (EAD)",
    "DACA or other EAD",
    "Other (described in notes)",
]


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except PdfReadError as e:
        raise ValueError("Could not read this PDF. It may be corrupted or password-protected.") from e


def load() -> str:
    """The saved resume text, or '' if there is none."""
    if not RESUME_TXT.exists():
        _convert_legacy_pdf()
    return RESUME_TXT.read_text() if RESUME_TXT.exists() else ""


def split_notes(text: str) -> tuple[str, str, str]:
    """Split saved resume text into (resume, work authorization, candidate notes).

    The work authorization is '' if the user hasn't picked one.
    """
    body, _, notes = text.partition(f"\n\n{NOTES_HEADER}\n")
    first, _, rest = notes.partition("\n")
    if first.startswith(WORK_AUTH_PREFIX):
        return body, first.removeprefix(WORK_AUTH_PREFIX).strip(), rest.strip()
    return body, "", notes.strip()


def save_notes(notes: str | None = None, work_authorization: str | None = None) -> None:
    """Update the candidate notes and/or work authorization, leaving whichever is None as it is."""
    body, saved_auth, saved_notes = split_notes(load())
    _write(
        body,
        saved_auth if work_authorization is None else work_authorization,
        saved_notes if notes is None else notes,
    )


def _write(body: str, work_authorization: str, notes: str) -> None:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    work_authorization, notes = work_authorization.strip(), notes.strip()
    section = "\n\n".join(
        part for part in (work_authorization and f"{WORK_AUTH_PREFIX}{work_authorization}", notes) if part
    )
    RESUME_TXT.write_text(f"{body}\n\n{NOTES_HEADER}\n{section}" if section else body)


def text_hash(text: str) -> str:
    """Identifies a version of the resume, so reports can record which one they were made from."""
    return hashlib.sha256(text.encode()).hexdigest()


def updated_at() -> str:
    return datetime.fromtimestamp(RESUME_TXT.stat().st_mtime).strftime("%b %d, %Y %I:%M %p")


def save_upload(filename: str, data: bytes) -> None:
    """Replace the saved resume with an uploaded PDF or TXT file, keeping the candidate notes
    and work authorization."""
    if filename.lower().endswith(".pdf"):
        text = pdf_text(data)
    else:
        text = data.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("No text found in this file. A scanned PDF needs to be converted to text first.")
    _, work_authorization, notes = split_notes(RESUME_TXT.read_text()) if RESUME_TXT.exists() else ("", "", "")
    _write(text, work_authorization, notes)


def _convert_legacy_pdf() -> None:
    pdf = next(LEGACY_PDF_DIR.glob("*.pdf"), None) if LEGACY_PDF_DIR.exists() else None
    if pdf:
        try:
            save_upload(pdf.name, pdf.read_bytes())
        except ValueError:
            pass
