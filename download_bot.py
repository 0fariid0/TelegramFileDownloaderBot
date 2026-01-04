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

# --- تنظیمات ---
try:
    from bot_config import TOKEN
except ImportError:
    TOKEN = "YOUR_BOT_TOKEN_HERE"

ADMIN_ID = 450281442 # آیدی عددی خود را وارد کنید
DB_FILE = "users_db.json"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 48 * 1024 * 1024 # پارت‌های 48 مگابایتی
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

# --- دیتابیس ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {"users": {}, "settings": {"daily_limit": 10}}

def save_db(db_data):
    with open(DB_FILE, "w") as f: json.dump(db_data, f, indent=4)

db = load_db()

def get_user(user_id):
    uid = str(user_id)
    today = str(datetime.now().date())
    if uid not in db["users"]:
        db["users"][uid] = {"dl_count": 0, "last_date": today, "status": "active"}
    if db["users"][uid]["last_date"] != today:
        db["users"][uid]["dl_count"] = 0
        db["users"][uid]["last_date"] = today
    save_db(db)
    return db["users"][uid]

# --- توابع کمکی ---
def get_progress_bar(percent):
    done = int(percent / 10)
    return "🔹" * done + "🔸" * (10 - done)

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: break
        size /= 1024.0
    return f"{size:.2f} {unit}"

# --- هسته دانلود ---
async def download_engine(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    
    headers = {"Range": f"bytes={downloaded}-"}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416: return "completed"
                if resp.status_code not in (200, 206): return f"Error: {resp.status_code}"
                
                total = int(resp.headers.get("Content-Length", 0)) + downloaded
                mode = "ab" if downloaded > 0 else "wb"
                
                with open(file_path, mode) as f:
                    last_upd = 0
                    start_t = time.time()
                    async for chunk in resp.aiter_bytes(chunk_size=32768):
                        if chat_data.get('status') == 'paused': return "paused"
                        if chat_data.get('status') == 'cancelled': return "cancelled"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if time.time() - last_upd > 4: # بهینه‌سازی برای جلوگیری از بلاک شدن توسط تلگرام
                            percent = (downloaded / total * 100) if total > 0 else 0
                            speed = (downloaded - (os.path.getsize(file_path) if mode=="ab" else 0)) / (time.time() - start_t + 0.1)
                            text = (
                                f"📥 **در حال دریافت ویدیو...**\n\n`{filename}`\n"
                                f"📊 {get_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡️ سرعت: {human_size(speed)}/s | حجم: {human_size(downloaded)}"
                            )
                            kb = [[InlineKeyboardButton("⏸ توقف", callback_data="btn_pause"),
                                   InlineKeyboardButton("❌ لغو", callback_data="btn_cancel")]]
                            try: await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
            return "completed"
        except Exception as e: return str(e)

# --- مدیریت ارسال ویدیو ---
async def send_as_video_parts(chat_id, context, file_path, filename):
    is_video = filename.lower().endswith(VIDEO_EXTS)
    size = os.path.getsize(file_path)
    
    if size > CHUNK_SIZE:
        part = 1
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk: break
                part_name = f"part_{part}_{filename}"
                with open(part_name, "wb") as p: p.write(chunk)
                
                with open(part_name, "rb") as p:
                    caption = f"🎬 {filename}\n📦 پارت {part}"
                    if is_video:
                        await context.bot.send_video(chat_id, video=p, caption=caption, supports_streaming=True)
                    else:
                        await context.bot.send_document(chat_id, document=p, caption=caption)
                
                os.remove(part_name)
                part += 1
    else:
        with open(file_path, "rb") as f:
            if is_video:
                await context.bot.send_video(chat_id, video=f, caption=f"✅ {filename}", supports_streaming=True)
            else:
                await context.bot.send_document(chat_id, document=f, caption=f"✅ {filename}")

# --- هندلرها ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user.id)
    await update.message.reply_text("👋 لینک ویدیو را بفرستید تا به صورت کلیپ (حتی چند پارت) براتون بفرستم.")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u_data = get_user(user_id)
    
    if u_data["dl_count"] >= db["settings"]["daily_limit"] and user_id != ADMIN_ID:
        return await update.message.reply_text("⚠️ سقف دانلود روزانه شما تمام شده.")

    url = update.message.text
    if not url.startswith("http"): return

    if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
    context.chat_data['queue'].append(url)
    await update.message.reply_text(f"✅ به صف اضافه شد. (تعداد در صف: {len(context.chat_data['queue'])})")
    
    if not context.chat_data.get('working'):
        await run_next(update.effective_chat.id, context)

async def run_next(chat_id, context):
    if not context.chat_data.get('queue'):
        context.chat_data['working'] = False
        return

    context.chat_data['working'] = True
    url = context.chat_data['queue'].popleft()
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or f"video_{int(time.time())}.mp4"
    
    context.chat_data.update({'status': 'downloading', 'current_url': url, 'current_file': filename})
    msg = await context.bot.send_message(chat_id, "⏳ شروع دانلود...")
    context.chat_data['msg_id'] = msg.message_id
    
    result = await download_engine(chat_id, context, url, filename)
    
    if result == "completed":
        db["users"][str(chat_id)]["dl_count"] += 1
        save_db(db)
        await context.bot.edit_message_text("📤 در حال آپلود ویدیو (ممکن است طول بکشد)...", chat_id, context.chat_data['msg_id'])
        
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        try:
            await send_as_video_parts(chat_id, context, file_path, filename)
            if os.path.exists(file_path): os.remove(file_path)
            await context.bot.delete_message(chat_id, context.chat_data['msg_id'])
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ خطا در ارسال: {e}")
        
        await run_next(chat_id, context)
    
    elif result == "paused":
        kb = [[InlineKeyboardButton("▶️ ادامه", callback_data="btn_resume"), InlineKeyboardButton("❌ لغو", callback_data="btn_cancel")]]
        await context.bot.edit_message_text("⏸ دانلود متوقف شد.", chat_id, context.chat_data['msg_id'], reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id

    if data == "btn_pause":
        context.chat_data['status'] = 'paused'
        await query.answer("⏸ متوقف شد")
    elif data == "btn_resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("▶️ ادامه دانلود")
        asyncio.create_task(run_resume(chat_id, context))
    elif data == "btn_cancel":
        context.chat_data['status'] = 'cancelled'
        await query.answer("❌ لغو شد")
        file_path = os.path.join(DOWNLOAD_DIR, context.chat_data.get('current_file', ''))
        if os.path.exists(file_path): os.remove(file_path)
        await run_next(chat_id, context)

async def run_resume(chat_id, context):
    res = await download_engine(chat_id, context, context.chat_data['current_url'], context.chat_data['current_file'])
    if res != "paused": await run_next(chat_id, context)

# --- اجرا ---
if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(callback_handler))
    print("🚀 ربات با قابلیت ارسال ویدیو استارت شد...")
    app.run_polling()
