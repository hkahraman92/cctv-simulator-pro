"""EN 62676-4 knowledge base + spec-language mapping for the compliance review.

Not a substitute for the standard text — a small, auditable map from the terms
that appear in Turkish CCTV tender specs to the clause and the numeric criterion
they imply, so the compliance matrix can cite *why* a requirement exists and the
optic engine can be pointed at the right pixel-density target.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# EN 62676-4 Table (pixel density across the target, px/m)
DORI_PPM: Dict[str, float] = {
    "monitor": 12.5,
    "detect": 25.0,
    "observe": 62.5,
    "recognize": 125.0,
    "identify": 250.0,
    "inspect": 1000.0,
}

# Turkish task words -> DORI key. ANPR / face have their own de-facto targets.
# Keep stems specific: "kontrol" ("erişim kontrolü", "giriş kontrol sistemi") and
# a bare "tanı" ("tanımlı", "tanı" = diagnosis) fired spurious DORI requirements.
_TASK_TR: Dict[str, str] = {
    "izleme": "monitor", "gözetleme": "monitor", "genel görünüm": "monitor",
    "algıla": "detect", "tespit": "detect", "sezme": "detect",
    "gözlem": "observe", "hareket takib": "observe",
    "tanıma": "recognize", "teşhis öncesi": "recognize",
    "teşhis": "identify", "kimlik belirle": "identify", "kimlik tespit": "identify",
    "detaylı inceleme": "inspect", "adli inceleme": "inspect",
}

# Non-DORI but common numeric targets used in TR specs.
SPECIAL_PPM: Dict[str, float] = {
    "plaka": 143.0,        # TR plate ~ 143 px/m (character height rule)
    "anpr": 143.0,
    "lpr": 143.0,
    "yüz tanıma": 250.0,   # face identification, optical
    "yüz teşhis": 250.0,
    "yüz tespit": 60.0,    # face *detection*
}

# category -> (clause ref, one-line description)
CLAUSES: Dict[str, Tuple[str, str]] = {
    "dori":         ("EN 62676-4 §6.2 / Tablo B.1", "Piksel yoğunluğu (DORI): Detect 25, Observe 62.5, Recognize 125, Identify 250 px/m"),
    "resolution":   ("EN 62676-4 §6.2", "Sensör çözünürlüğü ve hedefteki piksel yoğunluğu"),
    "lens_range":   ("EN 62676-4 §6.3", "Görüş alanı (FoV) ve odak uzaklığı seçimi"),
    "camera_type":  ("EN 62676-4 §5.2", "Kamera tipi (sabit / PTZ) görev tanımına uygun"),
    "ir":           ("EN 62676-2 §5.3", "Kızılötesi / aktif aydınlatma menzili"),
    "lux":          ("EN 62676-4 §6.4", "Sahne aydınlatma seviyeleri ve minimum illüminasyon"),
    "wdr":          ("EN 62676-4 §6.4.3", "Geniş dinamik aralık (WDR/HDR) — arkadan ışık"),
    "fps":          ("EN 62676-4 §6.5", "Kare hızı — hareketli hedef için ≥ 12,5 fps önerilir"),
    "environment":  ("IEC 60529 (IP) / IEC 62262 (IK)", "Muhafaza koruma sınıfları"),
    "temperature":  ("EN 62676-4 §7.2", "Çalışma sıcaklık aralığı"),
    "analytics":    ("EN 62676-4 §6.7", "Video içerik analizi (VCA) fonksiyonları"),
    "storage":      ("EN 62676-2 §6 / EN 62676-4 §6.6", "Kayıt ve saklama"),
    "codec":        ("EN 62676-2 §5.4", "Video sıkıştırma / iletim"),
    "audio":        ("EN 62676-4 §6.8", "Ses ve alarm G/Ç"),
    "sensor_size":  ("EN 62676-4 §6.2", "Sensör formatı"),
}

# Vague adjectives that carry no number -> the spec needs a clarification.
_VAGUE = [
    "yüksek", "düşük", "yeterli", "iyi", "uygun", "gelişmiş", "kaliteli",
    "hızlı", "güçlü", "yakın", "uzak", "hassas", "net", "keskin", "makul",
    "yeterince", "optimum", "üst düzey", "profesyonel",
]
_VAGUE_RE = re.compile(
    r"(?<![\wğüşıöçĞÜŞİÖÇ])(" + "|".join(_VAGUE) + r")(?![\wğüşıöçĞÜŞİÖÇ])",
    re.IGNORECASE,
)
# A number that actually quantifies a requirement carries a unit. A bare count
# ("1 adet ... kamera") does not make "yüksek çözünürlük" any less vague, so the
# unit list is explicit and boundary-guarded (no bare "a"/"k"/"p" that would
# swallow "1 adet", "1 kamera").
_SPEC_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:mp|megapiksel|metre|mm|cm|km|db|lux|lx|lümen|fps|hz|khz|px|piksel|%|gb|tb|"
    r"mbps|kbps|kbit|mbit|kelvin|watt|volt|amper|ip\d\d?|ik\d\d?|nit|cd|m|k)"
    r"(?![a-zçğıöşü])",
    re.IGNORECASE,
)


def clause_for(category: str) -> Tuple[str, str]:
    return CLAUSES.get(category, ("—", ""))


def dori_ppm_for_task(text: str) -> Optional[Tuple[str, float]]:
    """('recognize', 125.0) for a task phrase, or None."""
    low = text.casefold()
    for phrase, ppm in SPECIAL_PPM.items():
        if phrase in low:
            return phrase, ppm
    for phrase, key in _TASK_TR.items():
        if phrase in low:
            return key, DORI_PPM[key]
    return None


_CLAUSE_SPLIT = re.compile(r"[.\n;•]|(?:^|\s)[-*]\s|(?:\s-\s)")


def find_ambiguities(spec_text: str) -> List[Dict[str, str]]:
    """Vague, unquantified requirements + a clarification question to send back.

    Works clause by clause: a vague adjective is flagged only when its own
    clause carries no *unit-bearing* number ("1 adet" is a count, not a spec).
    """
    out: List[Dict[str, str]] = []
    seen = set()
    for clause in _CLAUSE_SPLIT.split(spec_text):
        clause = (clause or "").strip()
        if len(clause) < 6 or _SPEC_NUMBER.search(clause):
            continue
        m = _VAGUE_RE.search(clause)
        if not m:
            continue
        term = m.group(1).lower()
        key = (term, clause[:50].casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "term": term,
            "quote": clause.replace("\n", " ")[:180],
            "clarification": (
                f"“{clause[:80].strip()}” — “{term}” nicel değil. "
                "Beklenen sayısal değer nedir? (ör. çözünürlük X MP, menzil Y m, "
                "WDR Z dB, aydınlatma W lux, fps N)"
            ),
        })
    return out
