#!/usr/bin/env bash
# Добавить ещё один проект на сервер: домен → 127.0.0.1:ПОРТ (+ systemd, если нужно).
#
#   sudo ./new-project.sh banking banking.guvanch.ru 8787
#   sudo ./new-project.sh mysite mysite.guvanch.ru 3000 --user anna \
#        --systemd "/home/anna/projects/mysite/.venv/bin/python -m app.main"
#
# Скрипт идемпотентен: повторный запуск обновляет конфиг и перезапускает службу.
set -euo pipefail

NAME="${1:-}"; DOMAIN="${2:-}"; PORT="${3:-}"
[ -n "$NAME" ] && [ -n "$DOMAIN" ] && [ -n "$PORT" ] || {
	echo "Использование: $0 <имя> <домен> <порт> [--systemd \"команда запуска\"]"; exit 1; }
shift 3 || true

CMD=""
RUN_USER=""
while [ $# -gt 0 ]; do
	case "$1" in
		--systemd) CMD="${2:-}"; shift 2 ;;
		--user)    RUN_USER="${2:-}"; shift 2 ;;
		*) shift ;;
	esac
done
[ "$(id -u)" = "0" ] || { echo "Запустите через sudo."; exit 1; }

RUN_USER="${RUN_USER:-${SUDO_USER:-root}}"
[ "$RUN_USER" = "root" ] || id -u "$RUN_USER" >/dev/null 2>&1 || RUN_USER=root
if [ "$RUN_USER" = "root" ]; then
	PROJECT_DIR="/opt/projects/$NAME"
else
	PROJECT_DIR="/home/$RUN_USER/projects/$NAME"
fi
mkdir -p "$PROJECT_DIR" /etc/caddy/conf.d
[ "$RUN_USER" = "root" ] || chown -R "$RUN_USER":"$RUN_USER" "$PROJECT_DIR"

# --- Caddy: домен → локальный порт (WebSocket работает из коробки)
cat > "/etc/caddy/conf.d/$NAME.caddy" <<CADDY
# $NAME — добавлено $(date +%F)
$DOMAIN {
	encode zstd gzip
	reverse_proxy 127.0.0.1:$PORT
}
CADDY

# --- systemd-служба (если передали команду запуска)
if [ -n "$CMD" ]; then
	chown -R "$RUN_USER":"$RUN_USER" "$PROJECT_DIR"
	cat > "/etc/systemd/system/$NAME.service" <<UNIT
[Unit]
Description=$NAME
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$CMD
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
# чтобы один проект не съел сервер у соседей
MemoryMax=${MEMORY_MAX:-512M}
CPUQuota=${CPU_QUOTA:-50%}

[Install]
WantedBy=multi-user.target
UNIT
	systemctl daemon-reload
	systemctl enable --now "$NAME"
fi

caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 && systemctl reload caddy || {
	echo "Ошибка в Caddyfile — проверьте /etc/caddy/conf.d/$NAME.caddy"; exit 1; }

IP="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || echo 'IP_СЕРВЕРА')"
cat <<DONE

Проект «$NAME» подключён.

  1. DNS: A-запись $DOMAIN → $IP (у регистратора или в Cloudflare)
  2. Приложение должно слушать 127.0.0.1:$PORT (наружу порт не открываем)
  3. Код проекта: $PROJECT_DIR
  4. Конфиг: /etc/caddy/conf.d/$NAME.caddy   (логи: journalctl -u caddy -f)
$( [ -n "$CMD" ] && echo "  5. Служба: systemctl status $NAME" || echo "  5. Запуск приложения — docker compose или своя служба" )

DONE
