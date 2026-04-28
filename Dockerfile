FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir python-telegram-bot[job-queue]==20.7 aiohttp==3.9.1

COPY . .

CMD ["python", "bot.py"]
