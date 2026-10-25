#!/usr/bin/env bash
# Установка бота расписания на чистый VPS (Ubuntu 22.04/24.04, Debian 12).
#
#   curl -fsSL https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/deploy/install-vps.sh | sudo bash
#
# Можно без вопросов, передав переменные:
#   BOT_TOKEN=123:ABC ADMIN_IDS=123456789 sudo -E bash install-vps.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/mospoliteh2627-alt/schedule-bot-medcollege.git}"
APP_DIR="${APP_DIR:-/opt/schedule-bot}"
SERVICE="schedule-bot"

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

[ "$(id -u)" = "0" ] || { echo "Запустите через sudo."; exit 1; }

ask() {  # ask <переменная> <вопрос> <скрыть?>
  local __var="$1" __prompt="$2" __secret="${3:-no}" __value=""
  if [ "$__secret" = "yes" ]; then
    read -r -s -p "$__prompt: " __value < /dev/tty || true
    echo
  else
    read -r -p "$__prompt: " __value < /dev/tty || true
  fi
  printf -v "$__var" '%s' "$__value"
}

say "1/6  Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null

say "2/6  Код"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

say "3/6  Виртуальное окружение"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "4/6  Настройки"
TOKEN="${BOT_TOKEN:-}"
ADMINS="${ADMIN_IDS:-}"
if [ -z "$TOKEN" ]; then
  echo "Токен берётся у @BotFather в Telegram (если старый засветился — /revoke)."
  ask TOKEN "Вставьте токен бота" yes
fi
if [ -z "$ADMINS" ]; then
  echo "Ваш Telegram id можно узнать позже командой /id в боте и вписать в $APP_DIR/.env"
  ask ADMINS "Ваш Telegram id (можно пустым)" no
fi
cat > "$APP_DIR/.env" <<ENV
BOT_TOKEN=$TOKEN
ADMIN_IDS=$ADMINS
TZ=Europe/Moscow
COLLEGE_NAME=Медколледж
DIGEST_TIME=07:30
ENV
chmod 600 "$APP_DIR/.env"

say "5/6  Проверка токена"
if ! "$APP_DIR/.venv/bin/python" -m app.main --check; then
  warn "Telegram не принял токен. Проверьте его и поправьте $APP_DIR/.env, затем:"
  echo "  systemctl restart $SERVICE"
  exit 1
fi

say "6/6  Служба systemd"
RUN_USER="${SUDO_USER:-root}"
[ "$RUN_USER" = "root" ] || id -u "$RUN_USER" >/dev/null 2>&1 || RUN_USER=root
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
cat > "/etc/systemd/system/$SERVICE.service" <<UNIT
[Unit]
Description=Telegram schedule bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m app.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 3
systemctl --no-pager --lines=8 status "$SERVICE" || true

cat <<DONE

Готово. Бот работает 24/7 и перезапускается сам.

  Логи:        journalctl -u $SERVICE -f
  Перезапуск:  systemctl restart $SERVICE
  Настройки:   nano $APP_DIR/.env

В Telegram: /start → /id (узнать свой id) → вписать в ADMIN_IDS → systemctl restart $SERVICE
Дальше: /admin → «Загрузить PDF» → предпросмотр → «Опубликовать».

DONE
