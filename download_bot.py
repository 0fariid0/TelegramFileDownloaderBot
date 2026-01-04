import os
import time
import asyncio
import httpx
import logging
import json
import urllib.parse
from datetime import datetime
from collections import deque
from functools import wraps
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
PAGE_SIZE = 8

# تنظیمات اولیه فایل‌ها
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- مدیریت داده‌های کاربران ---
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "settings": {"global_limit": 100, "daily_limit": 5}}


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=4)


db = load_db()


def check_user(user_id):
    uid = str(user_id)
    users = db.setdefault("users", {})
    if uid not in users:
        users[uid] = {"downloads_today": 0, "last_reset": str(datetime.now().date()), "status": "active", "personal_limit": None}
        save_db(db)

    today = str(datetime.now().date())
    if users[uid]["last_reset"] != today:
        users[uid]["downloads_today"] = 0
        users[uid]["last_reset"] = today
        save_db(db)
    return users[uid]


# --- توابع کمکی رابط کاربری ---

def get_progress_bar(percent):
    done = int(percent / 10)
    return "🔹" * done + "🔸" * (10 - done)


def human_readable_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.2f} {unit}"


# --- ابزارهای async برای عملیات blocking ---
async def run_in_background(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def run_ffmpeg_sync(command):
    import subprocess
    return subprocess.run(command, capture_output=True, check=True)


async def run_ffmpeg_async(command):
    return await run_in_background(run_ffmpeg_sync, command)


async def safe_remove(path):
    def _rm():
        try:
            if os.path.exists(path):
                os.remove(path)
                return True
        except Exception:
            return False
    return await run_in_background(_rm)


# --- دکوراتور admin-only ---

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id != ADMIN_ID:
            try:
                if update.callback_query:
                    await update.callback_query.answer("🔒 فقط برای ادمین", show_alert=True)
                elif update.message:
                    await update.message.reply_text("🔒 فقط برای ادمین")
            except Exception:
                pass
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


# --- رجیستری callback های ادمین ---
ADMIN_CALLBACKS = {}


def register_admin_callback(key):
    def deco(fn):
        ADMIN_CALLBACKS[key] = admin_only(fn)
        return fn

    return deco


# --- هسته دانلود و پارت‌بندی ---
async def download_engine(chat_id, context, url, filename):
    chat_data = context.chat_data
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    downloaded = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url, headers={"Range": f"bytes={downloaded}-"}) as resp:
                if resp.status_code not in (200, 206):
                    logging.error(f"Bad status code: {resp.status_code} for {url}")
                    return "error"

                total_header = resp.headers.get("Content-Length")
                total = int(total_header) + downloaded if total_header and total_header.isdigit() else 0
                mode = "ab" if downloaded > 0 else "wb"

                # track initial downloaded to compute speed properly
                start_t = time.time()
                start_downloaded = downloaded
                last_upd = 0

                with open(file_path, mode) as f:
                    async for chunk in resp.aiter_bytes():
                        if chat_data.get('status') == 'paused':
                            return "paused"
                        if chat_data.get('status') == 'cancelled':
                            return "cancelled"

                        f.write(chunk)
                        downloaded += len(chunk)

                        # گزارش وضعیت هر 3 ثانیه
                        if time.time() - last_upd > 3:
                            elapsed = time.time() - start_t + 0.1
                            speed = (downloaded - start_downloaded) / elapsed
                            percent = (downloaded / total * 100) if total > 0 else 0
                            eta = int((total - downloaded) / (speed + 1)) if total > 0 else -1

                            if total > 0:
                                size_txt = f"{human_readable_size(downloaded)} / {human_readable_size(total)}"
                                eta_txt = f"{eta} ثانیه"
                            else:
                                size_txt = human_readable_size(downloaded)
                                eta_txt = "نامشخص"

                            text = (
                                f"📥 **در حال دریافت فایل...**\n\n"
                                f"📄 `{filename}`\n"
                                f"📊 {get_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡️ سرعت: {human_readable_size(speed)}/s\n"
                                f"📦 حجم: {size_txt}\n"
                                f"⏳ زمان: {eta_txt}"
                            )
                            kb = [[InlineKeyboardButton("⏸ توقف", callback_data="dl_pause"),
                                   InlineKeyboardButton("❌ لغو", callback_data="dl_cancel")]]
                            try:
                                await context.bot.edit_message_text(text, chat_id, chat_data['msg_id'], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
                            except Exception:
                                pass
                            last_upd = time.time()
            return "completed"
        except Exception as e:
            logging.exception("Download engine error")
            return str(e)


# --- helpers for admin UI ---

def get_admin_markup():
    kb = [
        [InlineKeyboardButton("📊 آمار و تاریخچه", callback_data="adm_history"), InlineKeyboardButton("👥 مدیریت کاربران", callback_data="adm_users:0")],
        [InlineKeyboardButton("📂 فایل‌های دانلود شده", callback_data="adm_files"), InlineKeyboardButton("📥 فایل‌های در حال دانلود", callback_data="adm_active")],
        [InlineKeyboardButton("⚙️ تنظیمات سیستم", callback_data="adm_settings"), InlineKeyboardButton("🧹 پاکسازی فایل‌ها", callback_data="adm_clear_confirm")],
        [InlineKeyboardButton("📜 مشاهده لاگ (فایل)", callback_data="adm_logs"), InlineKeyboardButton("🔄 بازنشانی آمار کاربران", callback_data="adm_reset_stats")],
        [InlineKeyboardButton("🔙 خروج", callback_data="adm_exit")]
    ]
    return InlineKeyboardMarkup(kb)


# --- هندلرهای دستورات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    check_user(user.id)
    msg = "🚀 **خوش آمدید!**\n\nلینک مستقیم فایل را بفرستید تا برایتان دانلود و آپلود کنم."
    if user.id == ADMIN_ID:
        msg += "\n\n👨‍✈️ ادمین عزیز، برای مدیریت از /admin استفاده کنید."
    await update.message.reply_text(msg, parse_mode='Markdown')


@admin_only
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = f"👥 تعداد کاربران: {len(db['users'])}\n⚙️ محدودیت روزانه: {db['settings']['daily_limit']} فایل"

    if update.callback_query:
        await update.callback_query.edit_message_text(f"🛠 **پنل مدیریت مدرن**\n\n{stats}", reply_markup=get_admin_markup(), parse_mode='Markdown')
    else:
        await update.message.reply_text(f"🛠 **پنل مدیریت مدرن**\n\n{stats}", reply_markup=get_admin_markup(), parse_mode='Markdown')


# --- پردازش پیام و صف ---
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # admin sets global limit (waiting_for_limit)
    if user_id == ADMIN_ID and context.user_data.get('waiting_for_limit'):
        if update.message.text.isdigit():
            new_limit = int(update.message.text)
            db["settings"]["daily_limit"] = new_limit
            save_db(db)
            context.user_data['waiting_for_limit'] = False
            return await update.message.reply_text(f"✅ محدودیت دانلود روزانه به {new_limit} تغییر یافت.")
        else:
            return await update.message.reply_text("❌ لطفاً فقط یک عدد انگلیسی ارسال کنید.")

    # admin sets personal limit for a user
    if user_id == ADMIN_ID and context.user_data.get('setting_user_limit_for'):
        target_uid = context.user_data.get('setting_user_limit_for')
        if update.message.text.isdigit():
            new_limit = int(update.message.text)
            if target_uid in db['users']:
                db['users'][target_uid]['personal_limit'] = new_limit
            else:
                db['users'][target_uid] = {"downloads_today": 0, "last_reset": str(datetime.now().date()), "status": "active", "personal_limit": new_limit}
            save_db(db)
            context.user_data.pop('setting_user_limit_for', None)
            return await update.message.reply_text(f"✅ محدودیت {new_limit} برای کاربر {target_uid} تنظیم شد.")
        else:
            return await update.message.reply_text("❌ لطفاً فقط یک عدد انگلیسی ارسال کنید.")

    u_data = check_user(user_id)

    if u_data["status"] == "banned":
        return await update.message.reply_text("🚫 دسترسی شما به ربات مسدود شده است.")

    url = update.message.text
    if url and url.startswith("http"):
        # بررسی محدودیت تعداد دانلود (اول شخصی، سپس کلی)
        limit = u_data.get('personal_limit') if u_data.get('personal_limit') is not None else db['settings'].get('daily_limit', 5)
        if u_data["downloads_today"] >= limit and user_id != ADMIN_ID:
            return await update.message.reply_text(f"⚠️ سقف دانلود روزانه شما ({limit}) تمام شده است.")

        if 'queue' not in context.chat_data:
            context.chat_data['queue'] = deque()
        context.chat_data['queue'].append(url)
        # ثبت کاربری که این دانلود را ایجاد کرده تا هنگام اتمام بتوانیم آمار را اعمال کنیم
        context.chat_data['initiator_id'] = user_id

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
    file_path = os.path.join(DOWNLOAD_DIR, chat_data.get('current_filename', ''))

    if res == "completed":
        initiator = str(chat_data.get('initiator_id', chat_id))
        # محافظت از اینکه اگر uid در db نیست، اضافه شود
        if initiator not in db['users']:
            db['users'][initiator] = {"downloads_today": 0, "last_reset": str(datetime.now().date()), "status": "active", "personal_limit": None}
        db["users"][initiator]["downloads_today"] += 1
        save_db(db)

        await context.bot.edit_message_text("✅ دانلود تمام شد. در حال ارسال به تلگرام...", chat_id, chat_data['msg_id'])

        if os.path.exists(file_path):
            is_vid = chat_data['current_filename'].lower().endswith(VIDEO_EXTS)
            file_size = os.path.getsize(file_path)

            # --- شروع بخش برش نهایی و قطعی ---
            if file_size > CHUNK_SIZE:
                await context.bot.edit_message_text("✂️ در حال قطعه‌قطعه کردن ویدیو (این کار ممکن است کمی طول بکشد)...", chat_id, chat_data['msg_id'])

                base_name, extension = os.path.splitext(chat_data['current_filename'])
                if not extension:
                    extension = ".mp4"
                clean_name = "".join([c for c in base_name if c.isalnum()]).strip()

                # ایجاد پوشه موقت
                temp_parts_dir = os.path.join(DOWNLOAD_DIR, f"parts_{chat_id}_{int(time.time())}")
                os.makedirs(temp_parts_dir, exist_ok=True)

                try:
                    output_template = os.path.join(temp_parts_dir, f"Part_%03d_{clean_name}{extension}")

                    command = [
                        'ffmpeg', '-y', '-i', file_path,
                        '-force_key_frames', 'expr:gte(t,n_forced*60)',
                        '-f', 'segment',
                        '-segment_time', '00:07:00',
                        '-reset_timestamps', '1',
                        '-map', '0',
                        '-c', 'copy',
                        output_template
                    ]

                    # اجرای ffmpeg به صورت غیرمسدود
                    await run_ffmpeg_async(command)

                    generated_parts = sorted([f for f in os.listdir(temp_parts_dir) if f.startswith("Part_")])

                    if not generated_parts:
                        raise Exception("No parts created")

                    total = len(generated_parts)
                    for i, p_file in enumerate(generated_parts, 1):
                        p_path = os.path.join(temp_parts_dir, p_file)
                        if chat_data.get('status') == 'cancelled':
                            break

                        if os.path.getsize(p_path) > 48 * 1024 * 1024:
                            logging.warning(f"Part too large even after segmentation: {p_path}")
                            continue

                        with open(p_path, 'rb') as tp:
                            caption = f"🎬 **{chat_data['current_filename']}**\n📦 پارت {i} از {total}"
                            await context.bot.send_video(
                                chat_id, video=tp, caption=caption,
                                supports_streaming=True, parse_mode='Markdown',
                                read_timeout=300, write_timeout=300
                            )

                        await safe_remove(p_path)
                        await asyncio.sleep(2)

                except Exception as e:
                    logging.exception("Final Attempt Error")
                    await context.bot.send_message(chat_id, "❌ متاسفانه به دلیل ساختار خاص این ویدیو، امکان برش هوشمند نبود.")

                finally:
                    def _rmdir(p):
                        import shutil
                        if os.path.exists(p):
                            shutil.rmtree(p)
                    await run_in_background(_rmdir, temp_parts_dir)
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
            await safe_remove(file_path)

        try:
            await context.bot.delete_message(chat_id, chat_data['msg_id'])
        except Exception:
            pass

        # اگر ادمین است، منوی ادمین را دوباره برایش بفرست
        try:
            await context.bot.send_message(ADMIN_ID, "🛠 پنل مدیریت (به‌روزرسانی)", reply_markup=get_admin_markup(), parse_mode='Markdown')
        except Exception:
            pass

        await run_next(chat_id, context)

    elif res == "cancelled":
        if os.path.exists(file_path):
            await safe_remove(file_path)
        await context.bot.send_message(chat_id, "❌ دانلود لغو شد.")
        await run_next(chat_id, context)

    else:
        # خطا
        try:
            await context.bot.edit_message_text(f"❌ خطا: {res}", chat_id, chat_data.get('msg_id'))
        except Exception:
            await context.bot.send_message(chat_id, f"❌ خطا: {res}")
        await run_next(chat_id, context)


# --- Callback router and handlers ---
async def callback_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = update.effective_chat.id

    # مدیریت دانلودها (همیشه پردازش شوند)
    if data == "dl_pause":
        context.chat_data['status'] = 'paused'
        await query.answer("متوقف شد")
        return
    elif data == "dl_resume":
        context.chat_data['status'] = 'downloading'
        await query.answer("ادامه دانلود")
        asyncio.create_task(download_engine_wrapper(chat_id, context))
        return
    elif data == "dl_cancel":
        context.chat_data['status'] = 'cancelled'
        file_path = os.path.join(DOWNLOAD_DIR, context.chat_data.get('current_filename', ''))
        if os.path.exists(file_path):
            await safe_remove(file_path)
        await query.edit_message_text("❌ دانلود لغو شد.")
        await run_next(chat_id, context)
        return

    # اگر callback مربوط به ادمین است، به رجیستری بسپار
    if data and data.startswith("adm_"):
        key = data.split(':')[0]
        handler = ADMIN_CALLBACKS.get(key)
        if handler:
            await handler(update, context)
        else:
            await query.answer("❌ دستور نامعتبر")
        return

    # سایر callbackهای غیر ادمینی را اینجا پردازش کن (در صورت نیاز)
    await query.answer()


async def download_engine_wrapper(chat_id, context):
    res = await download_engine(chat_id, context, context.chat_data['current_url'], context.chat_data['current_filename'])
    await finalize_dl(chat_id, context, res)


# --- ADMIN handlers (ثبت در رجیستری) ---
@register_admin_callback("adm_clear_confirm")
async def adm_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(
        "⚠️ مطمئنی می‌خوای همه فایل‌ها پاک بشن؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله پاک کن", callback_data="adm_clear"), InlineKeyboardButton("❌ نه", callback_data="adm_main")]
        ])
    )


@register_admin_callback("adm_clear")
async def adm_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⏳ در حال پاکسازی ...")

    def clear_folder():
        cnt = 0
        for f in os.listdir(DOWNLOAD_DIR):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, f))
                cnt += 1
            except Exception:
                pass
        return cnt

    cnt = await run_in_background(clear_folder)
    await update.callback_query.edit_message_text(f"🧹 پاکسازی انجام شد — {cnt} فایل حذف شد.")


@register_admin_callback("adm_logs")
async def adm_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ارسال فایل لاگ به عنوان فایل متنی (برای جلوگیری از خطای "Message is too long")
    if not os.path.exists(LOG_FILE):
        await update.callback_query.answer("❌ فایلی یافت نشد", show_alert=True)
        return

    try:
        with open(LOG_FILE, 'rb') as f:
            await update.callback_query.message.reply_document(document=f, caption="📜 فایل لاگ سیستم")
    except Exception:
        # در صورت مشکل، آخرین خطوط را نمایش بده
        def tail_file(path, lines=200):
            with open(path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                block = 1024
                data = b''
                while size > 0 and data.count(b'
') <= lines:
                    size = max(0, size - block)
                    f.seek(size)
                    chunk = f.read(block)
                    data = chunk + data
                    if size == 0:
                        break
                return data.decode(errors='ignore').splitlines()[-lines:]

        tail = await run_in_background(tail_file, LOG_FILE, 200)
        await update.callback_query.message.reply_text(f"📜 آخرین خطوط لاگ:

{chr(10).join(tail)}")


@register_admin_callback("adm_history")
async def adm_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_dl = sum(u['downloads_today'] for u in db['users'].values())
    msg = f"📈 **آمار سیستم:**\n\nکل دانلودهای امروز: {total_dl}"
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


@register_admin_callback("adm_main")
async def adm_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_menu(update, context)


@register_admin_callback("adm_users")
async def adm_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data  # e.g. "adm_users:0"
    parts = data.split(':')
    page = int(parts[1]) if len(parts) > 1 else 0

    users = list(db['users'].items())
    start = page * PAGE_SIZE
    page_users = users[start:start + PAGE_SIZE]

    kb = []
    for uid, info in page_users:
        status = info.get('status', 'active')
        personal = info.get('personal_limit') if info.get('personal_limit') is not None else '-'
        btn_text = f"{uid} ({status}) - limit: {personal}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=f"adm_user:{uid}:{page}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"adm_users:{page-1}"))
    if start + PAGE_SIZE < len(users):
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"adm_users:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")])

    await update.callback_query.edit_message_text("👥 مدیریت کاربران:", reply_markup=InlineKeyboardMarkup(kb))


@register_admin_callback("adm_user")
async def adm_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # data pattern: adm_user:<uid>:<page>
    parts = update.callback_query.data.split(':')
    uid = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    info = db['users'].get(uid, {})
    msg = f"👤 کاربر: {uid}\nوضعیت: {info.get('status','active')}\nدانلود‌های امروز: {info.get('downloads_today',0)}\nمحدودیت شخصی: {info.get('personal_limit', '-') }"
    kb = [
        [InlineKeyboardButton("⛔️ بلاک", callback_data=f"adm_ban:{uid}:{page}"), InlineKeyboardButton("✅ آنبلاک", callback_data=f"adm_unban:{uid}:{page}")],
        [InlineKeyboardButton("🔢 تنظیم محدودیت کاربر", callback_data=f"adm_set_user_limit:{uid}:{page}" )],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm_users:{page}")]
    ]
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))


@register_admin_callback("adm_ban")
async def adm_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.callback_query.data.split(':')
    uid = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    if uid in db['users']:
        db['users'][uid]['status'] = 'banned'
        save_db(db)
    await update.callback_query.answer("کاربر مسدود شد")
    await adm_users(update, context)


@register_admin_callback("adm_unban")
async def adm_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.callback_query.data.split(':')
    uid = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    if uid in db['users']:
        db['users'][uid]['status'] = 'active'
        save_db(db)
    await update.callback_query.answer("کاربر آزاد شد")
    await adm_users(update, context)


@register_admin_callback("adm_set_user_limit")
async def adm_set_user_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.callback_query.data.split(':')
    uid = parts[1]
    context.user_data['setting_user_limit_for'] = uid
    await update.callback_query.edit_message_text(f"لطفاً عدد جدید محدودیت دانلود روزانه برای کاربر {uid} را ارسال کنید:",
                                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data=f"adm_user:{uid}:0")]]))


@register_admin_callback("adm_settings")
async def adm_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = f"⚙️ تنظیمات سیستم:

محدودیت کلی فعلی: {db['settings'].get('daily_limit')}"
    kb = [
        [InlineKeyboardButton("🔢 تغییر محدودیت کلی", callback_data="adm_set_limit")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]
    ]
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))


@register_admin_callback("adm_set_limit")
async def adm_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['waiting_for_limit'] = True
    await update.callback_query.edit_message_text("لطفاً عدد جدید محدودیت دانلود روزانه کلی را ارسال کنید:",
                                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="adm_settings")]]))


@register_admin_callback("adm_files")
async def adm_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = os.listdir(DOWNLOAD_DIR)
    total_size = sum(os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) for f in files)
    msg = f"📂 فایل‌های دانلود شده: {len(files)}\nحجم کل: {human_readable_size(total_size)}"
    kb = [[InlineKeyboardButton("🧹 پاکسازی", callback_data="adm_clear_confirm")], [InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))


@register_admin_callback("adm_active")
async def adm_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نمایش وضعیت پوشه دانلود و محتویات صف چت ادمین (اگر وجود داشته باشد)
    files = os.listdir(DOWNLOAD_DIR)
    pending = len(files)
    msg = f"📥 در حال دانلود / صف: {pending} فایل در پوشه دانلود (این عدد شامل فایل‌های کامل و پارت‌ها می‌شود)."
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="adm_main")]]
    await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))


@register_admin_callback("adm_reset_stats")
async def adm_reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for uid in db['users']:
        db['users'][uid]['downloads_today'] = 0
        db['users'][uid]['last_reset'] = str(datetime.now().date())
    save_db(db)
    await update.callback_query.answer("آمار کاربران بازنشانی شد")
    await adm_main(update, context)


@register_admin_callback("adm_exit")
async def adm_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🔙 خروج از پنل مدیریت")


# --- Error handler برای لاگ کامل خطاها ---
async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled error:")
    try:
        if update and update.effective_user:
            await context.bot.send_message(ADMIN_ID, f"⚠️ خطای برنامه: {context.error}")
    except Exception:
        pass


# --- اجرای اصلی ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, filename=LOG_FILE, format='%(asctime)s - %(levelname)s - %(message)s')

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.add_handler(CallbackQueryHandler(callback_gate))
    app.add_error_handler(global_error_handler)

    print("🤖 Bot Started...")
    app.run_polling()
