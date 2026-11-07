@echo off
setlocal
cd /d "%~dp0"
REM Full release: Python core sidecar -> frontend -> Rust shell -> NSIS installer.
REM Output: desktop\src-tauri\target\release\bundle\nsis\CCTV Simülatör Pro_<ver>_x64-setup.exe
call build_core.bat || exit /b 1
cd ..
if not exist node_modules (call npm ci || exit /b 1)
call npm run typecheck || exit /b 1
call npm test || exit /b 1
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
call npx tauri build || exit /b 1
echo.
echo Kurulum dosyasi: src-tauri\target\release\bundle\nsis\
