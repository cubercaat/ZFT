#!/usr/bin/env bash
# Demo: ZFT ni shu kompyuterda (yoki GitHub Codespaces'da) ishga tushiradi.
# Birinchi marta brauzerda sozlash oynasi ochiladi: bot tokenini kiritasiz — tamom.
set -e
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo "ffmpeg kerak: sudo apt install ffmpeg  (Mac: brew install ffmpeg)"; exit 1; }
if [ -z "${CODESPACES:-}" ]; then
  [ -d .venv ] || python3 -m venv .venv
  . .venv/bin/activate
  pip install -q -r requirements.txt
fi
echo "ZFT ishga tushyapti... (to'xtatish: Ctrl+C)"
exec python -m zft run
