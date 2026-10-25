"""Birinchi ishga tushirish oynasi: BOT_TOKEN (va ixtiyoriy AI kalitlari) shu sahifada kiritiladi.

`python -m zft` .env da BOT_TOKEN bo'lmasa, http://localhost:8090 da shu sahifani ochadi.
Kalitlar faqat shu kompyuterdagi .env fayliga yoziladi. Saqlangach, ZFT o'sha zahoti ishga tushadi
va bot egasi bo'lish uchun bir martalik havola beriladi.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from .config import ROOT

log = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,60}$")


def set_env_values(path: Path, values: dict[str, str]) -> None:
    """`.env` faylida kalitlarni yozadi yoki almashtiradi (boshqa qatorlarga tegmaydi)."""
    if not path.exists():
        example = ROOT / ".env.example"
        path.write_text(example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    left = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and key in left and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={left.pop(key)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    os.environ.update(values)


def check_token(token: str, http=requests) -> str:
    if not TOKEN_RE.match(token):
        raise ValueError("Token shakli noto'g'ri. BotFather bergan to'liq tokenni nusxalang (123456789:AA...).")
    try:
        r = http.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError(f"Telegram'ga ulanib bo'lmadi: {exc}") from exc
    if not data.get("ok"):
        raise ValueError("Telegram bu tokenni qabul qilmadi. BotFather'da /mybots → API Token dan qayta oling.")
    return data["result"]["username"]


def configure(env_path: Path, body: dict, http=requests) -> dict:
    token = str(body.get("bot_token", "")).strip()
    username = check_token(token, http)
    values = {"BOT_TOKEN": token, "BOT_USERNAME": username}
    for field, key in (("anthropic", "ANTHROPIC_API_KEY"), ("fal", "FAL_KEY")):
        v = str(body.get(field, "")).strip()
        if v:
            if len(v) < 20 or any(c.isspace() for c in v):
                raise ValueError(f"{key} noto'g'ri ko'rinyapti.")
            values[key] = v
    password = os.environ.get("PANEL_PASSWORD", "")
    if len(password) < 10:
        password = secrets.token_urlsafe(12)
        values["PANEL_PASSWORD"] = password
    code = os.environ.get("OWNER_CLAIM_CODE", "") or str(secrets.randbelow(900000) + 100000)
    values.update(OWNER_CLAIM_CODE=code, PANEL_INSECURE_COOKIE="1")
    set_env_values(env_path, values)
    return {"bot_username": username, "password": password,
            "claim_link": f"https://t.me/{username}?start=egasi_{code}", "claim_code": code,
            "ai": "ANTHROPIC_API_KEY" in values, "studio": "FAL_KEY" in values}


PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZFT — ishga tushirish</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Unbounded:wght@600&family=Onest:wght@400;500;600&display=swap">
<style>
:root{--bg:#eef1f5;--card:#fff;--ink:#13203a;--muted:#5a6479;--line:#d6dce6;--sign:#f5a800;--good:#128a5a;--bad:#d23c3c;--sunk:#e5e9f0}
@media (prefers-color-scheme:dark){:root{--bg:#0b1222;--card:#121b2f;--ink:#e8edf6;--muted:#98a3b9;--line:#24314b;--sign:#ffb81c;--good:#35c088;--bad:#f06a6a;--sunk:#1a253c;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 Onest,system-ui,sans-serif;padding:6vh 16px}
main{max-width:520px;margin:0 auto;animation:rise .7s cubic-bezier(.22,1,.36,1) both}@keyframes rise{from{opacity:0;transform:translateY(14px)}}
.brand{display:flex;gap:14px;align-items:center;margin-bottom:22px}
.d{width:40px;height:40px;background:var(--sign);transform:rotate(45deg);border-radius:8px;display:grid;place-items:center;box-shadow:inset 0 0 0 3px #231900,inset 0 0 0 5px var(--sign)}
.d b{transform:rotate(-45deg);font:600 15px Unbounded,sans-serif;color:#231900}
h1{font:600 24px/1.2 Unbounded,sans-serif;margin:0}.sub{color:var(--muted);margin:2px 0 0}
form,.done{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;display:grid;gap:14px;box-shadow:0 12px 30px -14px rgba(19,32,58,.3)}
label{display:grid;gap:6px;font-weight:600}label small{font-weight:400;color:var(--muted)}
input{font:inherit;padding:11px 13px;border:1px solid var(--line);border-radius:11px;background:var(--sunk);color:var(--ink)}
input:focus{outline:none;border-color:var(--sign);box-shadow:0 0 0 4px color-mix(in srgb,var(--sign) 25%,transparent)}
button,.btn{font:inherit;font-weight:600;padding:12px 16px;border-radius:11px;border:0;background:var(--sign);color:#231900;cursor:pointer;text-align:center;text-decoration:none;display:block}
button[disabled]{opacity:.6}.err{color:var(--bad);min-height:1.3em;margin:0}
details{color:var(--muted)}summary{cursor:pointer}
.done{animation:rise .6s both}.big{font:600 20px Unbounded,sans-serif}.row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line)}
code{font-family:ui-monospace,Menlo,monospace;background:var(--sunk);padding:2px 6px;border-radius:6px}
.ok{color:var(--good);font-weight:600}
</style></head><body><main>
<div class="brand"><div class="d"><b>Z</b></div><div><h1>ZFT</h1><p class="sub">Chorraha uchun AI marketing — ishga tushirish</p></div></div>
<form id="f">
<label>Telegram bot tokeni<small>@BotFather → /newbot (yoki /mybots → API Token). Token faqat shu kompyuterda saqlanadi.</small>
<input id="bot_token" name="bot_token" required autocomplete="off" placeholder="123456789:AA..."></label>
<details><summary>AI kalitlari (ixtiyoriy — keyin ham qo'shsa bo'ladi)</summary>
<div style="display:grid;gap:12px;margin-top:12px">
<label>Claude API kaliti<small>console.anthropic.com → API Keys. Bo'sh bo'lsa — shablon rejim.</small><input id="anthropic" name="anthropic" autocomplete="off"></label>
<label>fal.ai kaliti<small>fal.ai → API Keys. Bo'sh bo'lsa — bepul test-videolar.</small><input id="fal" name="fal" autocomplete="off"></label>
</div></details>
<p class="err" id="err" role="alert"></p>
<button id="go" type="submit">Saqlash va ishga tushirish</button>
</form>
<div class="done" id="done" hidden>
<div class="big">✅ ZFT ishlayapti</div>
<div>Oxirgi qadam: pastdagi tugmani bosing va Telegram'da <b>Start</b> ni bosing. Shu bilan bot sizniki bo'ladi va birinchi video darhol yasala boshlaydi.</div>
<a class="btn" id="claim" target="_blank" rel="noopener">Telegram'da botni ochish</a>
<div class="row"><span>Bot</span><b id="bu"></b></div>
<div class="row"><span>Panel paroli</span><code id="pw"></code></div>
<div class="row"><span>Zaxira kod (kerak bo'lsa)</span><code id="cc"></code></div>
<div class="row"><span>AI matn / AI video</span><span id="ai"></span></div>
<a class="btn" href="/" style="background:var(--sunk);color:var(--ink)">Panelni ochish</a>
<small style="color:var(--muted)">Parolni saqlab qo'ying. Kompyuter yoki bu oyna yopilsa, bot ham to'xtaydi — doimiy ishlashi uchun server kerak (README).</small>
</div>
<script>
const $=id=>document.getElementById(id);
$("f").addEventListener("submit",async e=>{e.preventDefault();$("err").textContent="";$("go").disabled=true;$("go").textContent="Tekshirilmoqda…";
try{const r=await fetch("setup",{method:"POST",headers:{"Content-Type":"application/json","X-Panel":"1"},body:JSON.stringify({bot_token:$("bot_token").value,anthropic:$("anthropic").value,fal:$("fal").value})});
const j=await r.json();if(!r.ok)throw new Error(j.error||"Xato");
$("f").hidden=true;$("done").hidden=false;$("claim").href=j.claim_link;$("bu").textContent="@"+j.bot_username;$("pw").textContent=j.password;
$("cc").textContent="/egasi "+j.claim_code;$("ai").innerHTML=(j.ai?'<span class=ok>bor</span>':"yo'q")+" / "+(j.studio?'<span class=ok>bor</span>':"bepul rejim");}
catch(err){$("err").textContent=err.message}finally{$("go").disabled=false;$("go").textContent="Saqlash va ishga tushirish"}});
</script></main></body></html>"""


def run_setup(env_path: Path, host: str = "127.0.0.1", port: int = 8090) -> bool:
    """Sozlash sahifasini ko'rsatadi; foydalanuvchi saqlagach True qaytaradi (port bo'shatiladi)."""
    done = threading.Event()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: N802
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/").split("?")[0] not in ("/setup", "setup") or self.headers.get("X-Panel") != "1":
                return self._send(403, b'{"error":"rad etildi"}', "application/json")
            n = min(int(self.headers.get("Content-Length") or 0), 10_000)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                res = configure(env_path, body if isinstance(body, dict) else {})
            except ValueError as exc:
                return self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode(), "application/json")
            self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")
            done.set()

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}"
    print(f"\n  ZFT sozlash oynasi: {url}\n  Brauzerda oching va bot tokenini kiriting.\n", flush=True)
    if os.environ.get("ZFT_OPEN_BROWSER", "1") == "1" and not os.environ.get("CODESPACES"):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        done.wait()
    except KeyboardInterrupt:
        srv.shutdown()
        return False
    threading.Event().wait(1.0)  # javob brauzerga yetib borsin
    srv.shutdown()
    srv.server_close()
    return True
