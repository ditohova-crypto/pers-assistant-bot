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

BOT_PERSONALITY = (
    "Ты — личный ассистент пользователя по имени Диана. "
    "Ты помогаешь организовывать дела, даешь советы по продуктивности, поддерживаешь мотивацию. "
    "Ты дружелюбная, энергичная, немного игривая. "
    "Ты помнишь все предыдущие разговоры с пользователем и можешь на них ссылаться. "
    "Ты знаешь о задачах пользователя. "
    "Отвечай по-русски, кратко и по делу, но с теплом."
)

print("=" * 50)
print("STARTING BOT")
print(f"PORT = {PORT}")
print(f"URL = {RENDER_EXTERNAL_URL}")
print("=" * 50)

# ============ РАБОТА С ДАННЫМИ ============
def load_data() -> Dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"tasks": [], "dialog_history": [], "user_preferences": {}, "reminders": []}
    return {"tasks": [], "dialog_history": [], "user_preferences": {}, "reminders": []}

def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_to_history(role: str, content: str):
    data = load_data()
    if "dialog_history" not in data:
        data["dialog_history"] = []
    data["dialog_history"].append({"role": role, "content": content, "time": datetime.now().isoformat()})
    data["dialog_history"] = data["dialog_history"][-30:]
    save_data(data)

def get_history() -> List[Dict]:
    data = load_data()
    return data.get("dialog_history", [])[-20:]

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

# ============ ПОИСК ЧЕРЕЗ SearXNG ============
async def searx_search(query: str, num_results: int = 5) -> List[Dict]:
    """Поиск через публичные SearXNG инстансы."""
    # Список публичных SearXNG серверов
    instances = [
        "https://search.sapti.me",
        "https://search.bus-hit.me",
        "https://search.datura.network",
        "https://searx.be",
        "https://searx.tiekoetter.com",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "application/json",
    }

    for instance in instances:
        try:
            url = instance + "/search"
            params = {
                "q": query,
                "format": "json",
                "language": "ru-RU",
                "safesearch": "1",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        result = await response.json()
                        results = []
                        for item in result.get("results", [])[:num_results]:
                            results.append({
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "content": item.get("content", "")
                            })
                        if results:
                            print(f"SearXNG found {len(results)} results via {instance}")
                            return results
        except Exception as e:
            print(f"SearXNG {instance} failed: {e}")
            continue

    return []

# ============ ПОИСК ЧЕРЕЗ DuckDuckGo Lite ============
async def ddg_lite_search(query: str, num_results: int = 5) -> List[Dict]:
    """Поиск через DuckDuckGo Lite (упрощённая версия, меньше защиты)."""
    try:
        url = "https://lite.duckduckgo.com/lite/"
        data = {
            "q": query,
            "kl": "ru-ru",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://lite.duckduckgo.com/",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    return []

                html = await response.text()
                results = []

                # Парсим результаты DuckDuckGo Lite
                pattern = r'<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
                matches = re.findall(pattern, html, re.DOTALL)

                for url, title_html in matches[:num_results]:
                    title = re.sub(r'<[^>]+>', '', title_html).strip()
                    results.append({"title": title, "url": url, "content": ""})

                return results
    except Exception as e:
        print(f"DDG Lite error: {e}")
        return []

# ============ ОБЩИЙ ПОИСК ============
async def web_search(query: str, num_results: int = 5) -> List[Dict]:
    """Пробуем несколько поисковиков."""
    # Сначала SearXNG
    results = await searx_search(query, num_results)
    if results:
        return results

    # Потом DuckDuckGo Lite
    results = await ddg_lite_search(query, num_results)
    if results:
        return results

    return []

async def fetch_page_content(url: str, max_length: int = 3000) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as response:
                if response.status != 200:
                    return ""
                html = await response.text()
                html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
                text_parts = re.findall(r'<(?:p|h[1-6]|div|article|section)[^>]*>([^<]+)', html)
                text = ' '.join(text_parts)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:max_length]
    except Exception as e:
        print(f"Fetch error: {e}")
        return ""

async def search_and_summarize(query: str) -> str:
    results = await web_search(query, num_results=5)
    if not results:
        return None

    contents = []
    for result in results[:3]:
        content = result.get("content", "")
        title = result.get("title", "")
        url = result.get("url", "")

        # Если есть контент от SearXNG — используем его
        if content and len(content) > 100:
            source_text = f"Источник: {title}\nURL: {url}\n{content[:1500]}"
        else:
            # Иначе пробуем получить со страницы
            page_content = await fetch_page_content(url)
            if page_content and len(page_content) > 200:
                source_text = f"Источник: {title}\nURL: {url}\n{page_content[:1500]}"
            else:
                source_text = f"Источник: {title}\nURL: {url}"

        contents.append(source_text)

    return "\n\n---\n\n".join(contents)

# ============ AI (OpenRouter) ============
async def ask_ai(user_message: str, system_prompt: str = None, web_context: str = None) -> str:
    headers = {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_EXTERNAL_URL,
        "X-Title": "Telegram Assistant Bot"
    }
    messages = []
    messages.append({"role": "system", "content": BOT_PERSONALITY})
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    history = get_history()
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    if web_context:
        web_msg = "Информация из интернета по запросу пользователя:\n\n" + web_context + "\n\nИспользуй эту информацию для ответа."
        messages.append({"role": "system", "content": web_msg})
    messages.append({"role": "user", "content": user_message})
    payload = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 2000}

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
                    return "Ошибка API: " + str(response.status) + "\n" + error_text
    except Exception as e:
        return "Ошибка соединения: " + str(e)

# ============ НАПОМИНАНИЯ ============
def parse_reminder(text: str) -> Optional[Dict]:
    text_lower = text.lower()
    now = datetime.now()

    # "через X минут/часов"
    through_match = re.search(r'через\s+(\d+)\s+(минут|минуту|час|часа|часов)', text_lower)
    if through_match:
        amount = int(through_match.group(1))
        unit = through_match.group(2)
        if 'минут' in unit:
            remind_time = now + timedelta(minutes=amount)
        else:
            remind_time = now + timedelta(hours=amount)
        task_text = re.sub(r'через\s+\d+\s+(минут|минуту|час|часа|часов)', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    # "завтра в HH:MM"
    tomorrow_match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if tomorrow_match:
        hour = int(tomorrow_match.group(1))
        minute = int(tomorrow_match.group(2))
        remind_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        task_text = re.sub(r'завтра\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    # "сегодня в HH:MM"
    today_match = re.search(r'сегодня\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if today_match:
        hour = int(today_match.group(1))
        minute = int(today_match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time < now:
            remind_time += timedelta(days=1)
        task_text = re.sub(r'сегодня\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": None, "created": now.isoformat()}

    # "каждый день в HH:MM"
    daily_match = re.search(r'каждый\s+день\s+в\s+(\d{1,2}):(\d{2})', text_lower)
    if daily_match:
        hour = int(daily_match.group(1))
        minute = int(daily_match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time < now:
            remind_time += timedelta(days=1)
        task_text = re.sub(r'каждый\s+день\s+в\s+\d{1,2}:\d{2}', '', text, flags=re.IGNORECASE).strip(' ,')
        return {"text": task_text, "time": remind_time.isoformat(), "repeat": "daily", "created": now.isoformat()}

    # "в HH:MM"
    time_match = re.search(r'в\s+(\d{1,2}):(\d{2})', text_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
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
    reminders = data.get("reminders", [])
    active = []
    for r in reminders:
        if r["time"] <= now and not r.get("sent", False):
            active.append(r)
    return active

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

# ============ ОБРАБОТЧИКИ КОМАНД ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("Извини, я личный ассистент своего владельца.")
        return
    welcome_text = (
        "Привет! Я твой личный ассистент Kimi K2.6 с памятью.\n\n"
        "Вот что я умею:\n"
        "• /task <текст> — добавить задачу\n"
        "• /done <номер> — отметить задачу выполненной\n"
        "• /tasks — список активных задач\n"
        "• /clear — очистить выполненные задачи\n"
        "• /search <запрос> — поиск в интернете\n"
        "• /reminders — список напоминаний\n\n"
        "💡 Напоминания — просто напиши:\n"
        "• Напомни мне завтра в 10:30 позвонить маме\n"
        "• Каждый день в 8:00 напоминай про зарядку\n"
        "• Через 2 часа напомни про встречу\n\n"
        "🔍 Поиск — спрашивай про новости, курсы, погоду!"
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
    await update.message.reply_text("🔍 Ищу в интернете: " + query + "...")
    web_context = await search_and_summarize(query)
    if not web_context:
        await update.message.reply_text("Не удалось найти информацию. Попробуй другой запрос.")
        return
    system_prompt = "Ты — личный ассистент. Ответь на вопрос пользователя, используя предоставленную информацию из интернета. Давай краткий, структурированный ответ с источниками."
    answer = await ask_ai("Вопрос: " + query + "\n\nПроанализируй информацию и ответь.", system_prompt, web_context)
    await update.message.reply_text(answer)

async def list_reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    data = load_data()
    reminders = data.get("reminders", [])
    if not reminders:
        await update.message.reply_text("Нет напоминаний.")
        return
    text = "Твои напоминания:\n\n"
    for r in reminders[-10:]:
        time_str = datetime.fromisoformat(r["time"]).strftime("%d.%m %H:%M")
        repeat = " (каждый день)" if r.get("repeat") == "daily" else ""
        status = " ✅" if r.get("sent") else ""
        text += f"#{r['id']}: {time_str}{repeat} — {r['text']}{status}\n"
    await update.message.reply_text(text)

# ============ ОСНОВНОЙ ОБРАБОТЧИК ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    user_text = update.message.text

    # Проверяем, не напоминание ли это
    reminder = parse_reminder(user_text)
    if reminder and ("напомни" in user_text.lower() or "напоминай" in user_text.lower()):
        reminder_id = add_reminder(reminder)
        time_str = datetime.fromisoformat(reminder["time"]).strftime("%d.%m %Y %H:%M")
        repeat_text = " (каждый день)" if reminder.get("repeat") == "daily" else ""
        await update.message.reply_text(
            f"⏰ Напоминание #{reminder_id} установлено!\n"
            f"Время: {time_str}{repeat_text}\n"
            f"Текст: {reminder['text']}"
        )
        return

    # Проверяем, не задача ли это
    task_patterns = [
        r"^(добавь|новая|запиши|создай)\s+задач[уи]",
        r"^задача[:;]",
    ]
    for pattern in task_patterns:
        if re.search(pattern, user_text.lower()):
            task_text = re.sub(pattern, "", user_text, flags=re.IGNORECASE).strip(" :;")
            if task_text:
                task_id = add_task(task_text)
                await update.message.reply_text("Задача #" + str(task_id) + " добавлена: " + task_text)
                return

    # Определяем, нужен ли поиск
    search_indicators = [
        "новости", "сегодня", "вчера", "последние", "актуально",
        "курс", "цена", "погода", "события", "новый", "новое",
        "сколько стоит", "где купить", "отзывы", "рейтинг",
        "что происходит", "что случилось", "последние новости",
        "weather", "news", "price", "current", "today", "latest",
        "какой сейчас", "текущий", "свежие", "обновление"
    ]
    text_lower = user_text.lower()
    need_search = any(indicator in text_lower for indicator in search_indicators)

    web_context = None
    if need_search:
        await update.message.reply_text("🔍 Ищу в интернете...")
        web_context = await search_and_summarize(user_text)
        if web_context:
            await update.message.reply_text("📡 Нашел информацию, анализирую...")

    # Контекст задач
    active_tasks = get_active_tasks()
    tasks_context = ""
    if active_tasks:
        tasks_context = "Активные задачи пользователя:\n" + "\n".join(["- " + t["text"] for t in active_tasks])

    # Отправляем в AI
    answer = await ask_ai(user_text, tasks_context, web_context)
    await update.message.reply_text(answer)

# ============ ПРОВЕРКА НАПОМИНАНИЙ ============
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    active = get_active_reminders()
    for r in active:
        text = f"⏰ Напоминание!\n\n{r['text']}"
        await context.bot.send_message(chat_id=OWNER_ID, text=text)
        mark_reminder_sent(r["id"])

# ============ НАПОМИНАНИЯ ПО РАСПИСАНИЮ ============
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    if tasks:
        text = "🌅 Доброе утро! Твои задачи на сегодня:\n\n"
        for t in tasks:
            text += "  " + t["text"] + "\n"
        text += "\nУдачного дня!"
    else:
        text = "🌅 Доброе утро!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def afternoon_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    if tasks:
        text = "☀️ Полдень!\n\nВыполнено: " + str(len(completed)) + "\nОсталось: " + str(len(tasks))
    else:
        text = "☀️ Полдень! Все задачи выполнены!"
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    tasks = get_active_tasks()
    completed = get_completed_tasks_today()
    text = "🌙 Вечерний брифинг:\n\nСегодня выполнено: " + str(len(completed))
    if tasks:
        text += "\nПеренесено на завтра: " + str(len(tasks))
    await context.bot.send_message(chat_id=OWNER_ID, text=text)

# ============ WEBHOOK + ЗАПУСК ============
async def main():
    if not os.path.exists("/app/data"):
        os.makedirs("/app/data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_data({"tasks": [], "dialog_history": [], "user_preferences": {}, "reminders": []})

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("task", add_task_cmd))
    application.add_handler(CommandHandler("done", done_cmd))
    application.add_handler(CommandHandler("tasks", list_tasks_cmd))
    application.add_handler(CommandHandler("clear", clear_cmd))
    application.add_handler(CommandHandler("search", search_cmd))
    application.add_handler(CommandHandler("reminders", list_reminders_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = application.job_queue
    job_queue.run_daily(morning_reminder, time=time(6, 0))
    job_queue.run_daily(afternoon_reminder, time=time(12, 0))
    job_queue.run_daily(evening_reminder, time=time(18, 0))
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    webhook_url = RENDER_EXTERNAL_URL + "/telegram-webhook"
    print("Webhook URL: " + webhook_url)

    try:
        await application.initialize()
        await application.start()

        await application.bot.delete_webhook(drop_pending_updates=True)
        print("Старый webhook удален")

        await application.bot.set_webhook(webhook_url)
        print("Новый webhook установлен: " + webhook_url)

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
            return web.Response(text="OK - Kimi Bot with SearXNG & Reminders")

        app.router.add_post("/telegram-webhook", webhook_handler)
        app.router.add_get("/health", health_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        print("Бот запущен!")

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        print("FATAL ERROR:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
