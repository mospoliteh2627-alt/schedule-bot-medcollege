# Один сервер для всех проектов

Схема: **Caddy** слушает 80/443, сам выпускает HTTPS-сертификаты и разводит домены
по локальным портам. Каждый проект — отдельный каталог и своя служба. Наружу открыты
только 22, 80 и 443.

```
guvanch.ru ─────────────► /var/www/guvanch.ru            (лендинг, статика)
banking.guvanch.ru ─────► 127.0.0.1:8787                 (Вектор Банк, docker)
<admin.guvanch.ru> ─────► 127.0.0.1:8000                 (веб-админка расписания, позже)
Telegram-бот ───────────► без порта: long polling, исходящие к api.telegram.org
```

## Сколько брать

| Проекты | RAM | vCPU | Диск | Цена |
|---|---|---|---|---|
| только бот + лендинг | 1 ГБ | 1 | 15 ГБ | 250–350 ₽ |
| **бот + лендинг + банк + запас** | **2 ГБ** | **2** | **40 ГБ** | **450–700 ₽** |
| + Postgres, Redis, несколько Node-сервисов | 4 ГБ | 2–4 | 60 ГБ | 900–1400 ₽ |

Docker + Node + Postgres на 1 ГБ живут, но без запаса: обновление образа может уронить
сервер по памяти. Если планируете «крутить другие проекты» — берите 2 ГБ сразу,
апгрейд в панели обычно делается в два клика и без переустановки.

## Установка базы (5 минут)

```bash
curl -fsSL https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/server/install-server.sh | sudo bash
```

Что делает: ставит Caddy и настраивает авто-HTTPS, создаёт `/opt/projects`, `/var/www`,
включает ежедневные бэкапы в `/root/backups` (04:00, хранение 14 дней), firewall
(22/80/443) и автообновления безопасности.

## Добавить проект

```bash
curl -fsSL .../server/new-project.sh -o /usr/local/bin/new-project.sh && chmod +x /usr/local/bin/new-project.sh

# только прокси на порт (для docker-проектов)
new-project.sh banking banking.guvanch.ru 8787

# прокси + systemd-служба
new-project.sh mysite mysite.guvanch.ru 3000 --systemd "/opt/projects/mysite/.venv/bin/python -m app.main"
```

Скрипт создаст `/etc/caddy/conf.d/<имя>.caddy`, юнит `/etc/systemd/system/<имя>.service`
(если передан `--systemd`), перезагрузит Caddy и напомнит про A-запись DNS.

## Правила, чтобы не сломать соседей

1. **Порт наружу не публикуем.** Приложение слушает `127.0.0.1:ПОРТ`, наружу — только Caddy
   (в docker-compose: `"127.0.0.1:8787:8787"`).
2. **Своя папка и свои данные.** `/opt/projects/<имя>/data` — их и забирает бэкап.
3. **Не ставить панели** (ISPmanager/cPanel): 300–500 МБ памяти и лишняя точка отказа.
4. **Секреты — в `.env`** рядом с проектом, права `600`, в git не попадают.
5. **Обновления системы** — `unattended-upgrades` включён установщиком; крупные апгрейды
   (Postgres, Docker) — вручную и с бэкапом.

## Обслуживание

```bash
systemctl status caddy schedule-bot          # службы
journalctl -u caddy -f                       # логи прокси и сертификатов
journalctl -u schedule-bot -f                # логи бота
caddy validate --config /etc/caddy/Caddyfile # проверка конфига
/usr/local/bin/server-backup.sh              # бэкап вручную
df -h && free -h && docker stats --no-stream # ресурсы
```

## Что где лежит

| Путь | Что |
|---|---|
| `/etc/caddy/Caddyfile` | общий конфиг: импорт `conf.d/*.caddy` |
| `/etc/caddy/conf.d/*.caddy` | по файлу на домен |
| `/opt/projects/<имя>` | код и данные проектов |
| `/var/www/guvanch.ru` | статика лендинга |
| `/root/backups` | архивы: проекты, www, caddy, дамп Postgres |
