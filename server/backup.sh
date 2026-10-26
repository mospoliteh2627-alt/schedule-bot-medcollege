#!/usr/bin/env bash
# Бэкап всех проектов сервера. Запускается по cron: /etc/cron.d/server-backup
set -uo pipefail
DEST="${DEST:-/root/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%F)"
mkdir -p "$DEST"

# 1. код, данные и настройки (без node_modules и venv — они восстанавливаются)
tar czf "$DEST/projects-$STAMP.tgz" \
	--exclude='node_modules' --exclude='.venv' --exclude='__pycache__' --exclude='*.log' \
	-C / opt/projects 2>/dev/null

# проекты других людей: /home/<логин>/projects
for home in /home/*/; do
	user="$(basename "$home")"
	[ -d "$home/projects" ] || continue
	tar czf "$DEST/projects-$user-$STAMP.tgz" \
		--exclude='node_modules' --exclude='.venv' --exclude='__pycache__' --exclude='*.log' \
		-C "$home" projects 2>/dev/null
done

tar czf "$DEST/www-$STAMP.tgz" -C / var/www 2>/dev/null
tar czf "$DEST/caddy-$STAMP.tgz" -C / etc/caddy 2>/dev/null

# 2. Postgres, если он крутится в docker
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx postgres; then
	docker exec postgres pg_dumpall -U postgres | gzip > "$DEST/postgres-$STAMP.sql.gz" 2>/dev/null
fi

find "$DEST" -name '*.tgz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null
find "$DEST" -name '*.sql.gz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null
echo "$(date '+%F %T') backup ok: $(ls -1 "$DEST" | wc -l | tr -d ' ') файлов"
