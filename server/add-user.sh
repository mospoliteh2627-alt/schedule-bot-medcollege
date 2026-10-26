#!/usr/bin/env bash
# Отдельный пользователь на сервере: своя папка, свои службы, без root.
#
#   sudo ./add-user.sh anna
#   sudo ./add-user.sh anna "ssh-ed25519 AAAA... anna@mac"
#
# Дальше Анна заходит по SSH и ставит проекты в ~/projects (или вы делаете это за неё).
set -euo pipefail
NAME="${1:-}"
[ -n "$NAME" ] || { echo "Использование: $0 <логин> [ssh-ключ]"; exit 1; }
[ "$(id -u)" = "0" ] || { echo "Запустите через sudo."; exit 1; }
KEY="${2:-}"

if id -u "$NAME" >/dev/null 2>&1; then
	echo "Пользователь $NAME уже есть."
else
	adduser --disabled-password --gecos "" "$NAME"
fi

mkdir -p "/home/$NAME/projects"
chown -R "$NAME":"$NAME" "/home/$NAME/projects"
chmod 750 "/home/$NAME"

if [ -n "$KEY" ]; then
	install -d -m 700 -o "$NAME" -g "$NAME" "/home/$NAME/.ssh"
	echo "$KEY" >> "/home/$NAME/.ssh/authorized_keys"
	chown "$NAME":"$NAME" "/home/$NAME/.ssh/authorized_keys"
	chmod 600 "/home/$NAME/.ssh/authorized_keys"
	echo "SSH-ключ добавлен."
fi

cat <<DONE

Пользователь $NAME готов.

  Папка:      /home/$NAME/projects
  Службы:     создаются от его имени:
              new-project.sh сайт домен.ru 3000 --user $NAME --systemd "/home/$NAME/projects/сайт/.venv/bin/python -m app.main"
  Права:      без sudo (root остаётся только у владельца сервера)
  Ресурсы:    лимит задаётся в юните (MemoryMax/CPUQuota)

Подключение:  ssh $NAME@IP
DONE
