"""Formal EN 62676-4 compliance statement from a compliance-analysis result.

The deliverable a bidder submits: requirement, spec reference / standard clause,
proposed response, evidence, verdict, per line — plus a summary, the RFI
(clarification) list, and a signature block. Markdown out; a thin PDF wrapper
reuses exporters.write_simple_pdf.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

_VERDICT_MARK = {"Uyumlu": "U", "Kısmi": "K", "Uyumsuz": "X", "Bulunamadı": "?"}


def build_statement(result: Dict[str, Any], *, project_name: str = "",
                    bidder: str = "", chosen_by_profile: Optional[Dict[str, str]] = None) -> str:
    """One markdown document. ``chosen_by_profile`` maps profile_name -> the
    camera model whose row should be used as 'our response'; defaults to the
    top-scored model per profile."""
    scores = result.get("camera_scores", [])
    if chosen_by_profile is None:
        chosen_by_profile = {}
        for row in sorted(scores, key=lambda r: r.get("score", 0), reverse=True):
            chosen_by_profile.setdefault(row.get("profile_name", ""), row.get("camera_model", ""))

    matrix = result.get("matrix", [])
    reqs = result.get("requirements", [])
    req_by_id = {r.get("id"): r for r in reqs}

    now = _dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    L: List[str] = []
    L.append("# EN 62676-4 UYGUNLUK BEYANI")
    L.append("")
    L.append(f"- **Proje:** {project_name or '—'}")
    L.append(f"- **Teklif veren:** {bidder or '—'}")
    L.append(f"- **Tarih:** {now}")
    L.append(f"- **Analiz:** {result.get('recommendation', '')}")
    L.append("")
    L.append("Kısaltmalar: U = Uyumlu · K = Kısmi · X = Uyumsuz · ? = Veri yok")
    L.append("")

    profiles = sorted({m.get("profile_name", "") for m in matrix}) or [""]
    for pname in profiles:
        model = chosen_by_profile.get(pname, "")
        prows = [m for m in matrix if m.get("profile_name") == pname
                 and (not model or m.get("camera_model") == model)]
        if not prows:
            continue
        L.append(f"## Profil: {pname or 'Genel'}")
        L.append("")
        L.append(f"**Önerilen model:** {model or '—'}")
        srow = next((s for s in scores if s.get("profile_name") == pname
                     and s.get("camera_model") == model), None)
        if srow:
            L.append(f"**Kurallı skor:** {srow.get('score')}/100 · {srow.get('verdict')}")
        L.append("")
        L.append("| # | İster | Standart | Yanıt | Kanıt | Güven |")
        L.append("|---|---|---|---|---|---|")
        for m in prows:
            r = req_by_id.get(m.get("requirement_id"), {})
            mark = _VERDICT_MARK.get(m.get("status", ""), "?")
            clause = m.get("standard_clause") or r.get("standard_clause") or "—"
            ev = (m.get("evidence") or "").replace("|", "/").replace("\n", " ")
            req_txt = (m.get("requirement") or "").replace("|", "/")
            conf = m.get("confidence")
            conf_s = f"%{conf * 100:.0f}" if isinstance(conf, (int, float)) else "—"
            L.append(f"| {m.get('requirement_id', '')} | {req_txt} | {clause} "
                     f"| **{mark}** {m.get('status', '')} | {ev[:180]} | {conf_s} |")
            quote = m.get("spec_quote") or r.get("spec_quote")
            if quote:
                L.append(f"| | _şartname: “{quote[:160]}”_ | | | | |")
        L.append("")

    amb = result.get("ambiguities", [])
    if amb:
        L.append("## Açıklama Talepleri (RFI)")
        L.append("")
        L.append("Aşağıdaki maddeler nicel değildir; teklif öncesi netleştirilmelidir.")
        L.append("")
        for i, a in enumerate(amb, 1):
            L.append(f"{i}. {a.get('clarification', a.get('quote', ''))}")
        L.append("")

    L.append("## Beyan")
    L.append("")
    L.append("Yukarıdaki tablo, teklif edilen ekipmanın şartname maddelerine karşı "
             "uygunluğunu; optik başarım maddeleri EN 62676-4 piksel yoğunluğu "
             "(DORI) esasına göre hesap motoruyla, diğerleri üretici broşür "
             "verisiyle doğrulanarak sunar.")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| Hazırlayan | ______________________ |")
    L.append("| Tarih / İmza | ______________________ |")
    return "\n".join(L)


def write_statement_pdf(path: str, result: Dict[str, Any], **kw) -> None:
    from .exporters import write_simple_pdf
    md = build_statement(result, **kw)
    lines = [ln.replace("**", "").replace("| ", "").replace(" |", "").replace("|", " ")
             for ln in md.splitlines()]
    write_simple_pdf(path, "EN 62676-4 UYGUNLUK BEYANI", lines)
