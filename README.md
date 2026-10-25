# Бот расписания медколледжа

Telegram-бот: учебная часть присылает PDF, бот разбирает таблицу и отдаёт студентам
расписание группы — на день, на неделю, «следующая пара», поиск по преподавателю,
календарь `.ics`. При публикации нового PDF считает дифф и рассылает изменения.

## Запуск на VPS (Ubuntu/Debian, 1 ядро, 1 ГБ)

```bash
curl -fsSL https://raw.githubusercontent.com/mospoliteh2627-alt/schedule-bot-medcollege/main/deploy/install-vps.sh | sudo bash
```

Скрипт спросит токен от @BotFather и ваш Telegram id, поставит зависимости,
создаст systemd-службу и запустит бота.

## Запуск локально (Mac/Linux)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # вписать BOT_TOKEN и ADMIN_IDS
.venv/bin/python -m app.main --check
.venv/bin/python -m app.main
```

## Облако

`Dockerfile` и `render.yaml` — для Render/Koyeb/Hugging Face.
Учтите: на бесплатных тарифах контейнер эфемерный, база `data/schedule.sqlite3`
обнуляется при перезапуске — для постоянной работы нужен диск или VPS.

## Как это работает

`app/pdf_parser.py` — PDF → занятия по координатам текста (PyMuPDF).
`app/importer.py` — предпросмотр и дифф, `app/store.py` — SQLite с версиями и откатом,
`app/handlers_*.py` — кнопки и админка, `app/main.py` — polling и утренний дайджест.

Без токена и без сети бот не стартует — это нормально, токен задаётся в `.env`
(или в переменных окружения на хостинге) и в git не попадает.
