"""PDF -> plain text for the rule-based spec review.

Tender specs are usually PDFs, often with the requirements in tables. The
Gemini path already accepts PDFs directly; this fills the rule-based /
offline path.

``pypdf`` is an optional dependency (pure-python, no build). Without it,
``extract_text`` returns ``None`` and the UI keeps the paste-text path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

try:
    from pypdf import PdfReader
    _HAVE_PYPDF = True
except Exception:  # pragma: no cover - optional dep
    _HAVE_PYPDF = False

PDF_AVAILABLE = _HAVE_PYPDF


def extract_text(path: str | Path, max_pages: int = 120) -> Optional[str]:
    """All text from a PDF, page-tagged, with basic table-row flattening.

    Returns ``None`` when pypdf is missing or the file yields nothing useful.
    """
    if not _HAVE_PYPDF:
        return None
    try:
        reader = PdfReader(str(path))
    except Exception:
        return None

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            return None

    chunks = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        raw = _tidy_page(raw)
        if raw.strip():
            chunks.append(f"[Sayfa {i + 1}]\n{raw}")

    text = "\n\n".join(chunks).strip()
    return text or None


def _tidy_page(text: str) -> str:
    # collapse hyphen line-wraps ("çözü-\nnürlük" -> "çözünürlük")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # a table row split over lines: join a line that is clearly a continuation
    lines = [ln.rstrip() for ln in text.splitlines()]
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
            continue
        # bullet / numbered / "Key: value" starts a new row; else continuation
        if out and out[-1] and not re.match(r"^([-*•]|\d+[.)]|[A-ZÇĞİÖŞÜ][^:]{0,40}:)", s) \
                and len(out[-1]) < 90 and not out[-1].endswith((".", ":", ";")):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return "\n".join(out)
