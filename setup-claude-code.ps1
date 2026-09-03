# Proje skill'lerini .claude\skills\ altina yerlestirir. Bir kez calistir:
#   powershell -ExecutionPolicy Bypass -File .\setup-claude-code.ps1
#
# Uzaktan yazim .claude\ dizinine izin vermiyor; bu yuzden dosyalar
# claude-setup\ altina birakildi ve buradan tasiniyor.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$skillsRoot = Join-Path $PSScriptRoot ".claude\skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

# 1. Bu oturumda cikarilan proje skill'i
$staged = Join-Path $PSScriptRoot "claude-setup\skills"
if (Test-Path $staged) {
    Copy-Item -Path (Join-Path $staged "*") -Destination $skillsRoot -Recurse -Force
    Write-Host "proje skill'i yerlestirildi" -ForegroundColor Green
}

# 2. Hesap skill'lerinin kopyasi (istege bagli)
$zip = Join-Path $PSScriptRoot "project-skills.zip"
if (Test-Path $zip) {
    $answer = Read-Host "project-skills.zip icindeki 16 skill de acilsin mi? (e/H)"
    if ($answer -eq "e") {
        $tmp = Join-Path $env:TEMP "cctv-skills"
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inner = Join-Path $tmp ".claude\skills"
        $src = if (Test-Path $inner) { $inner } else { $tmp }
        Copy-Item -Path (Join-Path $src "*") -Destination $skillsRoot -Recurse -Force
        Remove-Item $tmp -Recurse -Force
        Write-Host "zip icindeki skill'ler acildi" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Yerlesen skill'ler:" -ForegroundColor Cyan
Get-ChildItem $skillsRoot -Directory | ForEach-Object { "  /$($_.Name)" }

Write-Host ""
Write-Host "Simdi bu klasorde calistir:  claude" -ForegroundColor Yellow
