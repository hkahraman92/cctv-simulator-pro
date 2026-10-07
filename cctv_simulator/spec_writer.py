"""Draft technical-requirements specification ("teknik şartname") writer.

Turns a camera's filled-in library brochure fields (``database.py``'s
extended field list) plus its *currently computed* DORI/optical performance
(``calculations.calculate_for_camera``'s ``OpticResult`` rows) into a
numbered, tender-style requirements document -- "Kamera en az ... olmalıdır"
/ "The camera shall ... at least ..." -- in Turkish and English.

Deliberately a *draft*: it seeds a requirements list from one camera's own
specs (common practice when a design is used as the technical baseline for
a tender), it does not invent numbers the optic engine or the brochure data
didn't already produce (Kural 1 -- no physics is re-derived here, only
phrased). Free-text brochure values (certificates, codec names, protocol
lists, ...) are not machine-translated; they are reused as-is since they are
largely language-neutral technical tokens (e.g. "IP67", "H.265+", "ONVIF
Profile S/G/T").
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .compliance_standards import DORI_PPM
from .database import camera_db_extended_field_specs, has_camera_db_value
from .models import CameraConfig, OpticResult

try:
    import docx
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    _DOCX_AVAILABLE = True
except ImportError:
    _DOCX_AVAILABLE = False

# The five tiers EN 62676-4:2015 actually defines (Tablo B.1). Reused from
# compliance_standards.DORI_PPM so the DORI vocabulary never forks --
# "inspect" (1000 px/m) is a sector-practice extension beyond the standard's
# own table, left out here the same way models.py's DEFAULT_LEVELS comment
# flags Scrutinize/Validate as "DORI dışı".
_DORI_TIERS: List[Tuple[float, str, str]] = [
    (DORI_PPM["monitor"], "İzleme (Monitoring)", "Monitoring"),
    (DORI_PPM["detect"], "Algılama (Detection)", "Detection"),
    (DORI_PPM["observe"], "Gözlem (Observation)", "Observation"),
    (DORI_PPM["recognize"], "Tanıma (Recognition)", "Recognition"),
    (DORI_PPM["identify"], "Teşhis (Identification)", "Identification"),
]

_MODE_EN = {"Geniş": "wide", "Dar": "tele", "Geniş + Dar": "wide+tele"}

# Core CameraConfig fields this module turns into requirements (not part of
# the camera-library "extended" field set, which only covers brochure-only
# data).
_CORE_FIELD_LABELS: List[Tuple[str, str, str]] = [
    ("sensor_name", "Sensör formatı", "Sensor format"),
    ("resolution_name", "Çözünürlük", "Resolution"),
    ("ir_range_m", "IR aydınlatma mesafesi", "IR illumination range"),
    ("min_lux", "Minimum ışık hassasiyeti (lux)", "Minimum light sensitivity (lux)"),
]

# English labels for database.camera_db_extended_field_specs()'s Turkish
# labels, keyed by field name so a brochure-data edit never desyncs the pair.
_EXT_FIELD_LABELS_EN: Dict[str, str] = {
    "max_fps": "Max. frame rate",
    "lens_type": "Lens type",
    "aperture_f_number": "Aperture",
    "lens_mount": "Lens mount",
    "pan_range_deg": "Pan range",
    "tilt_range_deg": "Tilt range",
    "rotate_range_deg": "Rotate range",
    "shutter_speed": "Shutter speed",
    "day_night": "Day & Night",
    "sn_ratio_db": "Signal/noise ratio",
    "wdr": "WDR / HDR",
    "illuminator_type": "Illuminator type",
    "white_light_range_m": "White-light illumination range",
    "illuminator_wavelength_nm": "Illuminator wavelength",
    "smart_illumination": "Smart IR / smart light",
    "image_enhancements": "Image enhancement",
    "privacy_masking": "Privacy masking",
    "codec": "Video codec",
    "smart_codec": "Smart codec",
    "bitrate_control": "Bitrate control",
    "video_bitrate": "Video bitrate",
    "main_stream": "Main stream",
    "sub_stream": "Sub stream",
    "third_stream": "Third stream",
    "ethernet_interface": "Ethernet interface",
    "network_protocols": "Network protocols",
    "onvif": "ONVIF",
    "standards_api": "CGI / SDK / API",
    "live_view_users": "Concurrent live-view users",
    "cyber_security": "Cyber security",
    "internal_storage": "Internal (edge) storage",
    "network_storage": "Network storage",
    "basic_analytics": "Basic analytics",
    "ai_analytics": "AI analytics",
    "target_classification": "Human / vehicle classification",
    "perimeter_protection": "Perimeter protection",
    "object_tracking": "Object tracking",
    "face_detection": "Face detection",
    "anpr_lpr": "ANPR / LPR",
    "people_counting": "People counting",
    "heat_map": "Heat map",
    "audio_compression": "Audio compression",
    "built_in_audio": "Built-in audio hardware",
    "audio_io": "Audio I/O",
    "alarm_io": "Alarm I/O",
    "power_supply": "Power supply",
    "power_consumption": "Power consumption",
    "temperature_min_c": "Min. operating temperature",
    "temperature_max_c": "Max. operating temperature",
    "humidity": "Humidity",
    "ip_rating": "IP rating",
    "ik_rating": "IK rating",
    "surge_protection": "Surge protection",
    "housing_material": "Housing material",
    "dimensions": "Dimensions",
    "weight": "Weight",
    "certificates": "Certificates",
}

# Section title (TR, EN) + the field keys it draws from. The optic-performance
# section (key "__dori__") is filled separately from computed results, not
# from the brochure dict -- see _dori_items().
_SECTIONS: List[Tuple[str, str, List[str]]] = [
    ("1. SENSÖR, LENS VE GENEL DONANIM", "1. SENSOR, LENS AND GENERAL HARDWARE", [
        "sensor_name", "resolution_name", "lens_type", "aperture_f_number", "lens_mount",
        "max_fps", "shutter_speed", "day_night", "wdr", "sn_ratio_db",
        "pan_range_deg", "tilt_range_deg", "rotate_range_deg",
    ]),
    ("2. OPTİK PERFORMANS (EN 62676-4 DORI)", "2. OPTICAL PERFORMANCE (EN 62676-4 DORI)", ["__dori__"]),
    ("3. GECE GÖRÜŞ VE AYDINLATMA", "3. LOW-LIGHT AND ILLUMINATION", [
        "ir_range_m", "min_lux", "illuminator_type", "white_light_range_m",
        "illuminator_wavelength_nm", "smart_illumination", "image_enhancements",
    ]),
    ("4. GÖRÜNTÜ İŞLEME VE ANALİTİK", "4. VIDEO PROCESSING AND ANALYTICS", [
        "privacy_masking", "codec", "smart_codec", "bitrate_control", "video_bitrate",
        "main_stream", "sub_stream", "third_stream", "basic_analytics", "ai_analytics",
        "target_classification", "perimeter_protection", "object_tracking",
        "face_detection", "anpr_lpr", "people_counting", "heat_map",
    ]),
    ("5. AĞ, GÜVENLİK VE ENTEGRASYON", "5. NETWORK, SECURITY AND INTEGRATION", [
        "ethernet_interface", "network_protocols", "onvif", "standards_api",
        "live_view_users", "cyber_security", "internal_storage", "network_storage",
        "audio_compression", "built_in_audio", "audio_io", "alarm_io",
    ]),
    ("6. MEKANİK, ÇEVRESEL VE GÜÇ", "6. MECHANICAL, ENVIRONMENTAL AND POWER", [
        "power_supply", "power_consumption", "temperature_min_c", "temperature_max_c",
        "humidity", "ip_rating", "ik_rating", "surge_protection", "housing_material",
        "dimensions", "weight",
    ]),
    ("7. SERTİFİKASYON", "7. CERTIFICATION", ["certificates"]),
]


def _field_label(key: str, lang: str) -> str:
    for fkey, tr, en in _CORE_FIELD_LABELS:
        if fkey == key:
            return tr if lang == "tr" else en
    tr_by_key = dict(camera_db_extended_field_specs())
    tr = tr_by_key.get(key, key)
    if lang == "tr":
        return tr
    return _EXT_FIELD_LABELS_EN.get(key, tr)


def _core_value(key: str, camera: CameraConfig) -> Any:
    if key == "sensor_name":
        return camera.sensor_name
    if key == "resolution_name":
        return camera.resolution_name
    if key == "ir_range_m":
        # BUGFIX: 0 is the camera library's "not applicable" sentinel for
        # thermal models (passive sensor, no active IR illuminator) -- see
        # e.g. config.DEFAULT_CAMERA_LIBRARY's "ASELSAN UMA T10" entry.
        # Emitting "en az 0 m olmalıdır" as a requirement is nonsensical.
        return f"{camera.ir_range_m:g} m" if camera.ir_range_m > 0 else None
    if key == "min_lux":
        return f"{camera.min_lux:g} lux" if camera.min_lux > 0 else None
    return None


_CORE_FIELD_KEYS = {fkey for fkey, _tr, _en in _CORE_FIELD_LABELS}


def _field_value(key: str, camera: CameraConfig, model: Dict[str, Any]) -> Optional[str]:
    if key in _CORE_FIELD_KEYS:
        # BUGFIX: these are CameraConfig's own authoritative (applied) values
        # -- never fall back to the raw library dict's same-named field,
        # which still carries the library's "0 = not applicable" sentinel
        # for e.g. a thermal camera's ir_range_m/min_lux (has_camera_db_value
        # treats numeric 0 as present, so that fallback used to leak
        # "en az 0 m" requirements back in).
        core = _core_value(key, camera)
        return str(core) if core is not None else None
    value = model.get(key)
    return str(value).strip() if has_camera_db_value(value) else None


def _lens_requirement(camera: CameraConfig, lang: str) -> str:
    if lang == "tr":
        return (
            f"Odak uzaklığı {camera.focal_min_mm:g}–{camera.focal_max_mm:g} mm "
            "aralığında (veya dengi/üstü menzilde) olmalıdır."
        )
    return (
        f"Focal length shall cover {camera.focal_min_mm:g}–{camera.focal_max_mm:g} mm "
        "(or an equivalent/wider range)."
    )


def _dori_items(results: List[OpticResult], lang: str) -> List[str]:
    """One sentence per DORI tier that this camera's *own current setup*
    (as last calculated) actually reaches -- never a tier it can't achieve.
    """
    items: List[str] = []
    seen: set = set()
    for result in results or []:
        mode_label = result.rows[0].mode if result.rows else ""
        for ppm, tr_label, en_label in _DORI_TIERS:
            row = next((r for r in result.rows if abs(r.ppm - ppm) < 1e-6), None)
            if row is None or row.status != "Aktif" or row.ground_distance_m <= 0:
                continue
            dedup_key = (mode_label, ppm, round(row.ground_distance_m, 1))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            dist = row.ground_distance_m
            if lang == "tr":
                items.append(
                    f"Kamera, {mode_label.lower()} lens konumunda en az {dist:.0f} metre "
                    f"mesafede en az {ppm:g} px/m piksel yoğunluğu ile "
                    f"{tr_label} görevini EN 62676-4 standardına uygun şekilde "
                    "yerine getirmelidir."
                )
            else:
                mode_en = _MODE_EN.get(mode_label, mode_label.lower())
                items.append(
                    f"In {mode_en} lens position, the camera shall provide at least "
                    f"{ppm:g} px/m pixel density at {dist:.0f} m, satisfying the "
                    f"{en_label} task per EN 62676-4."
                )
    return items


def build_requirement_sections(
    camera: CameraConfig,
    model: Dict[str, Any],
    results: List[OpticResult],
    lang: str = "tr",
) -> List[Tuple[str, List[str]]]:
    """Ordered (section_title, [requirement sentence, ...]) pairs, skipping
    sections that end up with nothing to say (missing brochure data / no
    achievable DORI tier at the current setup)."""
    sections: List[Tuple[str, List[str]]] = []
    for title_tr, title_en, keys in _SECTIONS:
        title = title_tr if lang == "tr" else title_en
        items: List[str] = []
        if keys == ["__dori__"]:
            items = _dori_items(results, lang)
        else:
            for key in keys:
                value = _field_value(key, camera, model)
                if value is None:
                    continue
                label = _field_label(key, lang)
                if lang == "tr":
                    items.append(f"{label}: en az {value} (veya dengi/üstü) sağlanmalıdır.")
                else:
                    items.append(f"{label}: at least {value} (or equivalent/better) shall be provided.")
                if key == "resolution_name":
                    items.append(_lens_requirement(camera, lang))
        if items:
            sections.append((title, items))
    return sections


def _overview_text(model: Dict[str, Any], lang: str) -> Optional[str]:
    overview = model.get("overview") or model.get("brochure_title")
    if not has_camera_db_value(overview):
        return None
    prefix = "Ürün özeti (üretici verisi): " if lang == "tr" else "Product overview (manufacturer data): "
    return prefix + str(overview).strip()


def write_requirement_docx(
    path: str,
    camera: CameraConfig,
    model: Dict[str, Any],
    results: List[OpticResult],
    lang: str = "tr",
    project_name: str = "",
    prepared_by: str = "Harun KAHRAMAN — Ürün Teknik Yöneticisi",
) -> None:
    """Write one Word (.docx) draft requirements spec for ``camera``."""
    if not _DOCX_AVAILABLE:
        raise RuntimeError(
            "Word (.docx) çıktısı için 'python-docx' kütüphanesi gerekli "
            "(pip install python-docx)."
            if lang == "tr" else
            "Word (.docx) output requires the 'python-docx' package "
            "(pip install python-docx)."
        )

    is_tr = lang == "tr"
    now_str = datetime.now().strftime("%d.%m.%Y")
    doc = docx.Document()

    title = "TASLAK TEKNİK ŞARTNAME" if is_tr else "DRAFT TECHNICAL SPECIFICATION"
    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle = f"{camera.model_name}" + (f" — {camera.name}" if camera.name else "")
    sub = doc.add_paragraph(subtitle)
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.runs[0].bold = True
    sub.runs[0].font.size = Pt(13)

    meta = doc.add_table(rows=0, cols=2)
    meta.style = "Light List Accent 1"
    rows = [
        ("Proje" if is_tr else "Project", project_name or "—"),
        ("Kamera modeli" if is_tr else "Camera model", camera.model_name),
        ("Tarih" if is_tr else "Date", now_str),
        ("Hazırlayan" if is_tr else "Prepared by", prepared_by),
    ]
    for key, val in rows:
        cells = meta.add_row().cells
        cells[0].text = key
        cells[1].text = val

    doc.add_paragraph("")
    note = (
        "Bu doküman, seçilen kameranın üretici broşür verisi ve EN 62676-4 "
        "DORI optik motoruyla hesaplanan mevcut kurulum performansı esas "
        "alınarak otomatik üretilmiş bir TASLAKTIR; ihaleye/teklife esas "
        "şartname olarak kullanılmadan önce teknik ekip tarafından "
        "incelenmeli ve onaylanmalıdır."
        if is_tr else
        "This document is an automatically generated DRAFT, based on the "
        "selected camera's manufacturer brochure data and the EN 62676-4 "
        "DORI optic engine's calculation for its current setup; it must be "
        "reviewed and approved by the technical team before use as a "
        "binding tender/bid specification."
    )
    note_p = doc.add_paragraph(note)
    note_p.runs[0].italic = True

    overview = _overview_text(model, lang)
    if overview:
        doc.add_paragraph("")
        doc.add_paragraph(overview)

    sections = build_requirement_sections(camera, model, results, lang)
    for title, items in sections:
        doc.add_heading(title, level=1)
        for item in items:
            doc.add_paragraph(item, style="List Number")

    doc.add_paragraph("")
    sig = doc.add_table(rows=1, cols=2)
    sig.cell(0, 0).text = ("Hazırlayan / İmza" if is_tr else "Prepared by / Signature") + "\n\n\n______________________"
    sig.cell(0, 1).text = ("Tarih" if is_tr else "Date") + f"\n\n\n{now_str}"

    doc.save(path)
