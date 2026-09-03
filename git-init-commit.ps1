# İlk commit'i atar. Bir kez çalıştır:
#   powershell -ExecutionPolicy Bypass -File .\git-init-commit.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".git")) {
    git init
    git branch -M main
}

# Yalnızca ilk kurulumda: kendi bilgilerinle değiştir ya da bu iki satırı sil
# git config user.name  "Harun"
# git config user.email "harun_738@hotmail.com"

git add -A
git status --short

$msg = @"
fix(map): gercek DEM, sessiz indirme hatalarini gorunur kil

- fetch_online_elevation_grid artik Terrarium karolarindan gercek yukselti
  indiriyor; uydurma sirt/vadi yalnizca son care ve is_measured=False
- DEM mozaiginde kuzey-guney aynalanmasini onleyen flipud
- except blogunda silinen exc yuzunden hic acilmayan hata diyalogu
- eksik/bos mozaik artik RuntimeError, sessiz gri harita degil
- Vazgec sirasinda 108 Tk callback hatasi -> 0
- OSM/OpenTopoMap karo URL'lerinde x/y ters yazilmisti
- map_3d_window: eksik 'from pathlib import Path' (Yerel Ortofoto Yukle bozuktu)
- gercek User-Agent, sunucu basina esazamanlilik siniri, disk onbellegi
- kare butcesi ve durust m/px etiketi
- Mercator piksel uzayinda tam kirpma
- __init__.py'ler tembel (362 ms -> 2.1 ms), requirements.txt'e numpy

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016REg1kTtfQ1nyu4CaAuw87
"@

git commit -m $msg
git log --stat -1
