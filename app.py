import os
import secrets
import sqlite3
import threading
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.filters.command import CommandObject
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
)
import asyncio
from dotenv import load_dotenv

# ============ ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env ============
load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 2074669766))

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в файле .env")

# ============ FLASK ДЛЯ KEEP-ALIVE ============
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот работает!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Глобальное состояние
temp_data = {}
bot_state = {"logs_enabled": True}

# ============ БАЗА ДАННЫХ ============
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS messages
                      (id INTEGER PRIMARY KEY AUTOINCREMENT,
                       sender_id INTEGER,
                       receiver_id INTEGER,
                       message_text TEXT,
                       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                       sender_username TEXT,
                       sender_name TEXT)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS active_chats
                      (user1_id INTEGER,
                       user2_id INTEGER,
                       last_message_id INTEGER,
                       PRIMARY KEY (user1_id, user2_id))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS flags
                      (user_id INTEGER PRIMARY KEY,
                       reason TEXT,
                       timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS user_tokens
                      (token TEXT PRIMARY KEY,
                       user_id INTEGER UNIQUE,
                       created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    conn.commit()
    conn.close()

# ============ ТОКЕНЫ ДЛЯ ССЫЛОК ============
def get_or_create_token(user_id: int) -> str:
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT token FROM user_tokens WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]
    token = secrets.token_urlsafe(8)
    cursor.execute("INSERT INTO user_tokens (token, user_id) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    return token

def resolve_token(token: str) -> int | None:
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM user_tokens WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# ============ КЛАВИАТУРЫ ============
def get_user_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔗 Моя ссылка")],
            [KeyboardButton(text="📈 Моя статистика")],
        ],
        resize_keyboard=True
    )

def get_admin_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔐 Панель управления"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📨 Последние логи"),    KeyboardButton(text="💬 Диалоги")],
            [KeyboardButton(text="🚫 Заблокированные"),   KeyboardButton(text="🔗 Моя ссылка")],
        ],
        resize_keyboard=True
    )

def get_reply_keyboard(sender_id: int, receiver_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="💬 Ответить анонимно",
            callback_data=f"reply_{sender_id}_{receiver_id}"
        )
    ]])

def get_admin_keyboard():
    log_status = "🟢 Логи: ВКЛ" if bot_state["logs_enabled"] else "🔴 Логи: ВЫК"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="💬 Диалоги", callback_data="admin_chats"),
        ],
        [
            InlineKeyboardButton(text="📨 Последние логи", callback_data="admin_logs"),
            InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_flagged"),
        ],
        [
            InlineKeyboardButton(text=log_status, callback_data="admin_toggle_logs"),
            InlineKeyboardButton(text="🗑️ Очистить логи", callback_data="admin_clear_logs"),
        ],
    ])

def get_clear_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="admin_clear_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin_clear_cancel"),
    ]])

# ============ ЛОГИ АДМИНА ============
async def log_to_admin(text: str):
    if not bot_state["logs_enabled"]:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except:
        pass

# ============ КОМАНДА /start ============
@dp.message(CommandStart())
async def start_cmd(message: Message, command: CommandObject):
    args = command.args
    bot_info = await bot.get_me()

    if args == "admin_panel":
        if message.from_user.id == ADMIN_ID:
            await show_admin_panel(message)
        return

    is_admin = message.from_user.id == ADMIN_ID
    kb = get_admin_reply_keyboard() if is_admin else get_user_keyboard()

    if args and args != "admin_panel":
        receiver_id = resolve_token(args)

        if receiver_id is None:
            await message.answer(
                "❌ <b>Ссылка недействительна.</b>\nВозможно, она устарела или неверна.",
                parse_mode="HTML", reply_markup=kb
            )
            return

        if receiver_id == message.from_user.id:
            await message.answer(
                "❌ <b>Нельзя написать самому себе!</b>\n"
                "Поделитесь своей ссылкой с другими.",
                parse_mode="HTML", reply_markup=kb
            )
            return

        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM flags WHERE user_id = ?", (receiver_id,))
        flag = cursor.fetchone()
        conn.close()

        if flag:
            await message.answer(
                "⛔ <b>Пользователь недоступен.</b>",
                parse_mode="HTML", reply_markup=kb
            )
            return

        temp_data[str(message.from_user.id)] = receiver_id

        await log_to_admin(
            f"🔹 <b>НОВЫЙ ДИАЛОГ</b>\n"
            f"От: <code>{message.from_user.id}</code> (@{message.from_user.username or 'нет'})\n"
            f"Кому: <code>{receiver_id}</code>"
        )

        await message.answer(
            "✉️ <b>Анонимный чат</b>\n\n"
            "Напишите ваше сообщение — оно будет доставлено анонимно.\n"
            "После отправки вы сможете продолжить диалог.",
            parse_mode="HTML", reply_markup=kb
        )
    else:
        token = get_or_create_token(message.from_user.id)
        my_link = f"https://t.me/{bot_info.username}?start={token}"
        await message.answer(
            f"👋 <b>Добро пожаловать в анонимный чат!</b>\n\n"
            f"🔗 <b>Ваша личная ссылка:</b>\n"
            f"<code>{my_link}</code>\n\n"
            f"Поделитесь ею — и вам смогут написать анонимно.\n"
            f"Вы получите сообщение и сможете ответить, не раскрывая себя.",
            parse_mode="HTML", reply_markup=kb
        )

# ============ ОБРАБОТКА СООБЩЕНИЙ ============
@dp.message(F.text & ~F.text.startswith('/'))
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_key = str(user_id)
    text = message.text

    if text == "📈 Моя статистика":
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages WHERE receiver_id = ?", (user_id,))
        received = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages WHERE sender_id = ?", (user_id,))
        sent = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages WHERE receiver_id = ?", (user_id,))
        unique_senders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT receiver_id) FROM messages WHERE sender_id = ?", (user_id,))
        unique_receivers = cursor.fetchone()[0]
        conn.close()
        kb = get_admin_reply_keyboard() if user_id == ADMIN_ID else get_user_keyboard()
        await message.answer(
            "📈 <b>Ваша статистика</b>\n\n"
            f"📩 Получено анонимных сообщений: <b>{received}</b>\n"
            f"📤 Отправлено анонимных сообщений: <b>{sent}</b>\n"
            f"👥 Уникальных собеседников, кто вам писал: <b>{unique_senders}</b>\n"
            f"🔀 Уникальных, кому вы писали: <b>{unique_receivers}</b>",
            parse_mode="HTML", reply_markup=kb
        )
        return

    if text == "🔗 Моя ссылка":
        bot_info = await bot.get_me()
        token = get_or_create_token(user_id)
        my_link = f"https://t.me/{bot_info.username}?start={token}"
        kb = get_admin_reply_keyboard() if user_id == ADMIN_ID else get_user_keyboard()
        await message.answer(
            f"🔗 <b>Ваша личная ссылка:</b>\n<code>{my_link}</code>",
            parse_mode="HTML", reply_markup=kb
        )
        return

    if user_id == ADMIN_ID:
        if text == "🔐 Панель управления":
            await show_admin_panel(message)
            return
        if text == "📊 Статистика":
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM messages"); total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages"); senders = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT receiver_id) FROM messages"); receivers = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM flags"); blocked = cursor.fetchone()[0]
            conn.close()
            await message.answer(
                "📊 <b>Статистика</b>\n\n"
                f"📝 Всего сообщений: <b>{total}</b>\n"
                f"👤 Уникальных отправителей: <b>{senders}</b>\n"
                f"👥 Уникальных получателей: <b>{receivers}</b>\n"
                f"🚫 Заблокировано: <b>{blocked}</b>",
                parse_mode="HTML"
            )
            return
        if text == "📨 Последние логи":
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute("""
                SELECT sender_id, sender_username, receiver_id, message_text, timestamp
                FROM messages ORDER BY timestamp DESC LIMIT 20
            """)
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                await message.answer("📭 Сообщений нет.")
                return
            out = "🕵️ <b>Последние сообщения</b>\n\n"
            for r in rows:
                preview = r[3][:60] + ("…" if len(r[3]) > 60 else "")
                out += f"<code>{r[0]}</code> (@{r[1]}) → <code>{r[2]}</code>\n💬 {preview}\n🕐 {str(r[4])[:16]}\n\n"
            await message.answer(out[:4096], parse_mode="HTML")
            return
        if text == "💬 Диалоги":
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user1_id, user2_id, COUNT(*) FROM active_chats GROUP BY user1_id, user2_id")
            chats = cursor.fetchall()
            conn.close()
            if not chats:
                await message.answer("📭 Активных диалогов нет.")
                return
            out = "💬 <b>Активные диалоги</b>\n\n"
            for c in chats:
                out += f"<code>{c[0]}</code> ↔ <code>{c[1]}</code> — {c[2]} сообщ.\n"
            await message.answer(out, parse_mode="HTML")
            return
        if text == "🚫 Заблокированные":
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, reason, timestamp FROM flags")
            flags = cursor.fetchall()
            conn.close()
            if not flags:
                await message.answer("📭 Заблокированных нет.")
                return
            out = "🚫 <b>Заблокированные пользователи</b>\n\n"
            for f in flags:
                out += f"ID: <code>{f[0]}</code>\nПричина: {f[1]}\nДата: {str(f[2])[:16]}\n\n"
            await message.answer(out, parse_mode="HTML")
            return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM flags WHERE user_id = ?", (user_id,))
    flag = cursor.fetchone()

    if flag:
        conn.close()
        await message.answer("⛔ <b>Ваш аккаунт заблокирован.</b>", parse_mode="HTML")
        return

    if user_key in temp_data:
        receiver_id = temp_data[user_key]

        cursor.execute("SELECT * FROM flags WHERE user_id = ?", (receiver_id,))
        receiver_flag = cursor.fetchone()
        if receiver_flag:
            conn.close()
            await message.answer("⛔ <b>Получатель недоступен.</b>", parse_mode="HTML")
            del temp_data[user_key]
            return

        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, message_text, sender_username, sender_name) VALUES (?, ?, ?, ?, ?)",
            (user_id, receiver_id, message.text,
             message.from_user.username or "Не указан",
             message.from_user.full_name)
        )
        msg_id = cursor.lastrowid

        cursor.execute(
            "INSERT OR REPLACE INTO active_chats (user1_id, user2_id, last_message_id) VALUES (?, ?, ?)",
            (min(user_id, receiver_id), max(user_id, receiver_id), msg_id)
        )
        conn.commit()
        conn.close()

        try:
            keyboard = get_reply_keyboard(user_id, receiver_id)
            await bot.send_message(
                receiver_id,
                f"📬 <b>Новое анонимное сообщение</b>\n\n{message.text}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await log_to_admin(
                f"📨 <b>СООБЩЕНИЕ #{msg_id}</b>\n"
                f"От: <code>{user_id}</code>\n"
                f"Кому: <code>{receiver_id}</code>\n"
                f"Текст: {message.text[:100]}"
            )
            await message.answer("✅ <b>Сообщение отправлено!</b>", parse_mode="HTML")
        except Exception as e:
            await message.answer("❌ <b>Не удалось доставить сообщение.</b>", parse_mode="HTML")
            await log_to_admin(f"⚠️ Ошибка: {str(e)}")

        del temp_data[user_key]

    else:
        reply_key = f"reply_{user_id}"
        if reply_key in temp_data:
            await handle_reply_message(message)
        else:
            await message.answer(
                "ℹ️ Чтобы начать диалог — используйте ссылку другого пользователя.",
                parse_mode="HTML"
            )

# ============ ОБРАБОТКА ОТВЕТОВ ============
@dp.callback_query(F.data.startswith("reply_"))
async def handle_reply(callback: CallbackQuery):
    data = callback.data.split("_")
    original_sender_id = int(data[1])
    receiver_id = int(data[2])
    current_user_id = callback.from_user.id

    if current_user_id != receiver_id:
        await callback.answer("❌ Это не ваше сообщение!", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass

    await callback.message.answer(
        "✏️ <b>Напишите анонимный ответ:</b>",
        parse_mode="HTML"
    )
    temp_data[f"reply_{current_user_id}"] = original_sender_id
    await callback.answer()

async def handle_reply_message(message: Message):
    user_id = message.from_user.id
    reply_key = f"reply_{user_id}"

    if reply_key in temp_data:
        receiver_id = temp_data[reply_key]

        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, message_text, sender_username, sender_name) VALUES (?, ?, ?, ?, ?)",
            (user_id, receiver_id, message.text,
             message.from_user.username or "Не указан",
             message.from_user.full_name)
        )
        msg_id = cursor.lastrowid

        cursor.execute(
            "INSERT OR REPLACE INTO active_chats (user1_id, user2_id, last_message_id) VALUES (?, ?, ?)",
            (min(user_id, receiver_id), max(user_id, receiver_id), msg_id)
        )
        conn.commit()
        conn.close()

        try:
            keyboard = get_reply_keyboard(user_id, receiver_id)
            await bot.send_message(
                receiver_id,
                f"💬 <b>Анонимный ответ</b>\n\n{message.text}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await log_to_admin(
                f"🔄 <b>ОТВЕТ #{msg_id}</b>\n"
                f"От: <code>{user_id}</code>\n"
                f"Кому: <code>{receiver_id}</code>\n"
                f"Текст: {message.text[:100]}"
            )
            await message.answer("✅ <b>Ответ отправлен!</b>", parse_mode="HTML")
        except Exception:
            await message.answer("❌ <b>Не удалось отправить ответ.</b>", parse_mode="HTML")

        del temp_data[reply_key]

# ============ АДМИН-ПАНЕЛЬ ============
async def show_admin_panel(message: Message):
    await message.answer(
        "🔐 <b>Панель администратора</b>\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await show_admin_panel(message)

@dp.callback_query(F.data.startswith("admin_"))
async def handle_admin_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа!", show_alert=True)
        return

    action = callback.data

    if action == "admin_stats":
        await callback.answer()
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages")
        senders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT receiver_id) FROM messages")
        receivers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM flags")
        blocked = cursor.fetchone()[0]
        conn.close()
        text = (
            "📊 <b>Статистика</b>\n\n"
            f"📝 Всего сообщений: <b>{total}</b>\n"
            f"👤 Уникальных отправителей: <b>{senders}</b>\n"
            f"👥 Уникальных получателей: <b>{receivers}</b>\n"
            f"🚫 Заблокировано: <b>{blocked}</b>"
        )
        await callback.message.answer(text, parse_mode="HTML")

    elif action == "admin_chats":
        await callback.answer()
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user1_id, user2_id, COUNT(*) FROM active_chats GROUP BY user1_id, user2_id")
        chats = cursor.fetchall()
        conn.close()
        if not chats:
            await callback.message.answer("📭 Активных диалогов нет.")
            return
        text = "💬 <b>Активные диалоги</b>\n\n"
        for chat in chats:
            text += f"<code>{chat[0]}</code> ↔ <code>{chat[1]}</code> — {chat[2]} сообщ.\n"
        await callback.message.answer(text, parse_mode="HTML")

    elif action == "admin_logs":
        await callback.answer()
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sender_id, sender_username, sender_name, receiver_id, message_text, timestamp
            FROM messages ORDER BY timestamp DESC LIMIT 20
        """)
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            await callback.message.answer("📭 Сообщений нет.")
            return
        text = "🕵️ <b>Последние сообщения</b>\n\n"
        for row in rows:
            preview = row[4][:60] + ("…" if len(row[4]) > 60 else "")
            text += (
                f"<code>{row[0]}</code> (@{row[1]}) → <code>{row[3]}</code>\n"
                f"💬 {preview}\n"
                f"🕐 {row[5]}\n\n"
            )
        await callback.message.answer(text[:4000], parse_mode="HTML")

    elif action == "admin_flagged":
        await callback.answer()
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, reason, timestamp FROM flags")
        flags = cursor.fetchall()
        conn.close()
        if not flags:
            await callback.message.answer("📭 Заблокированных нет.")
            return
        text = "🚫 <b>Заблокированные пользователи</b>\n\n"
        for flag in flags:
            text += f"ID: <code>{flag[0]}</code>\nПричина: {flag[1]}\nДата: {flag[2]}\n\n"
        await callback.message.answer(text, parse_mode="HTML")

    elif action == "admin_toggle_logs":
        bot_state["logs_enabled"] = not bot_state["logs_enabled"]
        if bot_state["logs_enabled"]:
            status_text = "🟢 Логи включены — вы будете получать уведомления о каждом сообщении."
        else:
            status_text = "🔴 Логи выключены — уведомления отключены. Смотрите историю через кнопки."
        await callback.answer(status_text, show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=get_admin_keyboard())

    elif action == "admin_clear_logs":
        await callback.answer()
        await callback.message.answer(
            "⚠️ <b>Вы уверены?</b>\n\n"
            "Это удалит <b>все сообщения и диалоги</b> из базы данных.\n"
            "Действие необратимо.",
            parse_mode="HTML",
            reply_markup=get_clear_confirm_keyboard()
        )

    elif action == "admin_clear_confirm":
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM messages")
        cursor.execute("DELETE FROM active_chats")
        conn.commit()
        conn.close()
        await callback.answer("✅ Логи очищены", show_alert=True)
        await callback.message.edit_text(
            f"🗑️ <b>Логи очищены.</b>\nУдалено сообщений: <b>{count}</b>",
            parse_mode="HTML"
        )

    elif action == "admin_clear_cancel":
        await callback.answer("Отменено")
        await callback.message.edit_text("❌ Очистка отменена.")

# ============ КОМАНДЫ АДМИНА ============
@dp.message(Command("stats"))
async def get_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages")
    senders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT receiver_id) FROM messages")
    receivers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM flags")
    blocked = cursor.fetchone()[0]
    conn.close()
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"📝 Всего сообщений: <b>{total}</b>\n"
        f"👤 Отправителей: <b>{senders}</b>\n"
        f"👥 Получателей: <b>{receivers}</b>\n"
        f"🚫 Заблокировано: <b>{blocked}</b>",
        parse_mode="HTML"
    )

@dp.message(Command("getlogs"))
async def get_logs(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sender_id, sender_username, sender_name, receiver_id, message_text, timestamp
        FROM messages ORDER BY timestamp DESC LIMIT 20
    """)
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.answer("📭 Сообщений нет.")
        return
    text = "🕵️ <b>Последние сообщения</b>\n\n"
    for row in rows:
        preview = row[4][:60] + ("…" if len(row[4]) > 60 else "")
        text += (
            f"<code>{row[0]}</code> (@{row[1]}) → <code>{row[3]}</code>\n"
            f"💬 {preview}\n"
            f"🕐 {row[5]}\n\n"
        )
    await message.answer(text[:4000], parse_mode="HTML")

@dp.message(Command("chats"))
async def get_chats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user1_id, user2_id, COUNT(*) FROM active_chats GROUP BY user1_id, user2_id")
    chats = cursor.fetchall()
    conn.close()
    if not chats:
        await message.answer("📭 Активных диалогов нет.")
        return
    text = "💬 <b>Активные диалоги</b>\n\n"
    for chat in chats:
        text += f"<code>{chat[0]}</code> ↔ <code>{chat[1]}</code> — {chat[2]} сообщ.\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("find"))
async def find_user_messages(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /find <code>ID</code>\n\nПример: /find 123456789",
            parse_mode="HTML"
        )
        return
    try:
        user_id = int(parts[1])
    except:
        await message.answer("❌ Неверный ID — только цифры.")
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE sender_id = ? OR receiver_id = ?",
        (user_id, user_id)
    )
    total = cursor.fetchone()[0]
    cursor.execute("""
        SELECT sender_id, receiver_id, message_text, timestamp, sender_username, sender_name
        FROM messages
        WHERE sender_id = ? OR receiver_id = ?
        ORDER BY timestamp DESC
        LIMIT 10
    """, (user_id, user_id))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.answer(f"📭 Сообщений для <code>{user_id}</code> не найдено.", parse_mode="HTML")
        return
    text = (
        f"🔍 <b>История пользователя</b> <code>{user_id}</code>\n"
        f"📊 Всего сообщений: <b>{total}</b> | Показаны последние 10\n"
        f"{'─' * 28}\n\n"
    )
    for i, row in enumerate(rows, 1):
        sender_id, receiver_id, msg_text, timestamp, sender_username, sender_name = row
        if sender_id == user_id:
            direction = "📤 <b>Написал</b>"
            other_label = f"кому → <code>{receiver_id}</code>"
        else:
            direction = "📩 <b>Получил</b>"
            other_label = f"от ← <code>{sender_id}</code> (@{sender_username or '?'})"
        preview = msg_text[:80] + ("…" if len(msg_text) > 80 else "")
        ts = str(timestamp)[:16]
        text += (
            f"{i}. {direction} {other_label}\n"
            f"💬 {preview}\n"
            f"🕐 {ts}\n\n"
        )
    if total > 10:
        text += f"📌 Для полного экспорта: /export {user_id}"
    await message.answer(text[:4096], parse_mode="HTML")

@dp.message(Command("export"))
async def export_user_messages(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Использование: /export <code>ID</code>", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
    except:
        await message.answer("❌ Неверный ID.")
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sender_id, receiver_id, message_text, timestamp, sender_username, sender_name
        FROM messages WHERE sender_id = ? OR receiver_id = ?
        ORDER BY timestamp DESC
    """, (user_id, user_id))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.answer(f"📭 Сообщений для {user_id} не найдено.")
        return
    filename = f"user_{user_id}_messages.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"ВСЕ СООБЩЕНИЯ ПОЛЬЗОВАТЕЛЯ {user_id}\n")
        f.write("=" * 50 + "\n\n")
        for row in rows:
            direction = "📤 ОТПРАВИЛ" if row[0] == user_id else "📩 ПОЛУЧИЛ"
            other = row[1] if row[0] == user_id else row[0]
            f.write(f"{direction} | Собеседник: {other}\n")
            f.write(f"Текст: {row[2]}\n")
            f.write(f"Время: {row[3]}\n")
            f.write("-" * 40 + "\n")
    with open(filename, "rb") as f:
        await message.answer_document(
            types.FSInputFile(filename),
            caption=f"📄 Сообщения пользователя {user_id} ({len(rows)} шт.)"
        )
    os.remove(filename)

@dp.message(Command("flag"))
async def flag_user(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("❌ Использование: /flag <code>ID причина</code>", parse_mode="HTML")
        return
    try:
        user_id = int(parts[1])
        reason = parts[2]
    except:
        await message.answer("❌ Неверный ID.")
        return
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO flags (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Пользователь <code>{user_id}</code> заблокирован.\nПричина: {reason}", parse_mode="HTML")

@dp.message(Command("unflag"))
async def unflag_user(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.