#!/usr/bin/env bash
# Базовый сервер для нескольких проектов: Caddy (HTTPS для всех доменов),
# каталоги, бэкапы, firewall, автообновления безопасности.
#
#   curl -fsSL https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/server/install-server.sh | sudo bash
set -euo pipefail
say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
[ "$(id -u)" = "0" ] || { echo "Запустите через sudo."; exit 1; }

say "1/5  Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl gnupg tar ufw unattended-upgrades \
	debian-keyring debian-archive-keyring apt-transport-https >/dev/null

say "2/5  Caddy"
if ! command -v caddy >/dev/null 2>&1; then
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		> /etc/apt/sources.list.d/caddy-stable.list
	apt-get update -qq
	apt-get install -y -qq caddy >/dev/null
fi
systemctl enable --now caddy >/dev/null 2>&1 || true

say "3/5  Каталоги и конфиг"
mkdir -p /opt/projects /var/www/guvanch.ru /etc/caddy/conf.d /root/backups

BASE="https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/server"
if [ ! -f /etc/caddy/Caddyfile ] || ! grep -q 'conf.d' /etc/caddy/Caddyfile; then
	curl -fsSL "$BASE/Caddyfile" -o /etc/caddy/Caddyfile
	echo "Caddyfile установлен из репозитория."
else
	echo "Caddyfile уже настроен — не трогаю."
fi
[ -f /etc/caddy/conf.d/banking.caddy ] || curl -fsSL "$BASE/conf.d-banking.caddy" -o /etc/caddy/conf.d/banking.caddy || true

caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl reload caddy || systemctl restart caddy

say "4/5  Бэкапы (каждый день в 04:00, хранение 14 дней)"
curl -fsSL "$BASE/backup.sh" -o /usr/local/bin/server-backup.sh
chmod +x /usr/local/bin/server-backup.sh
cat > /etc/cron.d/server-backup <<CRON
0 4 * * * root /usr/local/bin/server-backup.sh >> /var/log/server-backup.log 2>&1
CRON
chmod 644 /etc/cron.d/server-backup
/usr/local/bin/server-backup.sh || true

say "5/5  Firewall и автообновления"
ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1 || true
ufw allow 80,443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true

IP="$(curl -fsS -m 5 https://api.ipify.org 2>/dev/null || echo 'IP_СЕРВЕРА')"
cat <<DONE

Сервер готов: $IP

  Caddy:      systemctl status caddy   (конфиги в /etc/caddy, проекты в /etc/caddy/conf.d)
  Проекты:    /opt/projects
  Статика:    /var/www/guvanch.ru
  Бэкапы:     /root/backups (04:00, 14 дней)

Дальше:
  1) Бот расписания:  curl -fsSL .../deploy/install-vps.sh | sudo bash
  2) Лендинг:         rsync -avz personal-site/ root@$IP:/var/www/guvanch.ru/
  3) Новый проект:    curl -fsSL $BASE/new-project.sh -o /usr/local/bin/new-project.sh
                      chmod +x /usr/local/bin/new-project.sh
                      new-project.sh имя домен.guvanch.ru 3000

DNS: A-записи доменов → $IP. Сертификаты Caddy выпустит сам.

DONE
