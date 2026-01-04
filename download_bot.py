import os
import time
import asyncio
import httpx
import logging
import urllib.parse
from datetime import datetime
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)

# --- تنظیمات فایل‌ها ---
TOKEN = "YOUR_BOT_TOKEN" # توکن خود را اینجا بگذارید
ADMIN_FILE = "admin_id.txt"
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 48 * 1024 * 1024
VIDEO_EXT = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm')

# ایجاد پوشه دانلود
if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- توابع مدیریت ادمین و آمار ---

def get_admin():
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r") as f: return int(f.read().strip())
    return None

def set_admin(user_id):
    with open(ADMIN_FILE, "w") as f: f.write(str(user_id))

def save_history(filename, url, size):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"📅 {now} | 📦 {filename} ({size}) | 🔗 {url}\n")

# --- رابط کاربری (کیبوردها) ---

def main_menu_keyboard(is_admin=False):
    keyboard = []
    if is_admin:
        keyboard.append([InlineKeyboardButton("🛠 پنل مدیریت ادمین", callback_data="admin_main")])
    return InlineKeyboardMarkup(keyboard)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تاریخچه دانلود", callback_data="adm_hist"),
         InlineKeyboardButton("📜 وضعیت سیستم", callback_data="adm_logs")],
        [InlineKeyboardButton("🧹 پاکسازی", callback_data="adm_clear"),
         InlineKeyboardButton("🏠 بازگشت", callback_data="adm_back")]
    ])

def download_keyboard(status="dl"):
    if status == "dl":
        return InlineKeyboardMarkup([[InlineKeyboardButton("⏸ توقف", callback_data="cb_pause"), 
                                      InlineKeyboardButton("❌ لغو", callback_data="cb_stop")]])
    return InlineKeyboardMarkup([[InlineKeyboardButton("▶️ ادامه", callback_data="cb_resume"), 
                                  InlineKeyboardButton("❌ لغو", callback_data="cb_stop")]])

# --- هسته دانلود (با اصلاح خطاها) ---

async def download_engine(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url, headers={"Range": f"bytes={downloaded}-"}) as resp:
                if resp.status_code not in (200, 206): return f"Error: {resp.status_code}"
                
                total = int(resp.headers.get("Content-Length", 0)) + downloaded
                mode = "ab" if downloaded > 0 else "wb"
                
                with open(file_path, mode) as f:
                    start_t = time.time()
                    last_upd = 0
                    async for chunk in resp.aiter_bytes(chunk_size=16384):
                        if chat_data.get('st') == 'p': return "p"
                        if chat_data.get('st') == 's': return "s"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if time.time() - last_upd > 2.5:
                            perc = (downloaded/total*100) if total>0 else 0
                            speed = (downloaded - (os.path.getsize(file_path) if mode=="ab" else 0)) / (time.time()-start_t + 0.1)
                            bar = "🔹" * int(perc/10) + "🔸" * (10-int(perc/10))
                            text = f"🚀 **در حال دریافت...**\n\n`{filename}`\n{bar} `{perc:.1f}%`\n⚡ `{speed/1024/1024:.1f} MB/s`"
                            try: await context.bot.edit_message_text(text, chat_id, chat_data['m_id'], 
                                                                    reply_markup=download_keyboard("dl"), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
                return "ok"
        except Exception as e: return str(e)

# --- مدیریت کلیک روی تمام دکمه‌ها ---

async def global_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    admin_id = get_admin()

    # دکمه‌های دانلود
    if data == "cb_pause":
        context.chat_data['st'] = 'p'
        await query.answer("⏸ متوقف شد")
    elif data == "cb_stop":
        context.chat_data['st'] = 's'
        await query.answer("❌ لغو شد")
    elif data == "cb_resume":
        context.chat_data['st'] = 'dl'
        await query.answer("▶️ ادامه دانلود...")
        asyncio.create_task(run_process(chat_id, context))

    # دکمه‌های ادمین
    if chat_id == admin_id:
        if data == "admin_main" or data == "adm_back":
            await query.edit_message_text("🛠 به پنل مدیریت خوش آمدید:", reply_markup=admin_keyboard())
        elif data == "adm_logs":
            logs = "✅ سیستم پایدار است"
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f: logs = "".join(f.readlines()[-5:])
            await query.edit_message_text(f"📜 **آخرین گزارشات:**\n\n`{logs}`", reply_markup=admin_keyboard(), parse_mode='Markdown')
        elif data == "adm_hist":
            hist = "هنوز دانلودی انجام نشده."
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f: hist = "".join(f.readlines()[-5:])
            await query.edit_message_text(f"📊 **تاریخچه اخیر:**\n\n{hist}", reply_markup=admin_keyboard())
        elif data == "adm_clear":
            if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
            await query.answer("✨ تاریخچه پاکسازی شد")

# --- پردازش نهایی و ارسال ---

async def run_process(chat_id, context):
    chat_data = context.chat_data
    res = await download_engine(chat_id, context, chat_data['url'], chat_data['fname'])
    
    file_path = os.path.join(DOWNLOAD_DIR, chat_data['fname'])
    if res == "ok":
        await context.bot.edit_message_text("✅ دانلود شد! در حال ارسال ویدیو... 📤", chat_id, chat_data['m_id'])
        size_str = f"{os.path.getsize(file_path)/1024/1024:.1f} MB"
        save_history(chat_data['fname'], chat_data['url'], size_str)
        
        with open(file_path, 'rb') as f:
            if chat_data['fname'].lower().endswith(VIDEO_EXT):
                await context.bot.send_video(chat_id, video=f, caption=f"🎬 `{chat_data['fname']}`", supports_streaming=True)
            else:
                await context.bot.send_document(chat_id, document=f, caption=f"📄 `{chat_data['fname']}`")
        
        os.remove(file_path)
        await context.bot.delete_message(chat_id, chat_data['m_id'])
    elif res == "s":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ عملیات لغو شد.", chat_id, chat_data['m_id'])

# --- هندلرهای پیام ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_admin() is None:
        set_admin(user_id)
        await update.message.reply_text("👑 شما به عنوان ادمین شناسایی شدید!")
    
    is_admin = (user_id == get_admin())
    await update.message.reply_text("👋 لینک فایل را بفرستید:", reply_markup=main_menu_keyboard(is_admin))

async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"): return
    
    fname = urllib.parse.unquote(url.split('/')[-1]) or "file"
    context.chat_data.update({'url': url, 'fname': fname, 'st': 'dl'})
    
    m = await update.message.reply_text("🔍 در حال بررسی لینک...")
    context.chat_data['m_id'] = m.message_id
    await run_process(update.effective_chat.id, context)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(global_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    print("🤖 ربات روشن است...")
    app.run_polling()

if __name__ == '__main__':
    main()
