import json
from pathlib import Path
from typing import Dict, Any, Tuple, List
from .config import editable_camera_library_path, DEFAULT_CAMERA_LIBRARY


def read_camera_library_json() -> Dict[str, Any]:
    path = editable_camera_library_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_camera_library_json(data: Dict[str, Any]):
    path = editable_camera_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def load_camera_library() -> Dict[str, Any]:
    library = dict(DEFAULT_CAMERA_LIBRARY)
    extra_data = read_camera_library_json()
    for name, model in extra_data.items():
        if isinstance(model, dict):
            if name in library and isinstance(library[name], dict):
                merged = dict(library[name])
                merged.update(model)
                library[name] = merged
            else:
                library[name] = model
    return library


def camera_db_extended_field_specs() -> Tuple[Tuple[str, str], ...]:
    return (
        ("usage_purpose", "Kullanım amacı"),
        ("overview", "Özet paragraf"),
        ("highlights", "Öne çıkan özellikler"),
        ("max_fps", "Maks. fps"),
        ("lens_type", "Lens tipi"),
        ("aperture_f_number", "Apertür"),
        ("horizontal_fov_deg", "Yatay FOV"),
        ("vertical_fov_deg", "Dikey FOV"),
        ("diagonal_fov_deg", "Diyagonal FOV"),
        ("lens_mount", "Lens montajı"),
        ("pan_range_deg", "Pan aralığı"),
        ("tilt_range_deg", "Tilt aralığı"),
        ("rotate_range_deg", "Rotate aralığı"),
        ("dori_detect_m", "DORI Detect"),
        ("dori_observe_m", "DORI Observe"),
        ("dori_recognize_m", "DORI Recognize"),
        ("dori_identify_m", "DORI Identify"),
        ("shutter_speed", "Enstantane"),
        ("day_night", "Day & Night"),
        ("sn_ratio_db", "S/N oranı"),
        ("wdr", "WDR / HDR"),
        ("illuminator_type", "Aydınlatıcı tipi"),
        ("white_light_range_m", "Beyaz ışık menzili"),
        ("illuminator_wavelength_nm", "Dalga boyu"),
        ("smart_illumination", "Smart IR / Light"),
        ("image_enhancements", "Görüntü iyileştirme"),
        ("privacy_masking", "Privacy masking"),
        ("codec", "Kodlama / codec"),
        ("smart_codec", "Smart codec"),
        ("bitrate_control", "Bitrate kontrolü"),
        ("video_bitrate", "Video bitrate"),
        ("main_stream", "Ana akış"),
        ("sub_stream", "Alt akış"),
        ("third_stream", "Üçüncü akış"),
        ("ethernet_interface", "Ethernet"),
        ("network_protocols", "Ağ protokolleri"),
        ("onvif", "ONVIF"),
        ("standards_api", "CGI / SDK / API"),
        ("live_view_users", "Eşzamanlı kullanıcı"),
        ("cyber_security", "Siber güvenlik"),
        ("internal_storage", "Dahili depolama"),
        ("network_storage", "Ağ depolama"),
        ("basic_analytics", "Temel analitikler"),
        ("ai_analytics", "AI analitikleri"),
        ("target_classification", "İnsan / araç sınıflandırma"),
        ("perimeter_protection", "Çevre güvenliği"),
        ("object_tracking", "Nesne takibi"),
        ("face_detection", "Yüz algılama"),
        ("anpr_lpr", "ANPR / LPR"),
        ("people_counting", "Kişi sayma"),
        ("heat_map", "Heat map"),
        ("analytics_notes", "Analitik / YZ notu"),
        ("audio_compression", "Ses sıkıştırma"),
        ("built_in_audio", "Dahili ses donanımı"),
        ("audio_io", "Audio I/O"),
        ("alarm_io", "Alarm I/O"),
        ("power_supply", "Güç beslemesi"),
        ("power_consumption", "Güç tüketimi"),
        ("temperature_min_c", "Sıcaklık min"),
        ("temperature_max_c", "Sıcaklık max"),
        ("humidity", "Nem"),
        ("ip_rating", "IP koruma"),
        ("ik_rating", "IK koruma"),
        ("surge_protection", "Aşırı gerilim"),
        ("housing_material", "Kasa malzemesi"),
        ("dimensions", "Boyutlar"),
        ("weight", "Ağırlık"),
        ("mechanical_drawings", "Boyutsal çizim"),
        ("certificates", "Sertifikalar"),
        ("notes", "Genel not"),
        ("raw_sensor", "Ham sensör"),
        ("raw_resolution", "Ham çözünürlük"),
        ("raw_focal", "Ham lens"),
        ("raw_ir", "Ham IR"),
        ("raw_light_sensitivity", "Ham ışık hassasiyeti"),
    )


def has_camera_db_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def camera_db_missing_fields(model: Dict[str, Any], fallback_name: str = "") -> Tuple[List[str], List[str]]:
    required = [
        ("model_name", "Model adı"),
        ("sensor_name", "Sensör"),
        ("resolution_name", "Çözünürlük"),
        ("focal_min_mm", "Min odak"),
        ("focal_max_mm", "Max odak"),
        ("ir_range_m", "IR mesafesi"),
        ("min_lux", "Minimum lux"),
    ]
    important = [
        ("stock_code", "Stok kodu"),
        ("product_name", "Ürün adı"),
        ("camera_type", "Kamera tipi"),
    ] + list(camera_db_extended_field_specs())

    missing_required = []
    for key, label in required:
        if key == "model_name" and fallback_name:
            continue
        if not has_camera_db_value(model.get(key)):
            missing_required.append(label)
    missing_important = [label for key, label in important if not has_camera_db_value(model.get(key))]
    return missing_required, missing_important


def model_descriptor(model_name: str, model: Dict[str, Any]) -> str:
    keys = (
        "model_name",
        "stock_code",
        "product_name",
        "brochure_title",
        "sensor_name",
        "resolution_name",
        "camera_type",
        "source_sheet",
    )
    parts = [model_name]
    for key in keys:
        val = model.get(key)
        if val and isinstance(val, str):
            parts.append(val)
    return " ".join(parts).casefold()
