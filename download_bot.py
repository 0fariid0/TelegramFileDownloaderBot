import os
import time
import asyncio
import httpx
import json
import urllib.parse
import io
from datetime import datetime
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)

# --- تنظیمات ---
TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_ID = 450281442
DB_FILE = "users_db.json"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 47 * 1024 * 1024 # کمی کمتر از 50 مگابایت برای اطمینان
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)

# --- مدیریت دیتابیس (با استفاده از قفل برای جلوگیری از تداخل) ---
db_lock = asyncio.Lock()

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {"users": {}, "settings": {"daily_limit": 10}}

async def save_db_async(db_data):
    async with db_lock:
        with open(DB_FILE, "w") as f: json.dump(db_data, f, indent=4)

db = load_db()

def get_user(user_id):
    uid = str(user_id)
    today = str(datetime.now().date())
    if uid not in db["users"]:
        db["users"][uid] = {"dl_count": 0, "last_date": today}
    if db["users"][uid]["last_date"] != today:
        db["users"][uid]["dl_count"] = 0
        db["users"][uid]["last_date"] = today
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

# --- هسته دانلود اصلاح شده ---
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
                    async for chunk in resp.aiter_bytes(chunk_size=128*1024): # چانک بزرگتر برای سرعت بیشتر
                        if chat_data.get('status') == 'paused': return "paused"
                        if chat_data.get('status') == 'cancelled': return "cancelled"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # آپدیت پروگرس هر 5 ثانیه (برای جلوگیری از Flood)
                        if time.time() - last_upd > 5:
                            percent = (downloaded / total * 100) if total > 0 else 0
                            speed = (downloaded - (os.path.getsize(file_path) if mode=="ab" else 0)) / (time.time() - start_t + 0.1)
                            text = (
                                f"📥 **در حال دریافت ویدیو...**\n\n`{filename}`\n"
                                f"📊 {get_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡️ سرعت: {human_size(speed)}/s | حجم: {human_size(downloaded)}"
                            )
                            kb = [[InlineKeyboardButton("⏸ توقف", callback_data="btn_pause"),
                                   InlineKeyboardButton("❌ لغو", callback_data="btn_cancel")]]
                            try:
                                await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], 
                                                                 reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
            return "completed"
        except Exception as e: return str(e)

# --- ارسال هوشمند پارت‌ها بدون اشغال فضای اضافی ---
async def send_smart_parts(chat_id, context, file_path, filename):
    is_video = filename.lower().endswith(VIDEO_EXTS)
    total_size = os.path.getsize(file_path)
    
    if total_size <= CHUNK_SIZE:
        with open(file_path, "rb") as f:
            if is_video:
                await context.bot.send_video(chat_id, video=f, caption=f"✅ {filename}", supports_streaming=True)
            else:
                await context.bot.send_document(chat_id, document=f, caption=f"✅ {filename}")
    else:
        part = 1
        offset = 0
        while offset < total_size:
            with open(file_path, 'rb') as f:
                f.seek(offset)
                chunk = f.read(CHUNK_SIZE)
                if not chunk: break
                
                # ارسال مستقیم از حافظه (بدون ساخت فایل فیزیکی جدید)
                bio = io.BytesIO(chunk)
                bio.name = f"Part_{part}_{filename}"
                
                caption = f"🎬 پارت {part} از ویدیو:\n`{filename}`"
                if is_video:
                    await context.bot.send_video(chat_id, video=bio, caption=caption)
                else:
                    await context.bot.send_document(chat_id, document=bio, caption=caption)
                
                offset += CHUNK_SIZE
                part += 1

# --- هندلرهای اصلی ---
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u_data = get_user(user_id)
    
    if u_data["dl_count"] >= db["settings"]["daily_limit"] and user_id != ADMIN_ID:
        return await update.message.reply_text("⚠️ سقف دانلود روزانه شما تمام شده.")

    url = update.message.text
    if not url.startswith("http"): return

    if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
    context.chat_data['queue'].append(url)
    
    if not context.chat_data.get('working'):
        await run_next(update.effective_chat.id, context)
    else:
        await update.message.reply_text(f"✅ به صف اضافه شد. (موقعیت: {len(context.chat_data['queue'])})")

async def run_next(chat_id, context):
    if not context.chat_data.get('queue'):
        context.chat_data['working'] = False
        return

    context.chat_data['working'] = True
    url = context.chat_data['queue'].popleft()
    
    # استخراج نام فایل تمیزتر
    clean_url = url.split('?')[0]
    filename = urllib.parse.unquote(clean_url.split('/')[-1]) or f"file_{int(time.time())}.mp4"
    
    context.chat_data.update({'status': 'downloading', 'current_url': url, 'current_file': filename})
    msg = await context.bot.send_message(chat_id, "⏳ در حال شروع...")
    context.chat_data['msg_id'] = msg.message_id
    
    result = await download_engine(chat_id, context, url, filename)
    
    if result == "completed":
        db["users"][str(chat_id)]["dl_count"] += 1
        await save_db_async(db)
        
        await context.bot.edit_message_text("📤 در حال ارسال به تلگرام...", chat_id, context.chat_data['msg_id'])
        
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        try:
            await send_smart_parts(chat_id, context, file_path, filename)
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ خطا در ارسال: {e}")
        finally:
            if os.path.exists(file_path): os.remove(file_path)
            try: await context.bot.delete_message(chat_id, context.chat_data['msg_id'])
            except: pass
        
        await run_next(chat_id, context)
    # مدیریت بقیه وضعیت‌ها (Pause/Cancel) مشابه قبل...
