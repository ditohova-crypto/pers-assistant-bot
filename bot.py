import os
import asyncio
import json
import re
import traceback
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional

import aiohttp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from aiohttp import web

# ============ КОНФИГУРАЦИЯ ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
API_KEY = os.environ.get("API_KEY", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

PORT = int(os.environ.get("PORT", "10000"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

DATA_FILE = "/app/data/bot_data.json"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "kimi-k2.6"

# МАКСИМАЛЬНО ЧЁТКИЙ промпт — без двусмысленности
SYSTEM_PROMPT = (
    "Ты — AI-ассистент по имени Кими. Ты — программа, созданная для помощи.\n"
    "Твоя владелица — Диана. Она — человек, ты — искусственный интеллект.\n"
    "Ты НЕ Диана. Диана — твоя пользовательница.\n"
    "Ты помогаешь Диане организовывать дела, даёшь советы, поддерживаешь.\n"
    "Ты дружелюбная, энергичная, с чувством юмора.\n"
    "Всегда отвечай от своего имени (Кими) и помни, что Диана — отдельный человек.\n"
    "Если Диана рассказывает о себе — запоминай это и используй в будущем.\n"
    "Отвечай по-русски, кратко, по делу."
)

print("=" * 50)
print("STARTING BOT")
print(f"PORT = {PORT}")
print("=" * 50)

# ============ РАБОТА С ДАННЫМИ ============
def load_data() -> Dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"tasks": [], "dialog_history": [], "reminders": [], "user_facts": []}
    return {"tasks": [], "dialog_history": [], "reminders": [], "user_facts": []}

def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_to_history(role: str, content: str):
    data = load_data()
    if "dialog_history" not in data:
        data["dialog_history"] = []
    data["dialog_history"].append({
        "role": role,
        "content": content,
        "time": datetime.now().isoformat()
    })
    data["dialog_history"] = data["dialog_history"][-40:]
    save_data(data)

def get_history() -> List[Dict]:
    data = load_data()
    return data.get("dialog_history", [])[-20:]

def add_user_fact(fact: str):
    data = load_data()
    if "user_facts" not in data:
        data["user_facts"] = []
    # Проверяем, нет ли уже такого факта
    for f in data["user_facts"]:
        if f["fact"] == fact:
            return
    data["user_facts"].append({"fact": fact, "time": datetime.now().isoformat()})
    data["user_facts"] = data["user_facts"][-25:]
    save_data(data)

def get_user_facts() -> str:
    data = load_data()
    facts = data.get("user_facts", [])
    if not facts:
        return ""
    return "Я знаю о Диане:\n" + "\n".join([f"- {f['fact']}" for f in facts])

# ============ ЗАДАЧИ ============
def add_task(text: str) -> int:
    data = load_data()
    task_id = len(data["tasks"]) + 1
    task = {"id": task_id, "text": text, "created_at": datetime.now().isoformat(), "completed": False, "completed_at": None}
    data["tasks"].append(task)
    save_data(data)
    return task_id

def complete_task(task_id: int) -> bool:
    data = load_data()
    for task in data["tasks"]:
        if task["id"] == task_id and not task["completed"]:
            task["completed"] = True
            task["completed_at"] = datetime.now().isoformat()
            save_data(data)
            return True
    return False

def get_active_tasks() -> List[Dict]:
    data = load_data()
    return [t for t in data["tasks"] if not t["completed"]]

def get_completed_tasks_today() -> List[Dict]:
    data = load_data()
    today = datetime.now().date().isoformat()
    return [t for t in data["tasks"] if t["completed"] and t["completed_at"].startswith(today)]

# ============ НАПОМИНАНИЯ ============
def parse_reminder(text: str) -> Optional[Dict]:
    text_lower = text.lower()
    now = datetime.now()

    through_match = re.search(r'через\s+(\d+)\s+(минут|минуту|час|часа|часов)', text_lower)
    if through_match:
        amount = int(through_match.group(1))
        unit = through_match.group(2)
        delta = timedelta(minutes=amount) if 'минут' in unit else timedelta(hours=amount)
        task_text = re.sub(r'через\s+\d+\s+(минут|минуту|час|часа|часов)', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": (now + delta).isoformat(), "repeat": None, "created": now.isoformat()}

    tomorrow_match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if tomorrow_match:
        hour, minute = int(tomorrow_match.group(1)), int(tomorrow_match.group(2))
        remind_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        task_text = re.sub(r'завтра\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    today_match = re.search(r'сегодня\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if today_match:
        hour, minute = int(today_match.group(1)), int(today_match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time < now:
            remind_time += timedelta(days=1)
        task_text = re.sub(r'сегодня\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    daily_match = re.search(r'каждый\s+день\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if daily_match:
        hour, minute = int(daily_match.group(1)), int(daily_match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time < now:
            remind_time += timedelta(days=1)
        task_text = re.sub(r'каждый\s+день\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": "daily", "created": now.isoformat()}

    time_match = re.search(r'в\s+(\d{1,2}):(\d{2})', text_lower)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time < now:
            remind_time += timedelta(days=1)
        task_text = re.sub(r'в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    return None

def add_reminder(reminder: Dict) -> int:
    data = load_data()
    if "reminders" not in data:
        data["reminders"] = []
    reminder_id = len(data["reminders"]) + 1
    reminder["id"] = reminder_id
    data["reminders"].append(reminder)
    save_data(data)
    return reminder_id

def get_active_reminders() -> List[Dict]:
    data = load_data()
    now = datetime.now().isoformat()
    return [r for r in data.get("reminders", []) if r["time"] <= now and not r.get("sent", False)]

def mark_reminder_sent(reminder_id: int):
    data = load_data()
    for r in data.get("reminders", []):
        if r["id"] == reminder_id:
            r["sent"] = True
            if r.get("repeat") == "daily":
                next_time = datetime.fromisoformat(r["time"]) + timedelta(days=1)
                new_r = {
                    "id": len(data["reminders"]) + 1,
                    "text": r["text"],
                    "time": next_time.isoformat(),
                    "repeat": "daily",
                    "created": datetime.now().isoformat(),
                    "sent": False
                }
                data["reminders"].append(new_r)
            save_data(data)
            break

# ============ AI ============
async def ask_ai(user_message: str, extra_context: str = "") -> str:
    headers = {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_EXTERNAL_URL,
        "X-Title": "Telegram Assistant"
    }

    messages = []

    # 1. Жёсткая системная инструкция
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    # 2. Факты о пользователе
    facts = get_user_facts()
    if facts:
        messages.append({"role": "system", "content": facts})

    # 3. Задачи
    tasks = get_active_tasks()
    if tasks:
        tasks_text = "Задачи Дианы:\n" + "\n".join(["- " + t["text"] for t in tasks])
        messages.append({"role": "system", "content": tasks_text})

    # 4. Дополнительный контекст
    if extra_context:
        messages.append({"role": "system", "content": extra_context})

    # 5. История диалога
    history = get_history()
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # 6. Текущее сообщение (явно указываем, что это Диана пишет)
    messages.append({"role": "user", "content": "Диана говорит: " + user_message})

    payload = {"model": MODEL, "messages": messages, "temperature": 0.6, "max_tokens": 2000}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as response:
                if response.status == 200:
                    result = await response.json()
                    answer = result["choices"][0]["message"]["content"]
                    add_to_history("user", user_message)
                    add_to_history("assistant", answer)
                    return answer
                else:
                    error_text = await response.text()
                    return "Ошибка API: " + str(response.status)
    except Exception as e:
        return "Ошибка: " + str(e)

# ============ ОБРАБОТЧИКИ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Извини, я не твой ассистент.")
        return
    await update.message.reply_text(
        "Привет, Диана! 👋 Я Кими, твой ассистент.\n\n"
        "Что я умею:\n"
        "• Добавлять задачи: /task или просто напиши\n"
        "• Напоминания: 'Напомни завтра в 10:30...'\n"
        "• Вести дела: /tasks, /done, /clear\n"
        "• Запоминать о тебе всё\n\n"
        "Просто пиши мне!"
    )

async def add_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Укажи текст: /task Купить молоко")
        return
    task_text = " ".join(context.args)
    task_id = add_task(task_text)
    await update.message.reply_text(f"✅ Задача #{task_id}: {task_text}")

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        tasks = get_active_tasks()
        if not tasks:
            await update.message.reply_text("Нет задач!")
            return
        text = "Активные:\n" + "\n".join([f"#{t['id']}: {t['text']}" for t in tasks])
        await update.message.reply_text(text + "\n\nОтметь: /done <номер>")
        return
    try:
        task_id = int(context.args[0])
        if complete_task(task_id):
            await update.message.reply_text(f"🎉 Задача #{task_id} выполнена!")
        else:
            await update.message.reply_text("Не найдена или уже сделана.")
    except ValueError:
        await update.message.reply_text("Укажи номер: /done 1")

async def list_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    text = "📋 Дела:\n\n"
    if tasks:
        text += "Активные:\n" + "\n".join([f"  {t['text']}" for t in tasks]) + "\n"
    else:
        text += "Активных нет\n"
    if completed:
        text += f"\nСегодня сделано: {len(completed)}"
    await update.message.reply_text(text)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    data = load_data()
    active = [t for t in data["tasks"] if not t["completed"]]
    cleared = len(data["tasks"]) - len(active)
    data["tasks"] = active
    save_data(data)
    await update.message.reply_text(f"🧹 Очищено: {cleared}. Активных: {len(active)}")

async def list_reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    data = load_data()
    reminders = data.get("reminders", [])
    if not reminders:
        await update.message.reply_text("Нет напоминаний.")
        return
    text = "⏰ Напоминания:\n\n"
    for r in reminders[-10:]:
        time_str = datetime.fromisoformat(r["time"]).strftime("%d.%m %H:%M")
        repeat = " (ежедневно)" if r.get("repeat") == "daily" else ""
        status = " ✅" if r.get("sent") else ""
        text += f"#{r['id']}: {time_str}{repeat} — {r['text']}{status}\n"
    await update.message.reply_text(text)

# ============ ГЛАВНЫЙ ОБРАБОТЧИК ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    user_text = update.message.text
    text_lower = user_text.lower()

    # Напоминание?
    reminder = parse_reminder(user_text)
    if reminder and ("напомни" in text_lower or "напоминай" in text_lower):
        rid = add_reminder(reminder)
        time_str = datetime.fromisoformat(reminder["time"]).strftime("%d.%m %H:%M")
        repeat = " (ежедневно)" if reminder.get("repeat") == "daily" else ""
        await update.message.reply_text(f"⏰ Напоминание #{rid}: {time_str}{repeat}\n{reminder['text']}")
        return

    # Задача?
    if re.search(r'^(добавь|новая|запиши|создай)\s+задач[уи]', text_lower) or text_lower.startswith("задача:"):
        task_text = re.sub(r'^(добавь|новая|запиши|создай)\s+задач[уи]|задача:', '', text_lower, flags=re.IGNORECASE).strip(' :;')
        if task_text:
            tid = add_task(task_text)
            await update.message.reply_text(f"✅ Задача #{tid}: {task_text}")
            return

    # Сохраняем факты о Диане
    fact_keywords = ["я ", "мне ", "мой ", "моя ", "меня ", "мне нравится", "я люблю", "я работаю", "я живу", "я из", "я учусь"]
    for kw in fact_keywords:
        if text_lower.startswith(kw) or f" {kw}" in text_lower:
            add_user_fact(user_text)
            break

    # Отправляем в AI
    answer = await ask_ai(user_text)
    await update.message.reply_text(answer)

# ============ АВТОМАТИКА ============
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    for r in get_active_reminders():
        await context.bot.send_message(chat_id=OWNER_ID, text=f"⏰ Напоминание!\n\n{r['text']}")
        mark_reminder_sent(r["id"])

async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    if tasks:
        text = "🌅 Доброе утро, Диана!\n\nЗадачи на сегодня:\n" + "\n".join([f"  {t['text']}" for t in tasks])
    else:
        text = "🌅 Доброе утро, Диана! Задач нет — отличный день!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def afternoon_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    if tasks:
        text = f"☀️ Диана, полдень!\n\n✅ Сделано: {len(completed)}\n⬜ Осталось: {len(tasks)}\n\n" + "\n".join([f"  {t['text']}" for t in tasks])
    else:
        text = "☀️ Диана, полдень! Всё сделано — ты молодец!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    text = f"🌙 Диана, вечерний брифинг:\n\n📊 Сегодня: {len(completed)}"
    if tasks:
        text += f"\n📋 Завтра: {len(tasks)}\n" + "\n".join([f"  {t['text']}" for t in tasks])
    else:
        text += "\n🎉 Всё сделано!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

# ============ ЗАПУСК ============
async def main():
    os.makedirs("/app/data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_data({"tasks": [], "dialog_history": [], "reminders": [], "user_facts": []})

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("task", add_task_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("tasks", list_tasks_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("reminders", list_reminders_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    jq = app.job_queue
    jq.run_daily(morning_reminder, time=time(6, 0))
    jq.run_daily(afternoon_reminder, time=time(12, 0))
    jq.run_daily(evening_reminder, time=time(18, 0))
    jq.run_repeating(check_reminders, interval=60, first=10)

    webhook_url = RENDER_EXTERNAL_URL + "/telegram-webhook"

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.bot.set_webhook(webhook_url)

    web_app = web.Application()

    async def wh(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        return web.Response(text="OK")

    async def health(request):
        return web.Response(text="OK - Кими для Дианы")

    web_app.router.add_post("/telegram-webhook", wh)
    web_app.router.add_get("/health", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    print("Кими запущен для Дианы!")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
