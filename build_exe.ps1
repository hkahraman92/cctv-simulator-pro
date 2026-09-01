param(
    [switch]$OneFile,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Test-Path ".\cctv_dual_view_simulator.py")) {
    throw "cctv_dual_view_simulator.py bulunamadi."
}

if (-not (Test-Path ".\camera_library_from_excel.json")) {
    throw "camera_library_from_excel.json bulunamadi."
}

try {
    & $PythonExe -c "import PyInstaller" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller import failed"
    }
}
catch {
    Write-Host "PyInstaller bulunamadi."
    Write-Host "Kurulum: $PythonExe -m pip install pyinstaller pillow"
    exit 1
}

if ($OneFile) {
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onefile `
        --name "CCTV Dual View Simulator" `
        --add-data "camera_library_from_excel.json;." `
        "cctv_dual_view_simulator.py"
}
else {
    & $PythonExe -m PyInstaller --noconfirm --clean ".\cctv_dual_view_simulator.spec"
}

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build basarisiz oldu."
}

Write-Host ""
Write-Host "Build tamamlandi."
Write-Host "Cikti klasoru: $ScriptDir\dist"
Write-Host "EXE ilk calistiginda kamera veritabani kullanici klasorune kopyalanir:"
Write-Host "%APPDATA%\CCTV Dual View Simulator\camera_library_from_excel.json"
