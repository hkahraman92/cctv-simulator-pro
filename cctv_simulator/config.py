import os
import sys
import json
from pathlib import Path

APP_NAME = "CCTV Dual View Simulator"
CAMERA_LIBRARY_FILENAME = "camera_library_from_excel.json"
SETTINGS_FILENAME = "admin_settings.json"


def get_admin_password() -> str:
    path = user_data_dir() if is_frozen_app() else Path(__file__).resolve().parent.parent
    file_path = path / SETTINGS_FILENAME
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return data.get("admin_password", "admin")
        except Exception:
            pass
    return "admin"


def set_admin_password(new_pass: str):
    path = user_data_dir() if is_frozen_app() else Path(__file__).resolve().parent.parent
    file_path = path / SETTINGS_FILENAME
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"admin_password": new_pass}
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))


def app_resource_dir():
    if is_frozen_app():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    # outputs/cctv_simulator/config.py -> root outputs dir
    return Path(__file__).resolve().parent.parent


def resource_path(filename):
    return app_resource_dir() / filename


def user_data_dir():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".cctv_dual_view_simulator"


def editable_camera_library_path():
    if not is_frozen_app():
        return Path(__file__).resolve().parent.parent / CAMERA_LIBRARY_FILENAME
    target = user_data_dir() / CAMERA_LIBRARY_FILENAME
    if not target.exists():
        bundled = resource_path(CAMERA_LIBRARY_FILENAME)
        target.parent.mkdir(parents=True, exist_ok=True)
        if bundled.exists():
            target.write_bytes(bundled.read_bytes())
        else:
            target.write_text("{}", encoding="utf-8")
    return target


def configure_tk_paths():
    if is_frozen_app():
        return
    base_python_dir = Path(sys.base_prefix)
    tcl_dir = base_python_dir / "tcl" / "tcl8.6"
    tk_dir = base_python_dir / "tcl" / "tk8.6"
    if tcl_dir.exists():
        os.environ.setdefault("TCL_LIBRARY", str(tcl_dir))
    if tk_dir.exists():
        os.environ.setdefault("TK_LIBRARY", str(tk_dir))


SENSOR_DIMS_MM = {
    "1/3\"": (4.80, 3.60),
    "1/2.8\"": (5.60, 4.20),
    "1/2.7\"": (5.80, 4.35),
    "1/2\"": (6.40, 4.80),
    "1/1.8\"": (7.20, 5.40),
    "1/1.2\"": (10.10, 7.58),
    "1\"": (12.80, 9.60),
    "LWIR 640x512 (12µm)": (7.68, 6.144),
    "LWIR 640x512 (17µm)": (10.88, 8.704),
    "LWIR 1280x1024 (12µm)": (15.36, 12.288),
    "LWIR 384x288 (12µm)": (4.608, 3.456),
    "LWIR 384x288 (17µm)": (6.528, 4.896),
    "MWIR 1280x1024 (15µm)": (19.20, 15.36),
}

RESOLUTIONS = {
    "2 MP (1080p - 1920x1080)": (1920, 1080),
    "4 MP (2K - 2688x1520)": (2688, 1520),
    "5 MP (2592x1944)": (2592, 1944),
    "8 MP (4K - 3840x2160)": (3840, 2160),
    "12 MP (4000x3000)": (4000, 3000),
    "HD Termal (1280x1024)": (1280, 1024),
    "LWIR (640x512)": (640, 512),
    "LWIR (384x288)": (384, 288),
    "MWIR (1280x1024)": (1280, 1024),
}

DEFAULT_CAMERA_LIBRARY = {
    "Özel kamera": {},
    "ASELSAN UMA T10 (35-350mm Zoom Termal Kamera)": {
        "sensor_name": "LWIR 1280x1024 (12µm)",
        "resolution_name": "HD Termal (1280x1024)",
        "focal_min_mm": 35.0,
        "focal_max_mm": 350.0,
        "pole_height_m": 8.0,
        "tilt_deg": 0.0,
        "target_height_m": 1.8,
        "ir_range_m": 0.0,
        "min_lux": 0.0,
        "kamera_tipi": "Termal / HD Uzun Menzil PTZ Zoom",
        "kullanim_amaci": "Kritik Tesis Güvenliği, Sınır Hattı Gözetleme, Uzun Menzil Erken Uyarı",
        "ozet_paragraf": "ASELSAN UMA T10 1280x1024 HD 12µm LWIR sensörlü, 35-350mm 10x sürekli optik zoomlu termal kamera. Yüksek çözünürlüklü uzun menzil gözetleme.",
        "one_cikan_ozellikler": "1280x1024 SXGA HD VOx 12µm, 35-350mm 10x Zoom, NETD < 40mK, 17.5km Johnson Tespit Menzili",
    },
    "ASELSAN UMA T5 (25-225mm Zoom Termal Kamera)": {
        "sensor_name": "LWIR 640x512 (12µm)",
        "resolution_name": "LWIR (640x512)",
        "focal_min_mm": 25.0,
        "focal_max_mm": 225.0,
        "pole_height_m": 6.0,
        "tilt_deg": 0.0,
        "target_height_m": 1.8,
        "ir_range_m": 0.0,
        "min_lux": 0.0,
        "kamera_tipi": "Termal / PTZ Zoom",
        "kullanim_amaci": "Çevre Güvenliği, Sınır / Tesis Gözetleme, Uzun Menzil DRI",
        "ozet_paragraf": "ASELSAN UMA T5 640x512 12µm soğutmasız LWIR sensörlü, 25-225mm sürekli optik zoom termal kamera. 4.4km insan, 5.6km araç tespiti.",
        "one_cikan_ozellikler": "640x512 VOx 12µm, 25-225mm Motorize Sürekli Optik Zoom, NETD < 40mK, Johnson DRI Uyumlu, Algoritma VCA Desteği",
    },
    "ASELSAN UMA Fix 55mm Termal Kamera": {
        "sensor_name": "LWIR 640x512 (12µm)",
        "resolution_name": "LWIR (640x512)",
        "focal_min_mm": 55.0,
        "focal_max_mm": 55.0,
        "pole_height_m": 4.0,
        "tilt_deg": 2.0,
        "target_height_m": 1.8,
        "ir_range_m": 0.0,
        "min_lux": 0.0,
        "kamera_tipi": "Termal / Sabit Bullet",
        "kullanim_amaci": "Çevre Güvenliği, Tel Örgü Hattı, Orta Menzil Koridor Gözetleme",
        "ozet_paragraf": "ASELSAN UMA Fix 640x512 12µm LWIR sensörlü, 55mm sabit odaklı termal kamera. 1km+ insan tespiti ve çevre teli izleme.",
        "one_cikan_ozellikler": "640x512 12µm, 55mm Sabit Lens, 1km İnsan Algılama, IP67 / IK10",
    },
    "Genel 2MP varifokal 2.8-12mm": {
        "sensor_name": "1/2.8\"",
        "resolution_name": "2 MP (1080p - 1920x1080)",
        "focal_min_mm": 2.8,
        "focal_max_mm": 12.0,
        "ir_range_m": 30.0,
        "min_lux": 0.01,
    },
    "Genel 4MP varifokal 2.8-12mm": {
        "sensor_name": "1/2.8\"",
        "resolution_name": "4 MP (2K - 2688x1520)",
        "focal_min_mm": 2.8,
        "focal_max_mm": 12.0,
        "ir_range_m": 40.0,
        "min_lux": 0.005,
    },
    "Genel 8MP geniş açı 2.8mm": {
        "sensor_name": "1/2.7\"",
        "resolution_name": "8 MP (4K - 3840x2160)",
        "focal_min_mm": 2.8,
        "focal_max_mm": 2.8,
        "ir_range_m": 30.0,
        "min_lux": 0.01,
    },
    "Genel 8MP tele 5-50mm": {
        "sensor_name": "1/1.8\"",
        "resolution_name": "8 MP (4K - 3840x2160)",
        "focal_min_mm": 5.0,
        "focal_max_mm": 50.0,
        "ir_range_m": 80.0,
        "min_lux": 0.003,
    },
    "Plaka odaklı 4MP 8-32mm": {
        "sensor_name": "1/1.8\"",
        "resolution_name": "4 MP (2K - 2688x1520)",
        "focal_min_mm": 8.0,
        "focal_max_mm": 32.0,
        "ir_range_m": 60.0,
        "min_lux": 0.002,
    },
}

TURKISH_TRANSLATION = str.maketrans(
    {
        "ç": "c",
        "Ç": "C",
        "ğ": "g",
        "Ğ": "G",
        "ı": "i",
        "İ": "I",
        "ö": "o",
        "Ö": "O",
        "ş": "s",
        "Ş": "S",
        "ü": "u",
        "Ü": "U",
    }
)
