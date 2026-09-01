@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ==========================================================
echo   CCTV Simulator - Windows EXE build
echo ==========================================================
echo.

set "MODE=onedir"
if /i "%~1"=="onefile" set "MODE=onefile"
echo Build mode : %MODE%

REM ---------------------------------------------------------------
REM  Butun arayuz Tk uzerine kurulu.
REM  1. gecis: tkinter'i olan, conda OLMAYAN bir yorumlayici ara.
REM     (Anaconda'da tcl/tk baska bir dizinde durur ve PyInstaller
REM      tkinter'i pakete almadan build'i "basarili" bitirebilir.)
REM  2. gecis: hicbiri yoksa conda'ya da izin ver, ama uyar.
REM ---------------------------------------------------------------
set "PY="
call :pick "py -3.13"
call :pick "py -3.12"
call :pick "py -3.11"
call :pick "py -3.10"
call :pick "py -3"
call :pick "python"

if not defined PY (
    call :pickany "py -3"
    call :pickany "python"
    call :pickany "python3"
    if defined PY (
        echo.
        echo   [UYARI] Sadece conda tabanli bir Python bulundu.
        echo           PyInstaller + Anaconda birlesimi tkinter'i pakete
        echo           almayabilir. Sorun cikarsa python.org surumunu kurun.
        echo.
    )
)

if not defined PY (
    echo.
    echo ==========================================================
    echo   [HATA] tkinter iceren bir Python bulunamadi.
    echo ==========================================================
    echo.
    echo   Kurulu Python surumleri:
    py -0p 2>nul
    echo.
    echo   Cozum:
    echo     1^) Denetim Masasi ^> Uygulamalar ^> Python ^> Modify
    echo     2^) "tcl/tk and IDLE" secenegini ISARETLEYIN
    echo     3^) Kurulumu tamamlayin
    echo     4^) Bu klasordeki .venv-build klasorunu SILIN
    echo     5^) build_exe.bat dosyasini tekrar calistirin
    echo.
    echo   Python yoksa: https://www.python.org/downloads/
    echo   ^(kurulumda "Add python.exe to PATH" de isaretli olsun^)
    echo.
    pause
    exit /b 1
)

echo.
echo ---------------- Secilen yorumlayici ----------------
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo   Surum : %%v
for /f "delims=" %%e in ('%PY% -c "import sys;print(sys.executable)"') do echo   Yol   : %%e
for /f "delims=" %%t in ('%PY% -c "import tkinter;print(tkinter.TkVersion)"') do echo   Tk    : %%t
echo ----------------------------------------------------

if not exist ".venv-build\Scripts\python.exe" (
    echo.
    echo [1/5] Sanal ortam olusturuluyor...
    %PY% -m venv .venv-build
    if errorlevel 1 (
        echo [HATA] Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
) else (
    echo.
    echo [1/5] Mevcut sanal ortam kullaniliyor.
)
set "VPY=.venv-build\Scripts\python.exe"

echo [2/5] Sanal ortamda tkinter dogrulaniyor...
"%VPY%" -c "import tkinter, _tkinter" 2>nul
if errorlevel 1 (
    echo         Eksik - sanal ortam yeniden kuruluyor...
    rmdir /s /q .venv-build
    %PY% -m venv .venv-build
    "%VPY%" -c "import tkinter, _tkinter" 2>nul
    if errorlevel 1 (
        echo   [HATA] Sanal ortamda hala tkinter yok.
        echo          Yukaridaki cozum adimlarini uygulayip tekrar deneyin.
        pause
        exit /b 1
    )
)
echo         tkinter OK.

echo [3/5] Bagimliliklar kuruluyor...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install --upgrade --quiet -r requirements.txt
if errorlevel 1 (
    echo [HATA] pip install basarisiz.
    pause
    exit /b 1
)

echo [4/5] Onceki cikti temizleniyor...
taskkill /f /im "CCTV Simulator.exe" 2>nul
taskkill /f /im "CCTV Dual View Simulator.exe" 2>nul
ping 127.0.0.1 -n 2 >nul
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [5/5] PyInstaller calisiyor... (birkac dakika surebilir)
if /i "%MODE%"=="onefile" (set "CCTV_ONEFILE=1") else (set "CCTV_ONEFILE=0")
"%VPY%" -m PyInstaller --noconfirm --clean cctv_simulator.spec
if errorlevel 1 (
    echo.
    echo [HATA] Build basarisiz. Yukaridaki log satirlarina bakin.
    pause
    exit /b 1
)

echo.
echo ==========================================================
if /i "%MODE%"=="onefile" (
    echo   HAZIR:  dist\CCTV Simulator.exe
) else (
    echo   HAZIR:  dist\CCTV Simulator\CCTV Simulator.exe
    echo   Dagitirken "CCTV Simulator" klasorunun tamamini kopyalayin.
)
echo ==========================================================
echo.
start "" "dist"
pause
exit /b 0

REM ------------- tkinter var VE conda degil -------------
:pick
if defined PY exit /b
%~1 -c "import tkinter,_tkinter,sys;raise SystemExit(1 if 'conda' in sys.executable.lower() else 0)" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b

REM ------------- tkinter var (conda olsa da) -------------
:pickany
if defined PY exit /b
%~1 -c "import tkinter,_tkinter" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b
