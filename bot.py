"""Manga AI assistant: a Telegram-first personal and team productivity bot."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("manga")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
MORNING_TIME = os.getenv("MORNING_TIME", "08:00")
EVENING_TIME = os.getenv("EVENING_TIME", "20:00")
ALARM_PHONE = os.getenv("ALARM_PHONE", "")
COMPETITOR_FEEDS = [u.strip() for u in os.getenv("COMPETITOR_FEEDS", "").split(",") if u.strip()]
DB_PATH = os.getenv("DATABASE_PATH", "manga.sqlite3")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            description TEXT NOT NULL, due_at TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            reminded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            day TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(chat_id, day)
        );
        CREATE TABLE IF NOT EXISTS employee_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            employee TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feed_entries (
            url TEXT NOT NULL, entry_id TEXT NOT NULL, title TEXT NOT NULL,
            seen_at TEXT NOT NULL, PRIMARY KEY(url, entry_id)
        );
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            text TEXT NOT NULL, created_at TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0
        );
        """)


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def is_admin(update: Update) -> bool:
    return bool(update.effective_chat and ADMIN_CHAT_ID and update.effective_chat.id == ADMIN_CHAT_ID)


def parse_datetime(value: str) -> datetime:
    """Accept YYYY-MM-DD HH:MM or HH:MM (today, or tomorrow if already passed)."""
    current = now_local()
    try:
        if len(value) == 5:
            parsed = datetime.combine(current.date(), time.fromisoformat(value), tzinfo=TIMEZONE)
            if parsed <= current:
                parsed = parsed.replace(day=parsed.day) + __import__("datetime").timedelta(days=1)
            return parsed
        return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
    except ValueError as exc:
        raise ValueError("Vaqt formati HH:MM yoki YYYY-MM-DD HH:MM bo‘lsin.") from exc


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Salom! Men Manga AI yordamchisiman.\n\n"
            "/task HH:MM | vazifa — vazifa qo‘shish\n/tasks — ochiq vazifalar\n"
            "/done ID — bajarildi deb belgilash\n/checkin matn — kun yakuni\n"
            "/report ism | hisobot — xodim hisoboti\n/weekly — haftalik natija\n"
            "Admin: /draft mavzu, /approve ID, /send chat_id | xabar, /publish ID"
        )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_chat:
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await msg.reply_text("Format: /task 14:30 | Mijozga qo‘ng‘iroq qilish")
        return
    when, description = (part.strip() for part in raw.split("|", 1))
    if not description:
        await msg.reply_text("Vazifa matnini ham kiriting.")
        return
    try:
        due = parse_datetime(when)
    except ValueError as exc:
        await msg.reply_text(str(exc))
        return
    with db() as con:
        cur = con.execute("INSERT INTO tasks(chat_id,description,due_at,created_at) VALUES(?,?,?,?)",
                          (update.effective_chat.id, description, due.isoformat(), now_local().isoformat()))
        task_id = cur.lastrowid
    await msg.reply_text(f"✅ Vazifa #{task_id} saqlandi: {description}\nEslatma: {due:%d.%m %H:%M}")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_chat:
        return
    with db() as con:
        rows = con.execute("SELECT id,description,due_at FROM tasks WHERE chat_id=? AND done=0 ORDER BY due_at LIMIT 30",
                           (update.effective_chat.id,)).fetchall()
    if not rows:
        await update.effective_message.reply_text("Ochiq vazifalar yo‘q.")
        return
    lines = [f"#{r['id']} · {datetime.fromisoformat(r['due_at']):%d.%m %H:%M} · {r['description']}" for r in rows]
    await update.effective_message.reply_text("Ochiq vazifalar:\n" + "\n".join(lines))


async def complete_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_chat or not context.args or not context.args[0].isdigit():
        if msg:
            await msg.reply_text("Format: /done ID")
        return
    with db() as con:
        cur = con.execute("UPDATE tasks SET done=1 WHERE id=? AND chat_id=?", (int(context.args[0]), update.effective_chat.id))
    await msg.reply_text("✅ Bajarildi." if cur.rowcount else "Vazifa topilmadi.")


async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_chat:
        return
    text = " ".join(context.args).strip()
    if not text:
        await msg.reply_text("Kun yakuni: nimalarni bajardingiz, nima qolib ketdi va sababini yozing. /checkin bajarildi: ...")
        return
    with db() as con:
        con.execute("INSERT INTO checkins(chat_id,day,response,created_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(chat_id,day) DO UPDATE SET response=excluded.response,created_at=excluded.created_at",
                    (update.effective_chat.id, now_local().date().isoformat(), text, now_local().isoformat()))
    await msg.reply_text("Rahmat, kun yakuni saqlandi. Haftalik xulosaga qo‘shaman.")


async def employee_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_chat:
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await msg.reply_text("Format: /report Ism | Bugun bajarilgan ishlar va to‘siqlar")
        return
    employee, report = (p.strip() for p in raw.split("|", 1))
    if not employee or not report:
        await msg.reply_text("Ism va hisobot matnini kiriting.")
        return
    with db() as con:
        con.execute("INSERT INTO employee_reports(chat_id,employee,report,created_at) VALUES(?,?,?,?)",
                    (update.effective_chat.id, employee, report, now_local().isoformat()))
    await msg.reply_text("Hisobot qabul qilindi. Xodimlar tahliliga qo‘shildi.")


async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not update.effective_chat:
        return
    since = now_local().date().isoformat()
    with db() as con:
        tasks = con.execute("SELECT done,COUNT(*) n FROM tasks WHERE chat_id=? AND date(due_at)>=date('now','-7 days') GROUP BY done",
                            (update.effective_chat.id,)).fetchall()
        checks = con.execute("SELECT day,response FROM checkins WHERE chat_id=? AND day>=date('now','-7 days') ORDER BY day",
                             (update.effective_chat.id,)).fetchall()
        reports = con.execute("SELECT employee,COUNT(*) n FROM employee_reports WHERE chat_id=? AND date(created_at)>=date('now','-7 days') GROUP BY employee",
                              (update.effective_chat.id,)).fetchall()
    done = sum(r['n'] for r in tasks if r['done'])
    open_count = sum(r['n'] for r in tasks if not r['done'])
    check_lines = [f"• {r['day']}: {r['response'][:180]}" for r in checks]
    report_lines = [f"• {r['employee']}: {r['n']} ta hisobot" for r in reports]
    advice = "Keyingi hafta uchun: vazifalarni aniq natija va real muddat bilan belgilang; bajarilmaganlarini sababiga qarab qayta rejalashtiring."
    text = (f"📊 Oxirgi 7 kun\nVazifalar: {done} bajarildi, {open_count} ochiq.\n"
            f"Kun yakuni: {len(checks)} ta.\n" + ("\n".join(check_lines) or "Hali kun yakuni kiritilmagan.") +
            "\nXodimlar hisobotlari:\n" + ("\n".join(report_lines) or "Hisobotlar yo‘q.") + "\n\n" + advice)
    await msg.reply_text(text[:4000])


async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not is_admin(update):
        if msg:
            await msg.reply_text("Bu buyruq faqat admin uchun.")
        return
    topic = " ".join(context.args).strip()
    if not topic:
        await msg.reply_text("Format: /draft mavzu")
        return
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        await msg.reply_text("Post matni yaratish uchun OPENAI_API_KEY sozlang.")
        return
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        result = await asyncio.to_thread(
            client.responses.create,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=f"O‘zbek tilida Telegram kanali uchun qisqa, foydali va o‘qilishi oson post yoz. Mavzu: {topic}. Sarlavha va matn ber, uydirma faktlar kiritma. Oxirida bitta muhokama savoli bo‘lsin."
        )
        text = result.output_text.strip()
        with db() as con:
            cur = con.execute("INSERT INTO drafts(chat_id,text,created_at) VALUES(?,?,?)",
                              (update.effective_chat.id, text, now_local().isoformat()))
            draft_id = cur.lastrowid
        await msg.reply_text(f"📝 Qoralama #{draft_id} (ko‘rib chiqib /approve {draft_id} bilan tasdiqlang):\n\n{text}")
    except Exception:
        log.exception("Draft generation failed")
        await msg.reply_text("Qoralama yaratilmadi. API sozlamalari va loglarni tekshiring.")


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not is_admin(update) or not context.args or not context.args[0].isdigit():
        if msg:
            await msg.reply_text("Admin buyrug‘i: /approve ID")
        return
    with db() as con:
        row = con.execute("SELECT text FROM drafts WHERE id=? AND published=0 AND chat_id=?",
                          (int(context.args[0]), update.effective_chat.id)).fetchone()
    if not row:
        await msg.reply_text("Tasdiqlanmagan qoralama topilmadi.")
        return
    await msg.reply_text("Qoralama tasdiqlandi. Kanalga chiqarish uchun /publish ID yuboring.")


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not is_admin(update) or not context.args or not context.args[0].isdigit():
        if msg:
            await msg.reply_text("Admin buyrug‘i: /publish ID")
        return
    if not CHANNEL_ID:
        await msg.reply_text("TELEGRAM_CHANNEL_ID sozlanmagan.")
        return
    with db() as con:
        row = con.execute("SELECT text FROM drafts WHERE id=? AND published=0 AND chat_id=?",
                          (int(context.args[0]), update.effective_chat.id)).fetchone()
        if not row:
            await msg.reply_text("Qoralama topilmadi yoki allaqachon chiqarilgan.")
            return
        await context.bot.send_message(chat_id=CHANNEL_ID, text=row['text'])
        con.execute("UPDATE drafts SET published=1 WHERE id=?", (int(context.args[0]),))
    await msg.reply_text("✅ Post kanalga joylandi.")


async def send_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not is_admin(update):
        if msg:
            await msg.reply_text("Bu buyruq faqat admin uchun.")
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await msg.reply_text("Format: /send chat_id | xabar matni. Bot o‘z nomidan yuboradi.")
        return
    target, text = (p.strip() for p in raw.split("|", 1))
    try:
        await context.bot.send_message(chat_id=int(target), text=text)
        await msg.reply_text("Xabar bot nomidan yuborildi.")
    except (ValueError, Exception) as exc:
        log.warning("Send failed: %s", exc)
        await msg.reply_text("Xabar yuborilmadi. Chat ID va botga yozish ruxsatini tekshiring.")


async def due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as con:
        rows = con.execute("SELECT id,chat_id,description,due_at FROM tasks WHERE done=0 AND reminded=0 AND due_at<=?",
                           (now_local().isoformat(),)).fetchall()
    for row in rows:
        try:
            await context.bot.send_message(chat_id=row['chat_id'], text=f"⏰ Eslatma: #{row['id']} — {row['description']}\nBajarsangiz /done {row['id']} deb belgilang.")
            with db() as con:
                con.execute("UPDATE tasks SET reminded=1 WHERE id=?", (row['id'],))
        except Exception:
            log.exception("Could not send reminder for task %s", row['id'])


async def morning(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        return
    with db() as con:
        rows = con.execute("SELECT id,description,due_at FROM tasks WHERE chat_id=? AND done=0 AND date(due_at)=? ORDER BY due_at",
                           (ADMIN_CHAT_ID, now_local().date().isoformat())).fetchall()
    lines = [f"• #{r['id']} {datetime.fromisoformat(r['due_at']):%H:%M} — {r['description']}" for r in rows]
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="☀️ Xayrli tong! Bugungi vazifalar:\n" + ("\n".join(lines) or "Hozircha rejalashtirilgan vazifa yo‘q."))
    if ALARM_PHONE:
        await make_alarm_call("Xayrli tong! Bugungi vazifalaringizni Manga botidagi xabardan eshitishingiz mumkin.")


async def evening(context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_CHAT_ID:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="🌙 Kun yakuni so‘rovnomasi: nimalarni bajardingiz, nima qolib ketdi va nima sabab bo‘ldi? /checkin javob matni")


async def weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        return
    # Reuse the same query/report logic without requiring a synthetic Update.
    with db() as con:
        rows = con.execute("SELECT done,COUNT(*) n FROM tasks WHERE chat_id=? AND date(due_at)>=date('now','-7 days') GROUP BY done", (ADMIN_CHAT_ID,)).fetchall()
        checkins = con.execute("SELECT COUNT(*) n FROM checkins WHERE chat_id=? AND day>=date('now','-7 days')", (ADMIN_CHAT_ID,)).fetchone()['n']
        employees = con.execute("SELECT employee,COUNT(*) n FROM employee_reports WHERE date(created_at)>=date('now','-7 days') GROUP BY employee").fetchall()
    done = sum(r['n'] for r in rows if r['done'])
    open_count = sum(r['n'] for r in rows if not r['done'])
    team = "\n".join(f"• {r['employee']}: {r['n']} ta hisobot" for r in employees) or "Hisobotlar kelmadi."
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📊 Haftalik dashboard\nVazifalar: {done} bajarildi, {open_count} ochiq.\nKun yakunlari: {checkins} ta.\nXodimlar:\n{team}\n\nTavsiya: eng ko‘p qolib ketgan vazifalarni aniqlang, muddatlarni real belgilang va xodimlar bilan to‘siqlarni muhokama qiling.")


async def make_alarm_call(message: str) -> None:
    sid, token, phone, from_phone = (os.getenv(k, "") for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ALARM_PHONE", "TWILIO_FROM_NUMBER"))
    if not all((sid, token, phone, from_phone)):
        log.info("Voice call skipped: Twilio settings incomplete")
        return
    try:
        from twilio.rest import Client
        from xml.sax.saxutils import escape
        twiml = f'<Response><Say language="uz-UZ">{escape(message)}</Say></Response>'
        client = Client(sid, token)
        await asyncio.to_thread(client.calls.create, to=phone, from_=from_phone, twiml=twiml)
    except Exception:
        log.exception("Alarm call failed")


async def monitor_feeds(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID:
        return
    for url in COMPETITOR_FEEDS:
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in reversed(feed.entries[:10]):
                entry_id = entry.get("id") or entry.get("link") or entry.get("title", "")
                title = entry.get("title", "Yangi yangilik")
                with db() as con:
                    cur = con.execute("INSERT OR IGNORE INTO feed_entries(url,entry_id,title,seen_at) VALUES(?,?,?,?)",
                                      (url, entry_id, title, now_local().isoformat()))
                if cur.rowcount:
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔎 Kuzatilayotgan manbada yangi yangilik:\n{title}\n{entry.get('link','')}")
        except Exception:
            log.exception("Feed check failed for %s", url)


def schedule_daily(app: Application, callback, value: str, name: str) -> None:
    hour, minute = map(int, value.split(":"))
    app.job_queue.run_daily(callback, time=time(hour, minute, tzinfo=TIMEZONE), name=name)


def main() -> None:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN .env faylida sozlanishi kerak")
    init_db()
    app = Application.builder().token(TOKEN).build()
    for command, handler in (("start", start), ("task", add_task), ("tasks", list_tasks),
                             ("done", complete_task), ("checkin", checkin), ("report", employee_report),
                             ("weekly", weekly), ("draft", draft), ("approve", approve),
                             ("publish", publish), ("send", send_message)):
        app.add_handler(CommandHandler(command, handler))
    app.job_queue.run_repeating(due_reminders, interval=60, first=10, name="due-reminders")
    app.job_queue.run_repeating(monitor_feeds, interval=900, first=30, name="competitor-feeds")
    schedule_daily(app, morning, MORNING_TIME, "morning-reminder")
    schedule_daily(app, evening, EVENING_TIME, "evening-checkin")
    app.job_queue.run_daily(weekly_job, time=time(hour=18, minute=0, tzinfo=TIMEZONE), days=(6,), name="weekly-dashboard")
    log.info("Manga bot is running")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
