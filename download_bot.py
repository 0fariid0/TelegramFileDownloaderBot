import os
import time
import asyncio
import httpx
import logging
import json
import urllib.parse
from datetime import datetime
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)

# --- تنظیمات سیستم لاگ‌دهی ---
LOG_FILE = "bot_log.txt"
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- تنظیمات و دیتابیس ساده ---
TOKEN = "YOUR_BOT_TOKEN_HERE" # توکن خود را اینجا قرار دهید
ADMIN_ID = 450281442 
DB_FILE = "users_db.json"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 45 * 1024 * 1024  # پارت‌های 45 مگابایتی
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

if not os.path.exists(DOWNLOAD_DIR): 
    os.makedirs(DOWNLOAD_DIR)

# --- مدیریت داده‌های کاربران ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {"users": {}, "settings": {"daily_limit": 5}}

def save_db(db_data):
    with open(DB_FILE, "w") as f: json.dump(db_data, f, indent=4)

db = load_db()

def check_user(user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"downloads_today": 0, "last_reset": str(datetime.now().date()), "status": "active"}
        save_db(db)
    
    today = str(datetime.now().date())
    if db["users"][uid]["last_reset"] != today:
        db["users"][uid]["downloads_today"] = 0
        db["users"][uid]["last_reset"] = today
        save_db(db)
    return db["users"][uid]

# --- توابع کمکی ---
def get_progress_bar(percent):
    done = int(percent / 10)
    return "🔹" * done + "🔸" * (10 - done)

def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: break
        size /= 1024.0
    return f"{size:.2f} {unit}"

# --- هسته دانلود ---
async def download_engine(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = 0
    
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200: return "error"
                total = int(resp.headers.get("Content-Length", 0))
                
                with open(file_path, "wb") as f:
                    start_t = time.time()
                    last_upd = 0
                    async for chunk in resp.aiter_bytes():
                        if chat_data.get('status') == 'cancelled': return "cancelled"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if time.time() - last_upd > 4:
                            percent = (downloaded / total * 100) if total > 0 else 0
                            speed = downloaded / (time.time() - start_t + 0.1)
                            text = (
                                f"📥 **در حال دریافت...**\n\n"
                                f"📄 `{filename}`\n"
                                f"📊 {get_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡️ سرعت: {human_readable_size(speed)}/s\n"
                                f"📦 حجم: {human_readable_size(downloaded)} / {human_readable_size(total)}"
                            )
                            kb = [[InlineKeyboardButton("❌ لغو دانلود", callback_data="dl_cancel")]]
                            try: await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
            return "completed"
        except Exception as e:
            logger.error(f"Download Error: {e}")
            return str(e)

# --- هندلرها ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    check_user(user.id)
    msg = "🚀 **خوش آمدید!**\n\nلینک مستقیم فایل را بفرستید تا برایتان دانلود و آپلود کنم."
    await update.message.reply_text(msg, parse_mode='Markdown')

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    stats = f"👥 تعداد کاربران: {len(db['users'])}\n⚙️ محدودیت روزانه: {db['settings']['daily_limit']} فایل"
    kb = [
        [InlineKeyboardButton("📊 لیست کاربران", callback_data="adm_users"),
         InlineKeyboardButton("🧹 پاکسازی فایل‌ها", callback_data="adm_clear")],
        [InlineKeyboardButton("📜 دریافت فایل لاگ", callback_data="adm_logs")]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(f"🛠 **پنل مدیریت**\n\n{stats}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.message.reply_text(f"🛠 **پنل مدیریت**\n\n{stats}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u_data = check_user(user_id)
    
    if u_data["status"] == "banned":
        return await update.message.reply_text("🚫 شما مسدود هستید.")

    url = update.message.text
    if url.startswith("http"):
        if u_data["downloads_today"] >= db["settings"]["daily_limit"] and user_id != ADMIN_ID:
            return await update.message.reply_text("⚠️ سقف دانلود روزانه شما تمام شده است.")

        if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(url)
        await update.message.reply_text(f"✅ در صف قرار گرفت. (موقعیت: {len(context.chat_data['queue'])})")
        
        if not context.chat_data.get('is_working'):
            await run_next(update.effective_chat.id, context)

async def run_next(chat_id, context):
    if not context.chat_data.get('queue'):
        context.chat_data['is_working'] = False
        return

    context.chat_data['is_working'] = True
    url = context.chat_data['queue'].popleft()
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or f"file_{int(time.time())}"
    context.chat_data['current_filename'] = filename
    
    msg = await context.bot.send_message(chat_id, "🔍 در حال آماده‌سازی...")
    context.chat_data['msg_id'] = msg.message_id
    
    res = await download_engine(chat_id, context, url, filename)
    await finalize_dl(chat_id, context, res)

async def finalize_dl(chat_id, context, res):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, chat_data['current_filename'])
    
    if res == "completed":
        db["users"][str(chat_id)]["downloads_today"] += 1
        save_db(db)
        
        await context.bot.edit_message_text("✅ دانلود شد. در حال ارسال...", chat_id, chat_data['msg_id'])
        
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            # پارت‌بندی خودکار
            if size > CHUNK_SIZE:
                part = 1
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk: break
                        temp_name = f"part_{part}_{chat_data['current_filename']}"
                        with open(temp_name, "wb") as tp: tp.write(chunk)
                        with open(temp_name, "rb") as tp:
                            await context.bot.send_document(chat_id, document=tp, caption=f"📦 Part {part}")
                        os.remove(temp_name)
                        part += 1
            else:
                is_vid = chat_data['current_filename'].lower().endswith(VIDEO_EXTS)
                with open(file_path, 'rb') as f:
                    if is_vid: await context.bot.send_video(chat_id, video=f, supports_streaming=True)
                    else: await context.bot.send_document(chat_id, document=f)
            
            os.remove(file_path)
        await context.bot.delete_message(chat_id, chat_data['msg_id'])
    
    elif res == "cancelled":
        await context.bot.edit_message_text("❌ عملیات لغو شد.", chat_id, chat_data['msg_id'])
    
    await run_next(chat_id, context)

async def callback_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "dl_cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("در حال لغو...")
    
    # --- بخش مدیریت ---
    elif data.startswith("adm_") and update.effective_user.id == ADMIN_ID:
        if data == "adm_clear":
            files = os.listdir(DOWNLOAD_DIR)
            for f in files: os.remove(os.path.join(DOWNLOAD_DIR, f))
            await query.answer(f"🧹 {len(files)} فایل پاک شد")
        
        elif data == "adm_users":
            text = "👥 **وضعیت کاربران:**\n\n"
            for uid, info in db["users"].items():
                text += f"🆔 `{uid}`: {info['downloads_today']} دانلود امروز\n"
            kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            
        elif data == "adm_logs":
            if os.path.exists(LOG_FILE):
                await query.message.reply_document(document=open(LOG_FILE, 'rb'), caption="📜 Log File")
                await query.answer("ارسال شد")
            else:
                await query.answer("لاگی وجود ندارد")

        elif data == "adm_main":
            await admin_menu(update, context)

if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.add_handler(CallbackQueryHandler(callback_gate))
    print("🤖 Bot is running...")
    app.run_polling()
