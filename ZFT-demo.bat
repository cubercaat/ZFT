@echo off
chcp 65001 >nul
title ZFT
cd /d "%~dp0"
echo.
echo   ZFT demo - Chorraha uchun AI marketing
echo.
where python >nul 2>nul || (
  echo Python topilmadi, ornatilmoqda...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo Python ornatildi. Bu oynani yopib, ZFT-demo.bat ni qayta oching.
  pause
  exit /b
)
where ffmpeg >nul 2>nul || (
  echo ffmpeg topilmadi, ornatilmoqda...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo ffmpeg ornatildi. Bu oynani yopib, ZFT-demo.bat ni qayta oching.
  pause
  exit /b
)
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
echo.
echo   Brauzerda http://localhost:8090 ochiladi. Bot tokenini kiriting.
echo   Bu oyna ochiq tursa, bot ishlaydi. Yopsangiz - toxtaydi.
echo.
python -m zft run
pause
