import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, Any, List, Tuple, Optional
from .database import camera_db_extended_field_specs, has_camera_db_value


def build_compliance_prompt(spec_text: str, camera_library: Dict[str, Any]) -> str:
    camera_summary = camera_library_for_prompt(camera_library)
    compact_spec = spec_text[:16000]
    return (
        "Sen CCTV teknik şartname analiz uzmanısın. Gelen şartname metninden ölçülebilir kamera "
        "isterlerini çıkar, verilen kamera kütüphanesine göre uygunluk matrisi oluştur ve en uygun "
        "kameraları öner. Şartnamede birden fazla kamera tipi/profili varsa bunları ayrı profiller olarak "
        "ayır: örneğin sabit bullet, dome, PTZ/speed dome, plaka, iç ortam, dış ortam, termal vb. "
        "Kamera tipi yorumunda zoom/focus lens hareketini hareketli kamera sayma; hareketli kamera "
        "pan/tilt/PTZ/speed dome mekanik hareketi olan kameradır. "
        "Her profil için ayrı ister, ayrı matrix satırları ve ayrı model skorları üret. "
        "Sadece geçerli JSON döndür; markdown kullanma.\n\n"
        "JSON şeması:\n"
        "{\n"
        '  "profiles": [{"id": "P1", "name": "Dış ortam sabit kamera", "description": "..."}],\n'
        '  "requirements": [{"id": "R1", "profile_id": "P1", "profile_name": "Dış ortam sabit kamera", '
        '"category": "resolution|ir|lux|lens|analytics|environment|other", "requirement": "...", "weight": 1}],\n'
        '  "matrix": [{"profile_id": "P1", "profile_name": "Dış ortam sabit kamera", '
        '"requirement_id": "R1", "requirement": "...", "camera_model": "...", '
        '"status": "Uyumlu|Kısmi|Uyumsuz", "evidence": "..."}],\n'
        '  "camera_scores": [{"profile_id": "P1", "profile_name": "Dış ortam sabit kamera", '
        '"camera_model": "...", "score": 0, "verdict": "Uyumlu|Kısmi|Uyumsuz", '
        '"notes": "..."}],\n'
        '  "recommendation": "..."\n'
        "}\n\n"
        f"KAMERA KÜTÜPHANESİ:\n{json.dumps(camera_summary, ensure_ascii=False)}\n\n"
        f"ŞARTNAME:\n{compact_spec}"
    )


def camera_library_for_prompt(camera_library: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for name, model in camera_library.items():
        if not model:
            continue
        row = {
            "model_name": name,
            "stock_code": model.get("stock_code", ""),
            "sensor": model.get("sensor_name", ""),
            "resolution": model.get("resolution_name", ""),
            "focal_min_mm": model.get("focal_min_mm", ""),
            "focal_max_mm": model.get("focal_max_mm", ""),
            "ir_range_m": model.get("ir_range_m", ""),
            "min_lux": model.get("min_lux", ""),
            "source": model.get("source_sheet", ""),
        }
        for key, _label in camera_db_extended_field_specs():
            value = model.get(key, "")
            if has_camera_db_value(value):
                row[key] = value
        rows.append(row)
    return rows


def gemini_response_text(response_data: Dict[str, Any]) -> str:
    candidates = response_data.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini cevabında candidate yok.")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON nesnesi bulunamadı.")
    return cleaned[start : end + 1]


def run_gemini_in_thread(
    parent_win: tk.Widget,
    api_request: urllib.request.Request,
    on_success: Any,
    on_failure: Any
):
    """Gemini API çağrısını arka plan thread'inde çalıştırır; ilerleme ve 429 retry yönetimi sunar."""
    progress_win = tk.Toplevel(parent_win)
    progress_win.title("Gemini Analizi")
    progress_win.geometry("400x150")
    progress_win.resizable(False, False)
    progress_win.grab_set()

    status_var = tk.StringVar(value="Gemini API'ye bağlanılıyor, lütfen bekleyin...")
    tk.Label(progress_win, textvariable=status_var, wraplength=360, pady=12).pack()
    pb = ttk.Progressbar(progress_win, mode="indeterminate", length=340)
    pb.pack(pady=(0, 10))
    pb.start(12)

    cancelled = [False]
    result_container = [None]
    error_container = [None]

    def cancel():
        cancelled[0] = True
        if progress_win.winfo_exists():
            progress_win.destroy()

    ttk.Button(progress_win, text="İptal", command=cancel).pack()
    progress_win.protocol("WM_DELETE_WINDOW", cancel)

    _RETRY_WAITS = [15, 30, 60]
    _MAX_RETRIES = len(_RETRY_WAITS)

    def worker():
        for attempt in range(_MAX_RETRIES + 1):
            if cancelled[0]:
                return
            try:
                with urllib.request.urlopen(api_request, timeout=90) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                text = gemini_response_text(response_data)
                result_container[0] = json.loads(extract_json_object(text))
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    error_container[0] = Exception(
                        "Model bulunamadı (HTTP 404).\n\n"
                        "Girdiğiniz model adı Google API'sinde mevcut değil.\n"
                        "Geçerli modeller: gemini-2.0-flash, gemini-1.5-flash, "
                        "gemini-1.5-pro, gemini-2.0-flash-lite\n\n"
                        "Lütfen model adını kontrol edin."
                    )
                    return
                if exc.code == 403:
                    error_container[0] = Exception(
                        "API key geçersiz veya yetkisiz (HTTP 403).\n\n"
                        "API anahtarınızı kontrol edin veya\n"
                        "https://aistudio.google.com/apikey adresinden yeni bir key oluşturun."
                    )
                    return
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    wait_secs = _RETRY_WAITS[attempt]
                    for remaining in range(wait_secs, 0, -1):
                        if cancelled[0]:
                            return
                        progress_win.after(
                            0,
                            lambda r=remaining, a=attempt: status_var.set(
                                f"API kota limiti (429) — Deneme {a + 1}/{_MAX_RETRIES}. "
                                f"{r} saniye sonra tekrar denenecek..."
                            ),
                        )
                        time.sleep(1)
                    progress_win.after(
                        0,
                        lambda a=attempt: status_var.set(
                            f"Tekrar deneniyor ({a + 2}/{_MAX_RETRIES + 1})..."
                        ),
                    )
                else:
                    error_container[0] = exc
                    return
            except Exception as exc:
                error_container[0] = exc
                return
        error_container[0] = Exception(
            f"API kota limiti ({_MAX_RETRIES} denemede de 429 hatası aldı). "
            "Birkaç dakika bekleyip tekrar deneyin veya API kotanızı kontrol edin."
        )

    def poll():
        if cancelled[0]:
            return
        if worker_thread.is_alive():
            progress_win.after(250, poll)
            return
        if not progress_win.winfo_exists():
            return
        progress_win.destroy()
        if cancelled[0]:
            return
        if error_container[0] is not None:
            messagebox.showwarning(
                "Gemini analizi başarısız",
                f"Gemini cevabı alınamadı veya JSON parse edilemedi:\n{error_container[0]}"
                "\n\nKurallı analiz çalıştırılıyor.",
            )
            on_failure()
        elif result_container[0] is not None:
            on_success(result_container[0])

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    progress_win.after(250, poll)


def split_spec_profiles(spec_text: str) -> List[Dict[str, Any]]:
    raw_lines = [line.strip() for line in spec_text.splitlines() if line.strip()]
    fixed_lines = []
    moving_lines = []
    common_lines = []
    for line in raw_lines:
        normalized = line.casefold()
        fixed_hit = bool(re.search(r"\bsabit\b|fixed|bullet", normalized))
        moving_hit = bool(re.search(r"\bhareketli\b|\bptz\b|speed\s*dome|pan\s*/?\s*tilt|pan\s+tilt", normalized))
        if fixed_hit and not moving_hit:
            fixed_lines.append(line)
        elif moving_hit and not fixed_hit:
            moving_lines.append(line)
        else:
            common_lines.append(line)

    detected_profiles = []
    common_text = "\n".join(common_lines)
    if fixed_lines:
        detected_profiles.append(
            {
                "id": "P1",
                "name": "Sabit kamera isterleri",
                "text": "\n".join([common_text, "\n".join(fixed_lines)]).strip(),
                "expected_type": "fixed",
            }
        )
    if moving_lines:
        detected_profiles.append(
            {
                "id": f"P{len(detected_profiles) + 1}",
                "name": "Hareketli / PTZ kamera isterleri",
                "text": "\n".join([common_text, "\n".join(moving_lines)]).strip(),
                "expected_type": "moving",
            }
        )
    if detected_profiles and (fixed_lines or moving_lines):
        return detected_profiles

    lines = [line.strip() for line in spec_text.splitlines()]
    sections = []
    current_title = "Genel kamera isterleri"
    current_lines = []
    heading_pattern = re.compile(
        r"^(?:kamera\s*)?(?:tip|type|profil|profile|grup|group)\s*[-:]?\s*\d+|"
        r"^(?:sabit|bullet|dome|ptz|speed\s*dome|hareketli|plaka|lpr|anpr|iç ortam|dış ortam).{0,80}kamera",
        re.IGNORECASE,
    )
    for line in lines:
        if heading_pattern.search(line) and len(line) <= 120:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))
    if not sections:
        sections = [("Genel kamera isterleri", spec_text)]
    profiles = []
    for index, (title, text) in enumerate(sections, start=1):
        clean_title = title.strip(" :-") or f"Kamera profili {index}"
        lowered = clean_title.casefold()
        expected_type = None
        if re.search(r"\bsabit\b|fixed|bullet", lowered):
            expected_type = "fixed"
        elif re.search(r"\bhareketli\b|\bptz\b|speed\s*dome|pan\s*/?\s*tilt|pan\s+tilt", lowered):
            expected_type = "moving"
        profiles.append({"id": f"P{index}", "name": clean_title[:80], "text": text, "expected_type": expected_type})
    return profiles


def extract_lens_ranges_from_text(spec_text: str) -> List[Tuple[float, float]]:
    ranges = []
    seen = set()
    patterns = [
        r"(\d+(?:[.,]\d+)?)\s*mm\s*(?:ile|ila|-|–|—)\s*(?:minimum\s*|en\s+az\s*)?(\d+(?:[.,]\d+)?)\s*mm",
        r"(\d+(?:[.,]\d+)?)\s*(?:-|–|—|ile|ila)\s*(?:minimum\s*|en\s+az\s*)?(\d+(?:[.,]\d+)?)\s*mm",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, spec_text, flags=re.IGNORECASE):
            low = float(match.group(1).replace(",", "."))
            high = float(match.group(2).replace(",", "."))
            if high < low:
                low, high = high, low
            if high <= 0 or high > 1000:
                continue
            key = (round(low, 3), round(high, 3))
            if key in seen:
                continue
            seen.add(key)
            ranges.append((low, high))
    return ranges


def extract_rule_requirements(
    spec_text: str,
    profile_id: str = "P1",
    profile_name: str = "Genel kamera isterleri",
    expected_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    text = spec_text.lower()
    if expected_type is None:
        profile_key = f"{profile_name}\n{text}".casefold()
        if re.search(r"\bsabit\b|fixed|bullet", profile_key):
            expected_type = "fixed"
        elif re.search(r"\bhareketli\b|\bptz\b|speed\s*dome|pan\s*/?\s*tilt|pan\s+tilt", profile_key):
            expected_type = "moving"

    requirements = []
    req_id = 1
    if expected_type:
        type_label = "Sabit kamera tipi" if expected_type == "fixed" else "Hareketli / PTZ kamera tipi"
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "camera_type",
                "requirement": type_label,
                "value": expected_type,
                "weight": 4,
            }
        )
        req_id += 1

    lens_ranges = extract_lens_ranges_from_text(spec_text)
    if lens_ranges:
        mode = "zoom_cover" if expected_type == "moving" else "any_cover"
        if mode == "zoom_cover":
            target = max(lens_ranges, key=lambda item: item[1])
            lens_text = f"Hareketli kamerada lens/zoom aralığı en az {target[0]:g}-{target[1]:g} mm"
        else:
            lens_text = "Sabit kamerada lens aralığı " + " veya ".join(
                f"{low:g}-{high:g} mm" for low, high in lens_ranges
            )
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "lens_range",
                "requirement": lens_text,
                "ranges": lens_ranges,
                "mode": mode,
                "weight": 4,
            }
        )
        req_id += 1

    if re.search(r"4\s*k|3840\s*[x×]\s*2160|8\s*mp", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "resolution",
                "requirement": "En az 4K / 8MP çözünürlük",
                "weight": 3,
            }
        )
        req_id += 1
    elif re.search(r"full\s*hd|1920\s*[x×]\s*1080|2\s*mp", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "resolution",
                "requirement": "En az Full HD / 2MP çözünürlük",
                "weight": 2,
            }
        )
        req_id += 1

    ir_values = [
        float(match.group(1).replace(",", "."))
        for match in re.finditer(r"(?:(?:ir|ayd[ıi]nlatma).{0,35}?(\d+(?:[.,]\d+)?)\s*m)", text)
    ]
    if ir_values:
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "ir",
                "requirement": f"IR aydınlatma mesafesi en az {max(ir_values):g} m",
                "value": max(ir_values),
                "weight": 2,
            }
        )
        req_id += 1

    lux_values = [
        float(match.group(1).replace(",", "."))
        for match in re.finditer(r"(\d+(?:[.,]\d+)?)\s*lux", text)
    ]
    if lux_values:
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "lux",
                "requirement": f"Renkli minimum ışık hassasiyeti en fazla {min(lux_values):g} lux",
                "value": min(lux_values),
                "weight": 2,
            }
        )
        req_id += 1

    if re.search(r"\bip\s*66\b|ip66|su\s+geçirmez", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "environment_ip",
                "requirement": "Dış ortam için IP66 / su geçirmez koruma",
                "value": "IP66",
                "weight": 2,
            }
        )
        req_id += 1

    temp_values = [
        float(match.group(1).replace(",", "."))
        for match in re.finditer(r"([-+]?\d+(?:[.,]\d+)?)\s*(?:°|derece|santigrat)", text)
    ]
    if "sıcak" in text and len(temp_values) >= 2:
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "temperature",
                "requirement": f"Çalışma sıcaklığı {min(temp_values):g} ile {max(temp_values):g} °C aralığını kapsamalı",
                "value": {"min": min(temp_values), "max": max(temp_values)},
                "weight": 1,
            }
        )
        req_id += 1

    # 1. WDR / HDR
    wdr_matches = re.finditer(r"(\d{2,3})\s*db\s*(?:wdr|hdr)?|(?:wdr|hdr)", text)
    wdr_values = []
    for match in wdr_matches:
        if match.group(1):
            wdr_values.append(float(match.group(1)))
    if wdr_values:
        req_wdr = max(wdr_values)
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "wdr",
                "requirement": f"Gelişmiş dinamik aralık en az {req_wdr:g} dB WDR / HDR",
                "value": req_wdr,
                "weight": 2,
            }
        )
        req_id += 1
    elif "wdr" in text or "hdr" in text or "dinamik aralık" in text:
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "wdr",
                "requirement": "WDR / HDR (Geniş Dinamik Aralık) desteği",
                "value": 120.0,
                "weight": 2,
            }
        )
        req_id += 1

    # 2. Vandal / IK Rating
    if re.search(r"ik\s*10|ik10|vandal|darbeye\s+dayanıklı", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "environment_ik",
                "requirement": "Vandalizm / Darbeye dayanıklı IK10 koruma",
                "value": "IK10",
                "weight": 2,
            }
        )
        req_id += 1
    elif re.search(r"ik\s*0?8|ik08", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "environment_ik",
                "requirement": "En az IK08 darbe koruması",
                "value": "IK08",
                "weight": 1,
            }
        )
        req_id += 1

    # 3. FPS (Kare hızı)
    fps_match = re.search(r"(\d{2,3})\s*fps", text)
    if fps_match:
        target_fps = float(fps_match.group(1))
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "fps",
                "requirement": f"En az {target_fps:g} fps akış hızı",
                "value": target_fps,
                "weight": 2,
            }
        )
        req_id += 1

    # 4. Sensör Boyutu
    sensor_match = re.search(r"1\s*/\s*(1\.(?:2|8)|2\.(?:7|8)|3)\s*(?:inç|inch|\")?", text)
    if sensor_match:
        req_sensor = f"1/{sensor_match.group(1)}\""
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "sensor_size",
                "requirement": f"En az {req_sensor} sensör boyutu",
                "value": req_sensor,
                "weight": 2,
            }
        )
        req_id += 1

    # 5. AI / Gelişmiş Analitikler
    if re.search(r"sınır\s+ihlali|çevre\s+koruma|geçiş|perimeter|line\s*crossing|intrusion", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "analytics_ai",
                "requirement": "Çevre Güvenliği / Sınır & Bölge İhlali Analitiği",
                "type": "perimeter",
                "weight": 2,
            }
        )
        req_id += 1

    if re.search(r"insan\s*/?\s*araç|hedef\s+sınıflandırma|target\s+classification|human\s*/?\s*vehicle", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "analytics_ai",
                "requirement": "İnsan / Araç Ayrımı & Yapay Zeka Sınıflandırma",
                "type": "classification",
                "weight": 2,
            }
        )
        req_id += 1

    if re.search(r"kişi\s+sayma|insan\s+sayma|people\s+counting", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "analytics_ai",
                "requirement": "Kişi Sayma / Yoğunluk Analitiği",
                "type": "counting",
                "weight": 2,
            }
        )
        req_id += 1

    # 6. MicroSD / Depolama
    if re.search(r"sd\s*kart|microsd|dahili\s+hafıza|kart\s+yuvası|memory\s+card", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "storage",
                "requirement": "MicroSD / Dahili Hafıza Kart Yuvası Desteği",
                "weight": 1,
            }
        )
        req_id += 1

    # 7. Ses / Alarm
    if re.search(r"ses|mikrofon|audio|alarm", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "audio_alarm",
                "requirement": "Ses (Audio) / Alarm Girdi-Çıktı Arayüzü",
                "weight": 1,
            }
        )
        req_id += 1

    # 8. Codec
    if re.search(r"h\.?265\+?|smart\s*codec", text):
        requirements.append(
            {
                "id": f"{profile_id}-R{req_id}",
                "profile_id": profile_id,
                "profile_name": profile_name,
                "category": "codec",
                "requirement": "H.265 / H.265+ Yüksek Verimli Sıkıştırma",
                "weight": 1,
            }
        )
        req_id += 1

    return requirements


def rule_based_compliance(spec_text: str, camera_library: Dict[str, Any]) -> Dict[str, Any]:
    profiles = split_spec_profiles(spec_text)
    all_requirements = []
    matrix = []
    profile_rows = []

    items = [(name, model) for name, model in camera_library.items() if model]
    real_items = [(name, model) for name, model in items if model.get("stock_code")]
    compliance_cameras = real_items or items

    for profile in profiles:
        requirements = extract_rule_requirements(
            profile["text"], profile["id"], profile["name"], profile.get("expected_type")
        )
        if not requirements and len(profiles) > 1:
            requirements = extract_rule_requirements(
                spec_text, profile["id"], profile["name"], profile.get("expected_type")
            )
        all_requirements.extend(requirements)

        for model_name, model in compliance_cameras:
            if not model:
                continue
            passed = 0.0
            partial = 0.0
            not_found = 0.0
            blocker = False
            total_weight = sum(max(float(req.get("weight", 1)), 0.1) for req in requirements) or 1.0
            for requirement in requirements:
                weight = max(float(requirement.get("weight", 1)), 0.1)
                status, evidence = evaluate_rule_requirement(model_name, model, requirement)
                if status == "Uyumlu":
                    passed += weight
                elif status == "Kısmi":
                    partial += weight
                elif status == "Bulunamadı":
                    not_found += weight
                elif status == "Uyumsuz" and requirement.get("category") == "camera_type":
                    blocker = True
                matrix.append(
                    {
                        "profile_id": profile["id"],
                        "profile_name": profile["name"],
                        "requirement_id": requirement["id"],
                        "requirement": requirement["requirement"],
                        "camera_model": model_name,
                        "status": status,
                        "evidence": evidence,
                    }
                )
            score = round(((passed + partial * 0.5 + not_found * 0.15) / total_weight) * 100)
            if blocker:
                score = min(score, 40)
                verdict = "Uyumsuz"
            else:
                verdict = "Uyumlu" if score >= 82 else "Kısmi" if score >= 50 else "Uyumsuz"
            profile_rows.append(
                {
                    "profile_id": profile["id"],
                    "profile_name": profile["name"],
                    "camera_model": model_name,
                    "score": score,
                    "verdict": verdict,
                    "notes": f"{passed:g}/{total_weight:g} ağırlık tam uyumlu, {partial:g} kısmi, {not_found:g} bulunamadı.",
                }
            )

    if not all_requirements:
        all_requirements = [
            {
                "id": "R1",
                "profile_id": "P1",
                "profile_name": "Genel kamera isterleri",
                "category": "other",
                "requirement": "Kurallı analiz ölçülebilir ister çıkaramadı; Gemini ile analiz önerilir.",
                "weight": 1,
            }
        ]

    best_by_profile = {}
    for row in sorted(profile_rows, key=lambda item: item["score"], reverse=True):
        best_by_profile.setdefault(row["profile_name"], row)
    recommendation_parts = [
        f"{profile}: {row['camera_model']} ({row['score']}/100)"
        for profile, row in best_by_profile.items()
    ]
    recommendation = "En yüksek kurallı skorlar: " + ("; ".join(recommendation_parts) if recommendation_parts else "model yok")

    return {
        "profiles": [{"id": profile["id"], "name": profile["name"], "description": ""} for profile in profiles],
        "requirements": all_requirements,
        "matrix": matrix,
        "camera_scores": sorted(profile_rows, key=lambda item: item["score"], reverse=True),
        "recommendation": recommendation,
    }


def evaluate_rule_requirement(model_name: str, model: Dict[str, Any], requirement: Dict[str, Any]) -> Tuple[str, str]:
    category = requirement.get("category")
    focal_min = float(model.get("focal_min_mm", 2.8))
    focal_max = float(model.get("focal_max_mm", focal_min))
    if focal_max < focal_min:
        focal_min, focal_max = focal_max, focal_min
    ir_range = float(model.get("ir_range_m", 0.0))
    min_lux = float(model.get("min_lux", 0.01))
    cam_type = str(model.get("camera_type", "")).casefold()

    if category == "camera_type":
        expected = requirement.get("value")
        is_moving = bool(re.search(r"ptz|speed\s*dome|hareketli|pan\s*/?\s*tilt", cam_type))
        if expected == "fixed":
            if not is_moving:
                return "Uyumlu", f"Kamera tipi sabit/varifokal ({cam_type or 'Sabit'})."
            return "Uyumsuz", f"Sabit kamera isteniyor ancak model PTZ/hareketli ({cam_type})."
        if expected == "moving":
            if is_moving:
                return "Uyumlu", f"Kamera tipi PTZ/hareketli ({cam_type})."
            return "Uyumsuz", f"Hareketli kamera isteniyor ancak model sabit ({cam_type or 'Sabit'})."

    if category == "lens_range":
        ranges = requirement.get("ranges", [])
        mode = requirement.get("mode", "any_cover")
        if mode == "zoom_cover" and ranges:
            target_low, target_high = max(ranges, key=lambda item: item[1])
            if focal_min <= target_low and focal_max >= target_high:
                return "Uyumlu", f"Lens aralığı {focal_min:g}-{focal_max:g} mm, istenen {target_low:g}-{target_high:g} mm aralığını kapsıyor."
            if focal_max >= target_high:
                return "Kısmi", f"Max odak {focal_max:g} mm yeterli ancak min odak {focal_min:g} mm geniş açıda kalıyor."
            return "Uyumsuz", f"Lens aralığı {focal_min:g}-{focal_max:g} mm, istenen {target_low:g}-{target_high:g} mm aralığı için yetersiz."

        for low, high in ranges:
            if focal_min <= low and focal_max >= high:
                return "Uyumlu", f"Model lens aralığı {focal_min:g}-{focal_max:g} mm, istenen {low:g}-{high:g} mm ile tam uyumlu."
            if (focal_min <= high and focal_max >= low) or (low <= focal_max <= high) or (low <= focal_min <= high):
                return "Kısmi", f"Model lens aralığı {focal_min:g}-{focal_max:g} mm, istenen {low:g}-{high:g} mm ile kısmen kesişiyor."
        return "Uyumsuz", f"Model lens aralığı {focal_min:g}-{focal_max:g} mm."

    if category == "resolution":
        res_name = str(model.get("resolution_name", ""))
        if not res_name:
            return "Bulunamadı", "Broşür veritabanında çözünürlük bilgisi bulunamadı."
        if "8 MP" in requirement.get("requirement", ""):
            if "8 MP" in res_name or "12 MP" in res_name:
                return "Uyumlu", f"Çözünürlük {res_name} (8MP/4K desteği)."
            if "4 MP" in res_name or "5 MP" in res_name:
                return "Kısmi", f"İstenen 8MP, mevcut {res_name}."
            return "Uyumsuz", f"Çözünürlük {res_name}."
        if "2 MP" in res_name or "4 MP" in res_name or "5 MP" in res_name or "8 MP" in res_name or "12 MP" in res_name:
            return "Uyumlu", f"Çözünürlük {res_name}."
        return "Kısmi", f"Çözünürlük {res_name}."

    if category == "ir":
        target = float(requirement.get("value", 0))
        if ir_range <= 0:
            return "Bulunamadı", "Broşür veritabanında IR aydınlatma mesafesi bilgisi bulunamadı."
        if ir_range >= target:
            return "Uyumlu", f"IR mesafesi {ir_range:g} m >= {target:g} m."
        if ir_range >= target * 0.7:
            return "Kısmi", f"IR mesafesi {ir_range:g} m, istenen {target:g} m."
        return "Uyumsuz", f"IR mesafesi {ir_range:g} m yetersiz."

    if category == "lux":
        target = float(requirement.get("value", 0.01))
        if "min_lux" not in model:
            return "Bulunamadı", "Broşür veritabanında minimum lux hassasiyeti bilgisi bulunamadı."
        if min_lux <= target:
            return "Uyumlu", f"Min lux {min_lux:g} <= {target:g}."
        if min_lux <= target * 2:
            return "Kısmi", f"Min lux {min_lux:g}, istenen {target:g}."
        return "Uyumsuz", f"Min lux {min_lux:g} yüksek."

    if category == "wdr":
        wdr_text = str(model.get("wdr", "")).casefold()
        if not wdr_text or wdr_text == "none":
            return "Bulunamadı", "Broşür veritabanında WDR / Geniş Dinamik Aralık bilgisi bulunamadı."
        target_db = float(requirement.get("value", 120.0))
        found_db = [float(m.group(1)) for m in re.finditer(r"(\d{2,3})\s*db", wdr_text)]
        model_db = max(found_db) if found_db else (120.0 if "wdr" in wdr_text or "hdr" in wdr_text else 0.0)
        if model_db >= target_db:
            return "Uyumlu", f"WDR desteği {model.get('wdr', 'Var')} ({model_db:g} dB >= {target_db:g} dB)."
        if model_db > 0 or "wdr" in wdr_text:
            return "Kısmi", f"WDR desteği {model.get('wdr', 'Var')}, istenen {target_db:g} dB."
        return "Uyumsuz", "WDR / Geniş Dinamik Aralık yetersiz."

    if category == "environment_ip":
        ip = str(model.get("ip_rating", "")).upper()
        if not ip:
            return "Bulunamadı", "Broşür veritabanında IP koruma sınıfı bilgisi bulunamadı."
        if "IP66" in ip or "IP67" in ip or "IP68" in ip:
            return "Uyumlu", f"IP koruma sınıfı {ip}."
        return "Kısmi", f"IP koruma sınıfı {ip}."

    if category == "environment_ik":
        ik = str(model.get("ik_rating", "")).upper()
        if not ik:
            return "Bulunamadı", "Broşür veritabanında IK darbe koruma sınıfı bilgisi bulunamadı."
        target_ik = str(requirement.get("value", "IK10"))
        if target_ik in ik or "IK10" in ik:
            return "Uyumlu", f"Darbe koruma sınıfı {ik}."
        return "Kısmi", f"Darbe koruması {ik}, istenen {target_ik}."

    if category == "fps":
        target_fps = float(requirement.get("value", 30))
        if "max_fps" not in model:
            return "Bulunamadı", "Broşür veritabanında kare hızı (fps) bilgisi bulunamadı."
        try:
            max_fps = float(model.get("max_fps", 30))
        except (ValueError, TypeError):
            return "Bulunamadı", "Broşür veritabanında geçerli fps bilgisi bulunamadı."
        if max_fps >= target_fps:
            return "Uyumlu", f"Akış hızı {max_fps:g} fps >= {target_fps:g} fps."
        return "Kısmi", f"Akış hızı {max_fps:g} fps, istenen {target_fps:g} fps."

    if category == "sensor_size":
        req_sensor = str(requirement.get("value", ""))
        sensor = str(model.get("sensor_name", ""))
        if not sensor:
            return "Bulunamadı", "Broşür veritabanında sensör boyutu bilgisi bulunamadı."
        if req_sensor and req_sensor in sensor:
            return "Uyumlu", f"Sensör boyutu {sensor}."
        return "Kısmi", f"Sensör boyutu {sensor}, istenen {req_sensor}."

    if category == "analytics_ai":
        notes = (
            str(model.get("ai_analytics", ""))
            + " "
            + str(model.get("basic_analytics", ""))
            + " "
            + str(model.get("perimeter_protection", ""))
            + " "
            + str(model.get("target_classification", ""))
            + " "
            + str(model.get("object_tracking", ""))
            + " "
            + str(model.get("people_counting", ""))
            + " "
            + str(model.get("analytics_notes", ""))
        ).casefold()
        if not notes.strip():
            return "Bulunamadı", "Broşür veritabanında AI analitik bilgisi bulunamadı."
        ai_type = requirement.get("type", "")
        if ai_type == "perimeter" and re.search(r"perimeter|sınır|çevre|geçiş|line|intrusion", notes):
            return "Uyumlu", f"Çevre güvenliği & sınır ihlali analitiği mevcut ({model.get('perimeter_protection') or 'Var'})."
        if ai_type == "classification" and re.search(r"human|vehicle|insan|araç|sınıf|target", notes):
            return "Uyumlu", f"İnsan/araç yapay zeka sınıflandırma mevcut ({model.get('target_classification') or 'Var'})."
        if ai_type == "counting" and re.search(r"count|sayma|yoğunluk|heat", notes):
            return "Uyumlu", f"Kişi sayma / yoğunluk analitiği mevcut ({model.get('people_counting') or 'Var'})."
        return "Kısmi", "Analitik özellikleri broşür notlarında mevcut ancak bu spesifik tür yazılmamış."

    if category == "storage":
        storage = (str(model.get("internal_storage", "")) + " " + str(model.get("network_storage", ""))).casefold()
        if not storage.strip():
            return "Bulunamadı", "Broşür veritabanında dahili depolama bilgisi bulunamadı."
        if re.search(r"sd|card|kart|gb|tb|slot|hafıza", storage):
            return "Uyumlu", f"Dahili depolama desteği mevcut ({model.get('internal_storage') or 'MicroSD desteği'})."
        return "Kısmi", f"Depolama bilgisi: {model.get('internal_storage')}."

    if category == "audio_alarm":
        audio = (str(model.get("audio_io", "")) + " " + str(model.get("built_in_audio", "")) + " " + str(model.get("alarm_io", ""))).casefold()
        if not audio.strip():
            return "Bulunamadı", "Broşür veritabanında ses/alarm arayüz bilgisi bulunamadı."
        if re.search(r"in|out|giriş|çıkış|mikrofon|mic|speaker|hoparlör|alarm|channel|kanal", audio):
            return "Uyumlu", f"Ses/Alarm arayüzü mevcut ({model.get('audio_io') or model.get('alarm_io') or 'Var'})."
        return "Kısmi", "Ses veya alarm bağlantısı kısmen belirtilmiş."

    if category == "codec":
        codec = (str(model.get("codec", "")) + " " + str(model.get("smart_codec", ""))).casefold()
        if not codec.strip():
            return "Bulunamadı", "Broşür veritabanında video codec bilgisi bulunamadı."
        if "h.265" in codec or "smart" in codec:
            return "Uyumlu", f"Sıkıştırma biçimi {model.get('codec') or 'H.265 / Smart Codec'}."
        return "Kısmi", f"Sıkıştırma biçimi {model.get('codec') or 'H.264'}."

    if category == "temperature":
        target = requirement.get("value", {})
        if "temperature_min_c" not in model:
            return "Bulunamadı", "Broşür veritabanında çalışma sıcaklığı bilgisi bulunamadı."
        try:
            t_min = float(model.get("temperature_min_c", -30))
            t_max = float(model.get("temperature_max_c", 60))
        except (ValueError, TypeError):
            return "Bulunamadı", "Broşür veritabanında geçerli sıcaklık bilgisi bulunamadı."
        if t_min <= target.get("min", -30) and t_max >= target.get("max", 60):
            return "Uyumlu", f"Çalışma sıcaklık aralığı {t_min:g}..{t_max:g} °C."
        return "Kısmi", f"Çalışma sıcaklık aralığı {t_min:g}..{t_max:g} °C."

    if category == "analytics":
        notes = (
            str(model.get("analytics_notes", ""))
            + " "
            + str(model.get("ai_analytics", ""))
            + " "
            + str(model.get("anpr_lpr", ""))
            + " "
            + str(model.get("face_detection", ""))
        ).casefold()
        if not notes.strip():
            return "Bulunamadı", "Broşür veritabanında analitik bilgisi bulunamadı."
        req_text = requirement.get("requirement", "").casefold()
        if ("plaka" in req_text and ("anpr" in notes or "lpr" in notes or "plaka" in notes)) or (
            "yüz" in req_text and ("yüz" in notes or "face" in notes)
        ):
            return "Uyumlu", "Analitik modülü ve broşür notları isterle örtüşüyor."
        return "Kısmi", "Analitik notları kısmen destekliyor."

    return "Bulunamadı", "Broşür veritabanında bu ister için veri bulunamadı."
