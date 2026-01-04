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

# --- تنظیمات ---
TOKEN = "YOUR_BOT_TOKEN" # توکن خود را اینجا قرار دهید
ADMIN_FILE = "admin_id.txt"
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm')

if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- مدیریت ادمین و آمار ---
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

# --- کیبوردهای شیشه‌ای ---
def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 آخرین دانلودها", callback_data="adm_hist"),
         InlineKeyboardButton("📜 لاگ سیستم", callback_data="adm_logs")],
        [InlineKeyboardButton("🧹 پاکسازی تاریخچه", callback_data="adm_clear")],
        [InlineKeyboardButton("🏠 بستن پنل", callback_data="adm_close")]
    ])

def download_keyboard(status="dl"):
    if status == "dl":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⏸ توقف", callback_data="pause"), 
            InlineKeyboardButton("❌ لغو", callback_data="stop")
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ ادامه", callback_data="resume"), 
        InlineKeyboardButton("❌ لغو", callback_data="stop")
    ]])

# --- موتور دانلود قدرتمند ---
async def download_file(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            headers = {"Range": f"bytes={downloaded}-"}
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code not in (200, 206): return f"خطای سرور: {resp.status_code}"
                
                total = int(resp.headers.get("Content-Length", 0)) + downloaded
                mode = "ab" if downloaded > 0 else "wb"
                
                with open(file_path, mode) as f:
                    start_t = time.time()
                    last_upd = 0
                    async for chunk in resp.aiter_bytes(chunk_size=32768):
                        if chat_data.get('state') == 'paused': return "paused"
                        if chat_data.get('state') == 'stopped': return "stopped"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # آپدیت ظاهر هر 3 ثانیه
                        if time.time() - last_upd > 3:
                            perc = (downloaded/total*100) if total > 0 else 0
                            speed = (downloaded - (os.path.getsize(file_path) if mode=="ab" else 0)) / (time.time()-start_t + 0.1)
                            bar = "🔹" * int(perc/10) + "🔸" * (10-int(perc/10))
                            text = (f"🚀 **در حال دانلود...**\n\n`{filename}`\n\n"
                                    f"{bar} `{perc:.1f}%`\n"
                                    f"⚡ سرعت: `{speed/1024/1024:.1f} MB/s`\n"
                                    f"📦 حجم: `{downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB`")
                            try:
                                await context.bot.edit_message_text(text, chat_id, chat_data['m_id'], 
                                                                    reply_markup=download_keyboard("dl"), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
                return "success"
    except Exception as e: return str(e)

# --- مدیریت هوشمند عملیات ---
async def start_process(chat_id, context):
    chat_data = context.chat_data
    chat_data['state'] = 'running'
    
    result = await download_file(chat_id, context, chat_data['url'], chat_data['fname'])
    
    file_path = os.path.join(DOWNLOAD_DIR, chat_data['fname'])
    if result == "success":
        await context.bot.edit_message_text("✅ دانلود تمام شد. در حال ارسال... 📤", chat_id, chat_data['m_id'])
        size_str = f"{os.path.getsize(file_path)/1024/1024:.1f} MB"
        save_history(chat_data['fname'], chat_data['url'], size_str)
        
        with open(file_path, 'rb') as f:
            if chat_data['fname'].lower().endswith(VIDEO_EXTS):
                await context.bot.send_video(chat_id, video=f, caption=f"🎬 `{chat_data['fname']}`", supports_streaming=True, parse_mode='Markdown')
            else:
                await context.bot.send_document(chat_id, document=f, caption=f"📄 `{chat_data['fname']}`", parse_mode='Markdown')
        
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.delete_message(chat_id, chat_data['m_id'])
    
    elif result == "paused":
        await context.bot.edit_message_text(f"⏸ دانلود متوقف شد.\n`{chat_data['fname']}`", chat_id, chat_data['m_id'], 
                                            reply_markup=download_keyboard("paused"), parse_mode='Markdown')
    elif result == "stopped":
        if os.path.exists(file_path): os.remove(file_path)
        await context.bot.edit_message_text("❌ دانلود لغو و فایل حذف شد.", chat_id, chat_data['m_id'])

# --- هندلر مرکزی دکمه‌ها ---
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id
    
    # دکمه‌های دانلود
    if data == "pause":
        context.chat_data['state'] = 'paused'
        await query.answer("توقف موقت")
    elif data == "stop":
        context.chat_data['state'] = 'stopped'
        await query.answer("لغو دانلود")
    elif data == "resume":
        context.chat_data['state'] = 'running'
        await query.answer("ادامه دانلود...")
        asyncio.create_task(start_process(chat_id, context))
        
    # دکمه‌های ادمین
    if chat_id == get_admin():
        if data == "adm_logs":
            log_data = "بدون لاگ"
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r") as f: log_data = "".join(f.readlines()[-8:])
            await query.edit_message_text(f"📜 **وضعیت سیستم:**\n\n`{log_data}`", reply_markup=admin_keyboard(), parse_mode='Markdown')
        elif data == "adm_hist":
            hist = "تاریخچه خالی است."
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f: hist = "".join(f.readlines()[-6:])
            await query.edit_message_text(f"📊 **آخرین فعالیت‌ها:**\n\n{hist}", reply_markup=admin_keyboard())
        elif data == "adm_clear":
            if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
            await query.answer("پاکسازی شد ✨")
        elif data == "adm_close":
            await query.edit_message_text("پنل مدیریت بسته شد.")

# --- هندلرهای پیام ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if get_admin() is None:
        set_admin(uid)
        await update.message.reply_text("👑 مدیریت ربات به شما واگذار شد!")
    
    msg = "👋 خوش آمدید!\n\n🔗 لینک فایل را بفرستید تا دانلود کنم."
    kb = admin_keyboard() if uid == get_admin() else None
    await update.message.reply_text(msg, reply_markup=kb)

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"): return
    
    fname = urllib.parse.unquote(url.split('/')[-1]) or f"file_{int(time.time())}"
    context.chat_data.update({'url': url, 'fname': fname, 'state': 'running'})
    
    m = await update.message.reply_text("🔍 در حال بررسی لینک...")
    context.chat_data['m_id'] = m.message_id
    # اجرای دانلود در یک تسک جداگانه برای جلوگیری از قفل شدن دکمه‌ها
    asyncio.create_task(start_process(update.effective_chat.id, context))

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    print("🚀 ربات با موفقیت در حال اجرا است...")
    app.run_polling()

if __name__ == '__main__':
    main()
