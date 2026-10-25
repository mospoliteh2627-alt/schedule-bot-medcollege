# Образ для облака (Render / Koyeb / Hugging Face / любой Docker-хост).
# Локально не нужен: хватает .venv и `python -m app.main`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# база расписания живёт в /app/data — на облаке подключите диск, если он есть
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["python", "-m", "app.main"]
