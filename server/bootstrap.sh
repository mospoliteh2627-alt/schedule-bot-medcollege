#!/usr/bin/env bash
# Одна команда для чистого VPS: базовый сервер (Caddy, бэкапы, firewall) + бот расписания.
#
#   curl -fsSL https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/server/bootstrap.sh | sudo bash
set -euo pipefail
BASE="https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main"
[ "$(id -u)" = "0" ] || { echo "Запустите через sudo."; exit 1; }

echo "== 1/2  Базовый сервер =="
curl -fsSL "$BASE/server/install-server.sh" | bash

echo
echo "== 2/2  Бот расписания =="
curl -fsSL "$BASE/deploy/install-vps.sh" | bash

cat <<'DONE'

Всё готово. Что дальше:

  1) В Telegram: /start → /id → вписать id в /opt/schedule-bot/.env (ADMIN_IDS=...)
     sudo systemctl restart schedule-bot
  2) /admin → «Загрузить PDF» → проверить предпросмотр → «Опубликовать»
  3) Лендинг (когда будет домен):
     rsync -avz personal-site/ root@IP:/var/www/guvanch.ru/
  4) Новый проект: new-project.sh <имя> <домен> <порт> [--systemd "команда"]

Логи: journalctl -u schedule-bot -f  ·  systemctl status caddy schedule-bot
DONE
