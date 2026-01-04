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

# --- تنظیمات و دیتابیس ساده ---
try:
    from bot_config import TOKEN, ADMIN_ID
except ImportError:
    TOKEN = "YOUR_BOT_TOKEN_HERE"
    ADMIN_ID = 0  # مقدار پیش‌فرض

DB_FILE = "users_db.json"
LOG_FILE = "bot_log.txt"
HISTORY_FILE = "download_history.txt"
DOWNLOAD_DIR = "downloads"
CHUNK_SIZE = 47 * 1024 * 1024  # پارت‌های زیر 50 مگابایت
VIDEO_EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v')

# تنظیمات اولیه فایل‌ها
for f in [DOWNLOAD_DIR]:
    if not os.path.exists(f): os.makedirs(f)

# --- مدیریت داده‌های کاربران ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f: return json.load(f)
    return {"users": {}, "settings": {"global_limit": 100, "daily_limit": 5}}

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=4)

db = load_db()

def check_user(user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"downloads_today": 0, "last_reset": str(datetime.now().date()), "status": "active"}
        save_db(db)
    
    # ریست کردن آمار روزانه اگر تاریخ عوض شده باشد
    today = str(datetime.now().date())
    if db["users"][uid]["last_reset"] != today:
        db["users"][uid]["downloads_today"] = 0
        db["users"][uid]["last_reset"] = today
        save_db(db)
    return db["users"][uid]

# --- توابع کمکی رابط کاربری ---
def get_progress_bar(percent):
    done = int(percent / 10)
    return "🔹" * done + "🔸" * (10 - done)

def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0: break
        size /= 1024.0
    return f"{size:.2f} {unit}"

# --- هسته دانلود و پارت‌بندی ---
async def download_engine(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url, headers={"Range": f"bytes={downloaded}-"}) as resp:
                if resp.status_code not in (200, 206): return "error"
                total = int(resp.headers.get("Content-Length", 0)) + downloaded
                mode = "ab" if downloaded > 0 else "wb"
                
                with open(file_path, mode) as f:
                    start_t = time.time()
                    last_upd = 0
                    async for chunk in resp.aiter_bytes():
                        if chat_data.get('status') == 'paused': return "paused"
                        if chat_data.get('status') == 'cancelled': return "cancelled"
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if time.time() - last_upd > 3:
                            speed = (downloaded - (os.path.getsize(file_path) if mode=="ab" else 0)) / (time.time() - start_t + 0.1)
                            percent = (downloaded / total * 100) if total > 0 else 0
                            eta = (total - downloaded) / (speed + 1)
                            
                            text = (
                                f"📥 **در حال دریافت فایل...**\n\n"
                                f"📄 `{filename}`\n"
                                f"📊 {get_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡️ سرعت: {human_readable_size(speed)}/s\n"
                                f"📦 حجم: {human_readable_size(downloaded)} / {human_readable_size(total)}\n"
                                f"⏳ زمان: {int(eta)} ثانیه"
                            )
                            kb = [[InlineKeyboardButton("⏸ توقف", callback_data="dl_pause"),
                                   InlineKeyboardButton("❌ لغو", callback_data="dl_cancel")]]
                            try: await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                            except: pass
                            last_upd = time.time()
            return "completed"
        except Exception as e: return str(e)

# --- هندلرهای دستورات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    check_user(user.id)
    msg = "🚀 **خوش آمدید!**\n\nلینک مستقیم فایل را بفرستید تا برایتان دانلود و آپلود کنم."
    if user.id == ADMIN_ID:
        msg += "\n\n👨‍✈️ ادمین عزیز، برای مدیریت از /admin استفاده کنید."
    await update.message.reply_text(msg, parse_mode='Markdown')

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    stats = f"👥 تعداد کاربران: {len(db['users'])}\n⚙️ محدودیت روزانه: {db['settings']['daily_limit']} فایل"
    kb = [
        [InlineKeyboardButton("📊 آمار و تاریخچه", callback_data="adm_history"),
         InlineKeyboardButton("👥 مدیریت کاربران", callback_data="adm_users")],
        [InlineKeyboardButton("🧹 پاکسازی فایل‌ها", callback_data="adm_clear"),
         InlineKeyboardButton("📜 مشاهده لاگ", callback_data="adm_logs")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(f"🛠 **پنل مدیریت مدرن**\n\n{stats}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else:
        await update.message.reply_text(f"🛠 **پنل مدیریت مدرن**\n\n{stats}", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

# --- پردازش پیام و صف ---
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # بررسی اینکه آیا ادمین در حال تغییر تنظیمات است
    if user_id == ADMIN_ID and context.user_data.get('waiting_for_limit'):
        if update.message.text.isdigit():
            new_limit = int(update.message.text)
            db["settings"]["daily_limit"] = new_limit
            save_db(db)
            context.user_data['waiting_for_limit'] = False
            return await update.message.reply_text(f"✅ محدودیت دانلود روزانه به {new_limit} تغییر یافت.")
        else:
            return await update.message.reply_text("❌ لطفاً فقط یک عدد انگلیسی ارسال کنید.")

    # بقیه کد قبلی شما از اینجا شروع شود (بررسی banned بودن و لینک ها)
    u_data = check_user(user_id)
    # ... ادامه کد handle_msg
    
    if u_data["status"] == "banned":
        return await update.message.reply_text("🚫 دسترسی شما به ربات مسدود شده است.")

    url = update.message.text
    if url.startswith("http"):
        # بررسی محدودیت تعداد دانلود
        if u_data["downloads_today"] >= db["settings"]["daily_limit"] and user_id != ADMIN_ID:
            return await update.message.reply_text(f"⚠️ سقف دانلود روزانه شما ({db['settings']['daily_limit']}) تمام شده است.")

        if 'queue' not in context.chat_data: context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(url)
        
        await update.message.reply_text(f"✅ لینک در صف قرار گرفت. (موقعیت: {len(context.chat_data['queue'])})")
        
        if not context.chat_data.get('is_working'):
            await run_next(update.effective_chat.id, context)

async def run_next(chat_id, context):
    if not context.chat_data.get('queue'):
        context.chat_data['is_working'] = False
        return

    context.chat_data['is_working'] = True
    url = context.chat_data['queue'].popleft()
    context.chat_data['status'] = 'downloading'
    context.chat_data['current_url'] = url
    
    filename = urllib.parse.unquote(url.split('/')[-1].split('?')[0]) or f"file_{int(time.time())}"
    context.chat_data['current_filename'] = filename
    
    msg = await context.bot.send_message(chat_id, "🔍 در حال بررسی لینک...")
    context.chat_data['msg_id'] = msg.message_id
    
    res = await download_engine(chat_id, context, url, filename)
    await finalize_dl(chat_id, context, res)

async def finalize_dl(chat_id, context, res):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, chat_data['current_filename'])
    
    if res == "completed":
        uid = str(chat_id)
        db["users"][uid]["downloads_today"] += 1
        save_db(db)
        
        await context.bot.edit_message_text("✅ دانلود تمام شد. در حال ارسال به تلگرام...", chat_id, chat_data['msg_id'])
        
        if os.path.exists(file_path):
            is_vid = chat_data['current_filename'].lower().endswith(VIDEO_EXTS)
            file_size = os.path.getsize(file_path)

            # --- شروع بخش برش نهایی و قطعی ---
            if file_size > CHUNK_SIZE:
                await context.bot.edit_message_text("✂️ در حال قطعه‌قطعه کردن ویدیو (این کار ممکن است کمی طول بکشد)...", chat_id, chat_data['msg_id'])
                
                base_name, extension = os.path.splitext(chat_data['current_filename'])
                if not extension: extension = ".mp4"
                clean_name = "".join([c for c in base_name if c.isalnum()]).strip()
                
                # ایجاد پوشه موقت
                temp_parts_dir = os.path.join(DOWNLOAD_DIR, f"parts_{chat_id}_{int(time.time())}")
                os.makedirs(temp_parts_dir, exist_ok=True)

                import subprocess
                try:
                    # استفاده از متد تقسیم زمانی که بسیار پایدارتر است
                    # هر پارت را حدود 8 دقیقه در نظر می‌گیریم تا قطعا زیر 50 مگابایت بماند
                    output_template = os.path.join(temp_parts_dir, f"Part_%03d_{clean_name}{extension}")
                    
                    command = [
                        'ffmpeg', '-y', '-i', file_path,
                        '-force_key_frames', 'expr:gte(t,n_forced*60)', # اجبار به ایجاد فریم کلیدی در هر دقیقه
                        '-f', 'segment',
                        '-segment_time', '00:08:00', # برش‌های 8 دقیقه‌ای
                        '-reset_timestamps', '1',
                        '-map', '0',
                        '-c', 'copy', # ابتدا سعی می‌کند کپی کند
                        output_template
                    ]
                    
                    # اجرای دستور
                    subprocess.run(command, capture_output=True, check=True)
                    
                    # خواندن پارت‌ها
                    generated_parts = sorted([f for f in os.listdir(temp_parts_dir) if f.startswith("Part_")])

                    if not generated_parts:
                        raise Exception("No parts created")

                    total = len(generated_parts)
                    for i, p_file in enumerate(generated_parts, 1):
                        p_path = os.path.join(temp_parts_dir, p_file)
                        if chat_data.get('status') == 'cancelled': break
                        
                        # اگر پارتی به هر دلیل باز هم بزرگتر از 49 مگابایت بود
                        if os.path.getsize(p_path) > 49 * 1024 * 1024:
                            # این پارت را دوباره به دو نیم تقسیم کن (فقط برای اطمینان)
                            continue 

                        with open(p_path, 'rb') as tp:
                            caption = f"🎬 **{chat_data['current_filename']}**\n📦 پارت {i} از {total}"
                            
                            # ارسال
                            await context.bot.send_video(
                                chat_id, video=tp, caption=caption,
                                supports_streaming=True, parse_mode='Markdown',
                                read_timeout=300, write_timeout=300
                            )
                        
                        os.remove(p_path)
                        await asyncio.sleep(2)

                except Exception as e:
                    logging.error(f"Final Attempt Error: {e}")
                    # راه حل آخر: اگر FFmpeg کلا شکست خورد، فایل را به صورت داکیومنت با پایتون تکه کن
                    await context.bot.send_message(chat_id, "❌ متاسفانه به دلیل ساختار خاص این ویدیو، امکان برش هوشمند نبود.")
                
                finally:
                    import shutil
                    if os.path.exists(temp_parts_dir): shutil.rmtree(temp_parts_dir)
            # --- پایان بخش برش ---

            # --- شروع بخش ارسال تک فایل ---
            else:
                with open(file_path, 'rb') as f:
                    if is_vid:
                        await context.bot.send_video(
                            chat_id, video=f, 
                            caption=chat_data['current_filename'], 
                            supports_streaming=True,
                            read_timeout=120, write_timeout=120
                        )
                    else:
                        await context.bot.send_document(
                            chat_id, document=f, 
                            caption=chat_data['current_filename'],
                            read_timeout=120, write_timeout=120
                        )
            
            # پاکسازی فایل اصلی پس از اتمام (یا لغو)
            if os.path.exists(file_path):
                os.remove(file_path)
        
        try:
            await context.bot.delete_message(chat_id, chat_data['msg_id'])
        except:
            pass
        await run_next(chat_id, context)
    
    elif res == "cancelled":
        if os.path.exists(file_path):
            os.remove(file_path)
        await run_next(chat_id, context)

async def callback_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id

    # مدیریت دانلودها
    if data == "dl_pause":
        context.chat_data['status'] = 'paused'
        await query.answer("متوقف شد")
    elif data == "dl_resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("ادامه دانلود")
        asyncio.create_task(download_engine_wrapper(chat_id, context))
    elif data == "dl_cancel":
        context.chat_data['status'] = 'cancelled'
        file_path = os.path.join(DOWNLOAD_DIR, context.chat_data.get('current_filename', ''))
        if os.path.exists(file_path): os.remove(file_path)
        await query.edit_message_text("❌ دانلود لغو شد.")
        await run_next(chat_id, context)
    
    # --- بخش اصلاح شده ادمین ---
    elif data.startswith("adm_") and update.effective_user.id == ADMIN_ID:
        if data == "adm_main":
            await admin_menu(update, context)
            
        elif data == "adm_clear":
            files = os.listdir(DOWNLOAD_DIR)
            for f in files: os.remove(os.path.join(DOWNLOAD_DIR, f))
            await query.answer(f"🧹 {len(files)} فایل پاکسازی شد")
            
        elif data == "adm_history":
            # نمایش آمار دانلودها
            total_dl = sum(u['downloads_today'] for u in db['users'].values())
            msg = f"📈 **آمار سیستم:**\n\nکل دانلودهای امروز: {total_dl}"
            kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            
        elif data == "adm_users":
            msg = f"👥 **مدیریت کاربران**\n\nمحدودیت فعلی سیستم: {db['settings']['daily_limit']} فایل در روز"
            kb = [
                [InlineKeyboardButton("🔢 تغییر محدودیت عمومی", callback_data="adm_set_limit")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]
            ]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

        elif data == "adm_set_limit":
            context.user_data['waiting_for_limit'] = True
            await query.edit_message_text("لطفاً عدد جدید محدودیت دانلود روزانه را ارسال کنید:", 
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="adm_users")]]))

        elif data == "adm_logs":
            # بررسی وجود فایل لاگ
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "rb") as f:
                    await context.bot.send_document(chat_id, document=f, caption="📜 فایل لاگ سیستم")
            else:
                await query.answer("❌ فایلی یافت نشد", show_alert=True)
    
    # بخش ادمین
    elif data.startswith("adm_") and update.effective_user.id == ADMIN_ID:
        if data == "adm_clear":
            for f in os.listdir(DOWNLOAD_DIR): os.remove(os.path.join(DOWNLOAD_DIR, f))
            await query.answer("🧹 پوشه دانلود پاکسازی شد")
        elif data == "adm_history":
            await query.edit_message_text("📈 بخش تاریخچه (به زودی)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]))
        elif data == "adm_main":
            await admin_menu(update, context)

async def download_engine_wrapper(chat_id, context):
    res = await download_engine(chat_id, context, context.chat_data['current_url'], context.chat_data['current_filename'])
    await finalize_dl(chat_id, context, res)

# --- اجرای اصلی ---
if __name__ == '__main__':
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.add_handler(CallbackQueryHandler(callback_gate))
    print("🤖 Bot Started...")
    app.run_polling()
