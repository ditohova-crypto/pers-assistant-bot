import os
import asyncio
import json
import re
import traceback
from datetime import datetime, time
from typing import Dict, List

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
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

PORT = int(os.environ.get("PORT", "10000"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

MORNING_TIME = time(6, 0)
AFTERNOON_TIME = time(12, 0)
EVENING_TIME = time(18, 0)

DATA_FILE = "bot_data.json"
KIMI_API_URL = "https://api.moonshot.cn/v1/chat/completions"

print("=" * 50)
print("STARTING BOT")
print(f"PORT = {PORT}")
print(f"URL = {RENDER_EXTERNAL_URL}")
print("=" * 50)

# ============ РАБОТА С ДАННЫМИ ============
def load_data() -> Dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tasks": [], "dialog_history": [], "user_preferences": {}}

def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_task(text: str) -> int:
    data = load_data()
    task_id = len(data["tasks"]) + 1
    task = {
        "id": task_id,
        "text": text,
        "created_at": datetime.now().isoformat(),
        "completed": False,
        "completed_at": None
    }
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

# ============ ПОИСК В ИНТЕРНЕТЕ ============
async def web_search(query: str, num_results: int = 5) -> List[Dict]:
    try:
        search_url = "https://html.duckduckgo.com/html/"
        params = {"q": query}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    return []
                html = await response.text()
                results = []
                pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>'
                matches = re.findall(pattern, html)
                for url, title in matches[:num_results]:
                    if url.startswith("//"):
                        url = "https:" + url
                    elif url.startswith("/"):
                        url = "https://duckduckgo.com" + url
                    results.append({"title": title.strip(), "url": url, "source": "web"})
                return results
    except Exception as e:
        print(f"Search error: {e}")
        return []

async def fetch_page_content(url: str, max_length: int = 3000) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as response:
                if response.status != 200:
                    return ""
                html = await response.text()
                html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
                text_parts = re.findall(r'<(?:p|h[1-6]|div)[^>]*>([^<]+)', html)
                text = ' '.join(text_parts)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:max_length]
    except Exception as e:
        print(f"Fetch error: {e}")
        return ""

async def search_and_summarize(query: str) -> str:
    results = await web_search(query, num_results=3)
    if not results:
        return None
    contents = []
    for result in results[:2]:
        content = await fetch_page_content(result["url"])
        if content:
            source_text = "Источник: " + result["title"] + "\n" + content[:1500]
            contents.append(source_text)
    if not contents:
        links_text = "\n".join(["• " + r["title"] + ": " + r["url"] for r in results])
        return "Нашел информацию по запросу:\n" + links_text
    return "\n\n---\n\n".join(contents)

# ============ KIMI AI ============
async def ask_kimi(user_message: str, system_prompt: str = None, web_context: str = None) -> str:
    headers = {
        "Authorization": "Bearer " + KIMI_API_KEY,
        "Content-Type": "application/json"
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    data = load_data()
    history = data.get("dialog_history", [])[-8:]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    if web_context:
        web_msg = "Информация из интернета по запросу пользователя:\n\n" + web_context + "\n\nИспользуй эту информацию для ответа."
        messages.append({"role": "system", "content": web_msg})
    messages.append({"role": "user", "content": user_message})
    payload = {
        "model": "moonshot-v1-8k",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2000
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(KIMI_API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=40)) as response:
                if response.status == 200:
                    result = await response.json()
                    answer = result["choices"][0]["message"]["content"]
                    data["dialog_history"].append({"role": "user", "content": user_message, "time": datetime.now().isoformat()})
                    data["dialog_history"].append({"role": "assistant", "content": answer, "time": datetime.now().isoformat()})
                    save_data(data)
                    return answer
                else:
                    error_text = await response.text()
                    return "Ошибка API: " + str(response.status) + "\n" + error_text
    except Exception as e:
        return "Ошибка соединения: " + str(e)

# ============ ОПРЕДЕЛЕНИЕ НУЖЕН ЛИ ПОИСК ============
def needs_web_search(text: str) -> bool:
    search_indicators = [
        "новости", "сегодня", "вчера", "последние", "актуально",
        "курс", "цена", "погода", "события", "новый", "новое",
        "сколько стоит", "где купить", "отзывы", "рейтинг",
        "что происходит", "что случилось", "последние новости",
        "weather", "news", "price", "current", "today", "latest",
        "какой сейчас", "текущий", "свежие", "обновление"
    ]
    text_lower = text.lower()
    for indicator in search_indicators:
        if indicator in text_lower:
            return True
    return False

# ============ ОБРАБОТЧИКИ КОМАНД ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Извини, я личный ассистент своего владельца.")
        return
    welcome_text = (
        "Привет! Я твой личный ассистент с доступом к интернету.\n\n"
        "Вот что я умею:\n"
        "• /task <текст> — добавить задачу\n"
        "• /done <номер> — отметить задачу выполненной\n"
        "• /tasks — список активных задач\n"
        "• /clear — очистить выполненные задачи\n"
        "• /search <запрос> — поиск в интернете\n"
        "• /remind <время> <текст> — быстрое напоминание\n\n"
        "Просто задавай вопросы — если нужна свежая информация, я сам найду ее в интернете!\n\n"
        "Примеры:\n"
        "• Какие новости в IT сегодня?\n"
        "• Курс доллара сейчас\n"
        "• Погода в Москве"
    )
    await update.message.reply_text(welcome_text)

async def add_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Укажи текст задачи: /task Купить молоко")
        return
    task_text = " ".join(context.args)
    task_id = add_task(task_text)
    await update.message.reply_text("Задача #" + str(task_id) + " добавлена: " + task_text)

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        tasks = get_active_tasks()
        if not tasks:
            await update.message.reply_text("Нет активных задач!")
            return
        text = "Активные задачи:\n\n"
        for t in tasks:
            text += "#" + str(t["id"]) + ": " + t["text"] + "\n"
        text += "\nОтметь выполненную: /done <номер>"
        await update.message.reply_text(text)
        return
    try:
        task_id = int(context.args[0])
        if complete_task(task_id):
            await update.message.reply_text("Задача #" + str(task_id) + " выполнена! Молодец!")
        else:
            await update.message.reply_text("Задача не найдена или уже выполнена.")
    except ValueError:
        await update.message.reply_text("Укажи номер задачи: /done 1")

async def list_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    tasks = get_active_tasks()
    completed_today = get_completed_tasks_today()
    text = "Твои дела:\n\n"
    if tasks:
        text += "Активные:\n"
        for t in tasks:
            text += "  " + t["text"] + "\n"
    else:
        text += "Активных задач нет\n"
    if completed_today:
        text += "\nСегодня выполнено: " + str(len(completed_today)) + "\n"
        for t in completed_today:
            text += "  " + t["text"] + "\n"
    await update.message.reply_text(text)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    data = load_data()
    active = [t for t in data["tasks"] if not t["completed"]]
    cleared = len(data["tasks"]) - len(active)
    data["tasks"] = active
    save_data(data)
    await update.message.reply_text("Очищено " + str(cleared) + " выполненных задач. Активных: " + str(len(active)))

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Укажи запрос: /search последние новости IT")
        return
    query = " ".join(context.args)
    await update.message.reply_text("Ищу: " + query + "...")
    web_context = await search_and_summarize(query)
    if not web_context:
        await update.message.reply_text("Не удалось найти информацию. Попробуй другой запрос.")
        return
    system_prompt = "Ты — личный ассистент. Ответь на вопрос пользователя, используя предоставленную информацию из интернета. Давай краткий, структурированный ответ."
    answer = await ask_kimi("Вопрос: " + query + "\n\nПроанализируй информацию и ответь.", system_prompt, web_context)
    await update.message.reply_text(answer)

async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /remind 14:30 Позвонить маме")
        return
    time_str = context.args[0]
    reminder_text = " ".join(context.args[1:])
    await update.message.reply_text("Напоминание установлено на " + time_str + ": " + reminder_text)

# ============ ОСНОВНОЙ ОБРАБОТЧИК ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    user_text = update.message.text
    task_patterns = [
        r"^(добавь|новая|запиши|создай)\s+задач[уи]",
        r"^задача[:;]",
        r"^напомни\s+(мне\s+)?(что|про|о)",
    ]
    for pattern in task_patterns:
        if re.search(pattern, user_text.lower()):
            task_text = re.sub(pattern, "", user_text, flags=re.IGNORECASE).strip(" :;")
            if task_text:
                task_id = add_task(task_text)
                await update.message.reply_text("Задача #" + str(task_id) + " добавлена: " + task_text)
                return
    need_search = needs_web_search(user_text)
    web_context = None
    if need_search:
        await update.message.reply_text("Ищу актуальную информацию в интернете...")
        web_context = await search_and_summarize(user_text)
        if web_context:
            await update.message.reply_text("Нашел информацию, анализирую...")
    system_prompt = "Ты — личный ассистент пользователя. Ты помогаешь организовывать дела, даешь советы по продуктивности, поддерживаешь мотивацию. Отвечай дружелюбно, по-русски, кратко и по делу."
    active_tasks = get_active_tasks()
    if active_tasks:
        tasks_context = "Активные задачи пользователя:\n" + "\n".join(["- " + t["text"] for t in active_tasks])
        system_prompt += "\n\n" + tasks_context
    answer = await ask_kimi(user_text, system_prompt, web_context)
    await update.message.reply_text(answer)

# ============ НАПОМИНАНИЯ ============
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    if tasks:
        text = "Доброе утро! Твои задачи на сегодня:\n\n"
        for t in tasks:
            text += "  " + t["text"] + "\n"
        text += "\nУдачного дня!"
    else:
        text = "Доброе утро! У тебя нет запланированных задач. Отличный день для новых свершений!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def afternoon_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    if tasks:
        text = "Полдень! Проверь прогресс:\n\n"
        text += "Выполнено сегодня: " + str(len(completed)) + "\n"
        text += "Осталось: " + str(len(tasks)) + "\n\n"
        for t in tasks:
            text += "  " + t["text"] + "\n"
        text += "\nТы справишься!"
    else:
        text = "Полдень! Все задачи на сегодня выполнены — ты супергерой!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    text = "Вечерний брифинг:\n\n"
    text += "Сегодня выполнено: " + str(len(completed)) + "\n"
    if tasks:
        text += "Перенесено на завтра: " + str(len(tasks)) + "\n"
        for t in tasks:
            text += "  " + t["text"] + "\n"
        text += "\nОтдохни — завтра новый день!"
    else:
        text += "Все задачи выполнены! Отличная работа сегодня!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

# ============ WEBHOOK + ЗАПУСК ============
async def main():
    if not os.path.exists(DATA_FILE):
        save_data({"tasks": [], "dialog_history": [], "user_preferences": {}})

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("task", add_task_cmd))
    application.add_handler(CommandHandler("done", done_cmd))
    application.add_handler(CommandHandler("tasks", list_tasks_cmd))
    application.add_handler(CommandHandler("clear", clear_cmd))
    application.add_handler(CommandHandler("search", search_cmd))
    application.add_handler(CommandHandler("remind", remind_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = application.job_queue
    job_queue.run_daily(morning_reminder, time=MORNING_TIME)
    job_queue.run_daily(afternoon_reminder, time=AFTERNOON_TIME)
    job_queue.run_daily(evening_reminder, time=EVENING_TIME)

    webhook_url = RENDER_EXTERNAL_URL + "/telegram-webhook"
    print("Webhook URL: " + webhook_url)

    try:
        await application.initialize()
        await application.start()

        await application.bot.delete_webhook(drop_pending_updates=True)
        print("Старый webhook удален")

        await application.bot.set_webhook(webhook_url)
        print("Новый webhook установлен: " + webhook_url)

        # HTTP сервер
        app = web.Application()

        async def webhook_handler(request):
            try:
                data = await request.json()
                update = Update.de_json(data, application.bot)
                await application.process_update(update)
                return web.Response(text="OK")
            except Exception as e:
                print("Webhook error:", e)
                traceback.print_exc()
                return web.Response(text="Error", status=500)

        async def health_handler(request):
            return web.Response(text="OK - Bot is running")

        app.router.add_post("/telegram-webhook", webhook_handler)
        app.router.add_get("/health", health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        print("Бот запущен на webhook!")
        print("Health: http://0.0.0.0:" + str(PORT) + "/health")

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        print("FATAL ERROR:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
