#!/usr/bin/env bash
# ZFT — bitta buyruq bilan o'rnatish va yangilash (Ubuntu 22.04 / 24.04).
#
# Ochiq repo:
#   curl -fsSL https://raw.githubusercontent.com/cubercaat/zft/main/deploy/setup.sh | sudo bash
# Yopiq (private) repo — GitHub'dan "fine-grained token" (faqat shu repo, Contents: Read) oling:
#   curl -fsSL -H "Authorization: token TOKEN" https://raw.githubusercontent.com/cubercaat/zft/main/deploy/setup.sh | sudo GH_TOKEN=TOKEN bash
#
# Domen kerak emas: server IP manzilidan bepul HTTPS manzil yasaladi (masalan 1-2-3-4.sslip.io).
# Qayta ishga tushirilsa — kodni yangilaydi, .env dagi sozlamalarga tegmaydi.
set -euo pipefail

REPO="${ZFT_REPO:-cubercaat/zft}"
BRANCH="${ZFT_BRANCH:-main}"
APP_DIR=/opt/zft
ENV_FILE="$APP_DIR/.env"
TTY=/dev/tty

say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; }
ask()  { local q="$1" def="${2:-}" a; if [ -r "$TTY" ]; then read -r -p "$q" a < "$TTY" || true; fi; echo "${a:-$def}"; }
asks() { local q="$1" a; if [ -r "$TTY" ]; then read -r -s -p "$q" a < "$TTY" || true; echo >&2; fi; echo "${a:-}"; }
setenv() {  # setenv KEY VALUE — .env da qiymatni yozadi yoki almashtiradi
  local k="$1" v="$2"
  if grep -q "^$k=" "$ENV_FILE"; then
    python3 - "$ENV_FILE" "$k" "$v" <<'PY'
import sys, re
path, key, val = sys.argv[1:]
lines = open(path, encoding="utf-8").read().splitlines()
out = [f"{key}={val}" if re.match(rf"^{re.escape(key)}=", l) else l for l in lines]
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
  else
    echo "$k=$v" >> "$ENV_FILE"
  fi
}
getenv() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]\+#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//'; }

[ "$(id -u)" = 0 ] || { echo "sudo bilan ishga tushiring"; exit 1; }

say "Tizim paketlari"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q git python3 python3-venv ffmpeg fonts-dejavu-core nginx certbot python3-certbot-nginx \
                   ufw fail2ban unattended-upgrades sqlite3 curl ca-certificates >/dev/null

say "Xavfsizlik: firewall (faqat SSH, HTTP, HTTPS), fail2ban, avtomatik yangilanishlar"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

say "Kod: github.com/$REPO ($BRANCH)"
id zft &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin zft
URL="https://github.com/$REPO.git"
[ -n "${GH_TOKEN:-}" ] && URL="https://x-access-token:${GH_TOKEN}@github.com/$REPO.git"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" remote set-url origin "$URL"
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  OLD=""
  if [ -d "$APP_DIR" ]; then OLD="$APP_DIR.old.$(date +%s)"; mv "$APP_DIR" "$OLD"; fi
  git clone -q --branch "$BRANCH" "$URL" "$APP_DIR"
  if [ -n "$OLD" ]; then   # eski o'rnatishdagi sozlama va bazani saqlab qolamiz
    [ -f "$OLD/.env" ] && cp "$OLD/.env" "$ENV_FILE"
    [ -d "$OLD/data" ] && cp -a "$OLD/data/." "$APP_DIR/data/" 2>/dev/null || true
  fi
fi
git -C "$APP_DIR" remote set-url origin "https://github.com/$REPO.git"   # token diskda saqlanmaydi
mkdir -p "$APP_DIR/data/media" "$APP_DIR/data/backups"

say "Python muhiti"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
ln -sf "$APP_DIR/.venv/bin/edge-tts" /usr/local/bin/edge-tts

FIRST=0
if [ ! -f "$ENV_FILE" ]; then
  FIRST=1
  cp "$APP_DIR/.env.example" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

if [ -z "$(getenv BOT_TOKEN)" ]; then
  say "Sozlamalar (kalitlar faqat shu serverda, $ENV_FILE ichida saqlanadi)"
  echo "Telegram: @BotFather → /newbot → token."
  while :; do
    T="$(asks 'BOT_TOKEN: ')"
    [ -z "$T" ] && { echo "Token kerak."; continue; }
    U="$(curl -fsS "https://api.telegram.org/bot$T/getMe" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])' 2>/dev/null || true)"
    [ -n "$U" ] && break
    echo "Bu token ishlamadi, qayta kiriting."
  done
  setenv BOT_TOKEN "$T"; setenv BOT_USERNAME "$U"
  echo "✅ Bot: @$U"
  echo
  echo "Claude API kaliti (console.anthropic.com → API Keys). Bo'sh qoldirsangiz — AI'siz shablon rejim."
  A="$(asks 'ANTHROPIC_API_KEY: ')"; [ -n "$A" ] && setenv ANTHROPIC_API_KEY "$A"
  echo
  echo "fal.ai kaliti (fal.ai → API Keys) — AI kinematografik videolar uchun. Bo'sh qoldirsangiz — bepul test-videolar."
  F="$(asks 'FAL_KEY: ')"; [ -n "$F" ] && setenv FAL_KEY "$F"
  B="$(ask 'Oylik AI video budjeti, $ [25]: ' 25)"; setenv VIDEO_MONTHLY_BUDGET_USD "$B"
fi

if [ -z "$(getenv PANEL_PASSWORD)" ]; then
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  setenv PANEL_PASSWORD "$PW"
  SHOW_PW="$PW"
fi
if [ -z "$(getenv OWNER_ID)" ] && [ -z "$(getenv OWNER_CLAIM_CODE)" ]; then
  setenv OWNER_CLAIM_CODE "$(python3 -c 'import secrets; print(secrets.randbelow(900000)+100000)')"
fi

say "HTTPS manzil (domensiz)"
IP="$(curl -fsS -4 https://api.ipify.org || curl -fsS -4 https://ifconfig.me)"
HOST="${ZFT_HOST:-$(echo "$IP" | tr . -).sslip.io}"
cat > /etc/nginx/sites-available/zft <<NGINX
server {
    listen 80;
    server_name $HOST;
    client_max_body_size 64k;

    # Instagram va TikTok videoni shu yerdan yuklab oladi
    location /media/ {
        alias $APP_DIR/data/media/;
        autoindex off;
        types { video/mp4 mp4; }
        default_type application/octet-stream;
        add_header Cache-Control "public, max-age=86400";
        limit_except GET HEAD { deny all; }
    }
    # Boshqaruv paneli va hamkor kabinetlari
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 60s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/zft /etc/nginx/sites-enabled/zft
rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/zft-media
chmod o+x /opt "$APP_DIR" "$APP_DIR/data" "$APP_DIR/data/media" 2>/dev/null || true
nginx -t -q && systemctl reload nginx
SCHEME=http
if certbot --nginx -d "$HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect >/dev/null 2>&1 \
   || [ -d "/etc/letsencrypt/live/$HOST" ]; then
  SCHEME=https
else
  echo "⚠️  HTTPS sertifikati olinmadi (keyinroq qayta: sudo bash $APP_DIR/deploy/setup.sh). Hozircha http."
fi
setenv PANEL_INSECURE_COOKIE "$([ "$SCHEME" = https ] && echo 0 || echo 1)"
setenv PANEL_URL "$SCHEME://$HOST"
setenv PUBLIC_MEDIA_URL "$SCHEME://$HOST/media"
setenv MEDIA_DIR "$APP_DIR/data/media"

chown -R zft:zft "$APP_DIR"
chmod 600 "$ENV_FILE"

say "Xizmat (systemd), kunlik backup, eski videolarni tozalash"
cp "$APP_DIR/deploy/zft.service" /etc/systemd/system/zft.service
cat > /etc/cron.d/zft <<CRON
30 3 * * * zft sqlite3 $APP_DIR/data/zft.db ".backup '$APP_DIR/data/backups/zft-\$(date +\\%F).db'" && find $APP_DIR/data/backups -name '*.db' -mtime +14 -delete
45 3 * * * zft find $APP_DIR/data/media -name 'p*.mp4' -mtime +30 -delete
CRON
cat > /usr/local/bin/zft-update <<UPD
#!/usr/bin/env bash
# Yangilash: GitHub'dagi oxirgi versiyani olib, xizmatni qayta ishga tushiradi.
set -e
curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/deploy/setup.sh | sudo GH_TOKEN="\${GH_TOKEN:-}" bash
UPD
chmod +x /usr/local/bin/zft-update
systemctl daemon-reload
systemctl enable zft >/dev/null 2>&1
systemctl restart zft
sleep 4
systemctl is-active --quiet zft && STATUS="✅ ishlayapti" || STATUS="❌ ishlamayapti — sudo journalctl -u zft -n 50"

CODE="$(getenv OWNER_CLAIM_CODE)"
BOTU="$(getenv BOT_USERNAME)"
cat <<DONE

════════════════════════════════════════════════════════════
 ZFT $STATUS
════════════════════════════════════════════════════════════
 Panel:   $SCHEME://$HOST
DONE
[ -n "${SHOW_PW:-}" ] && echo " Parol:   $SHOW_PW      ← saqlab qo'ying, faqat bir marta ko'rsatiladi"
if [ -z "$(getenv OWNER_ID)" ]; then
cat <<DONE

 1) Telegram'da @$BOTU ga yozing:   /egasi $CODE
    Shu bilan siz bot egasi bo'lasiz va birinchi video darhol yasala boshlaydi.
DONE
fi
cat <<DONE
 2) Botni kanalingiz va PRAVA QUIZ guruhiga admin qiling — o'zi aniqlaydi.
 3) Instagram / TikTok: panel → «Ulanishlar».

 Loglar:    sudo journalctl -u zft -f
 Yangilash: sudo zft-update
 Sozlamalar: sudo nano $ENV_FILE  &&  sudo systemctl restart zft
════════════════════════════════════════════════════════════
DONE
