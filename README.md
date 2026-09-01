# CCTV Dual-View Simulator

Run the app from this folder with:

```powershell
py cctv_dual_view_simulator.py
```

or, if Python is installed as `python`:

```powershell
python cctv_dual_view_simulator.py
```

Added features include:

- input validation with visible error messages
- project save/load as JSON
- CSV, PNG/EPS, and text-based PDF export
- wide/narrow lens comparison mode
- multiple cameras with plan coordinates and heading angle
- optional plan image background
- custom PPM task layers
- mouse hover distance/PPM readout
- automatic installation recommendations
- top-down auto scale based on each camera view angle
- interactive top-down camera move/rotate/target placement tools
- target point PPM, angle, geometry, and IR validation
- built-in generic camera model library
- Excel-derived camera library from `camera_library_from_excel.json`
- focal length suggestion engine for a selected task and distance
- tilt/dead-zone optimization helper
- IR range and minimum lux checks for night coverage
- automatic alternative camera model suggestions from the Excel-derived library
- one-click application of the best suggested alternative model
- separate specification assistant UI for camera selection and compliance matrix
- optional Gemini API analysis using `GEMINI_API_KEY` or an entered API key
- rule-based fallback compliance analysis when Gemini is unavailable
- PDF files are sent to Gemini as PDF input instead of being displayed as garbled text
- DOCX files are unpacked and converted to text with the standard library
- multi-camera specifications are grouped into profiles and shown with a `Profil` column
- camera database screen for missing data completion, existing model updates, and new model entry
- extra model fields such as camera type, IP rating, temperature range, codec, ONVIF, WDR, analytics notes, and raw brochure evidence
- brochure-level database fields for overview/highlights, optical DORI values, FOV, shutter, day/night, illuminator, streams, network security, storage, audio/alarm, AI analytics, power, housing, dimensions, weight, drawings, and certificates
- camera type compliance treats zoom/focus lens movement as fixed-camera capable; only pan/tilt/PTZ/speed dome movement is classified as moving camera

The app was syntax-checked with the bundled Codex Python runtime.

The Excel file `Kamera _Broşür_Bilgileri 27 02 2026.xlsx` was parsed into `camera_library_from_excel.json`. The app loads this JSON automatically at startup and merges it into the ready-made camera model list.

Gemini integration uses Google AI Studio's `models.generateContent` REST endpoint. The default editable model field is `gemini-3.6-flash`.

## Windows EXE Build

Install build dependencies once:

```powershell
python -m pip install pyinstaller pillow
```

Create the recommended folder-based EXE package:

```powershell
.\build_exe.ps1
```

If PowerShell blocks `.ps1` scripts with an execution policy error, use the command wrapper instead:

```cmd
build_exe.cmd
```

Or run the PowerShell script with a process-only bypass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

The output will be under:

```text
dist\CCTV Dual View Simulator\CCTV Dual View Simulator.exe
```

For a single-file EXE:

```powershell
.\build_exe.ps1 -OneFile
```

or:

```cmd
build_exe.cmd --onefile
```

When running as an EXE, the bundled `camera_library_from_excel.json` is copied on first launch to a writable user data folder:

```text
%APPDATA%\CCTV Dual View Simulator\camera_library_from_excel.json
```

Camera database edits are saved there, so the EXE can be placed under a read-only folder without breaking model updates.

---

## Windows EXE derleme

Tek dosyaya paketlenmiş tam uygulama: klasik çift görünüm ekranı, 3D kamera bakış
açısı, kamera veritabanı, şartname asistanı, tüm dışa aktarımlar ve yeni
EN 62676-4 optik tezgâhı.

Windows makinede, bu klasörde:

    build_exe.bat                 ->  dist\CCTV Simulator\CCTV Simulator.exe
    build_exe.bat onefile         ->  dist\CCTV Simulator.exe

`build_exe.bat` kendi sanal ortamını (`.venv-build`) kurar, `requirements.txt`
içindekileri yükler ve PyInstaller'ı çalıştırır. Python 3.10+ gerekir
(kurulumda "Add python.exe to PATH" işaretli olmalı).

| Mod | Çıktı | Açılış | Dağıtım |
|---|---|---|---|
| `onedir` (varsayılan) | `dist\CCTV Simulator\` klasörü | hızlı (~1 sn) | klasörün tamamını kopyalayın |
| `onefile` | tek `.exe` (~30 MB) | yavaş (~5-10 sn, her açılışta geçici klasöre açılır) | tek dosya |

Elle derlemek isterseniz:

    pip install -r requirements.txt
    pyinstaller --noconfirm cctv_simulator.spec

Notlar:

- `cctv_simulator.spec` ttkbootstrap temalarını, customtkinter tema/asset
  dosyalarını ve reportlab font metriklerini ayrıca toplar. Bunlar import
  zinciriyle bulunamayan veri dosyaları olduğu için elle eklenmeleri gerekir;
  eksik olurlarsa exe açılışta çöker.
- UPX kapalı bırakıldı: birçok antivirüs motoru UPX ile sıkıştırılmış exe'leri
  yanlış pozitif olarak işaretliyor.
- İkon `assets\cctv_logo.ico` (1024 px logodan 16-256 px arası üretildi).
- Donmuş uygulamada kamera kütüphanesi `%APPDATA%\CCTV Dual View Simulator\`
  altına kopyalanır ve düzenlemeler oraya yazılır.
- Eski `cctv_dual_view_simulator.spec` ve `CCTV Dual View Simulator.spec`
  dosyalarının yerini `cctv_simulator.spec` aldı.

## Kaynaktan çalıştırma

    pip install -r requirements.txt
    python cctv_dual_view_simulator.py      # tam uygulama
    python cctv_optics_workbench.py         # sadece optik tezgâhı

Optik tezgâhı klasik uygulamanın içinden de açılır: üst şeritteki
**◈ Optik Tezgâhı** düğmesi veya Kamera sekmesindeki aynı düğme. Seçili
kameranın optiği ve geometrisi pencereye aktarılır.
