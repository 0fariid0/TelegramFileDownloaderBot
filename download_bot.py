import os
import requests
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler
)

# فعال کردن لاگ‌گیری برای دیدن خطاها
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# توکن ربات خود را اینجا قرار دهید
# این مقدار توسط اسکریپت نصب جایگذاری خواهد شد
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# تعریف حالت‌های مکالمه برای ConversationHandler
WAITING_URL, DOWNLOADING = range(2)
# حداکثر حجم فایل برای دانلود (به بایت). تلگرام برای ربات‌ها محدودیت آپلود ۵۰ مگابایتی دارد.
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# --- توابع اصلی ربات ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """با دستور /start اجرا می‌شود و کاربر را به حالت ارسال لینک هدایت می‌کند."""
    user = update.effective_user
    await update.message.reply_html(
        f"سلام {user.mention_html()}! 👋\n\n"
        f"لطفاً یک لینک مستقیم برای دانلود فایل ارسال کنید. من آن را برای شما آپلود می‌کنم.",
    )
    return WAITING_URL

async def process_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """لینک ارسال شده توسط کاربر را پردازش می‌کند."""
    url = update.message.text
    chat_id = update.message.chat_id

    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ لینک نامعتبر است. لطفاً یک لینک با http یا https ارسال کنید.")
        return WAITING_URL

    try:
        # ابتدا با یک درخواست HEAD اطلاعات اولیه فایل را می‌گیریم
        with requests.head(url, allow_redirects=True, timeout=10) as r:
            r.raise_for_status()
            # بررسی حجم فایل
            content_length = r.headers.get('content-length')
            if content_length and int(content_length) > MAX_FILE_SIZE:
                await update.message.reply_text(f"❌ حجم فایل بیشتر از ۵۰ مگابایت است و قابل ارسال نیست.")
                return WAITING_URL

            # استخراج نام فایل از هدرها یا URL
            filename = "downloaded_file"
            if "content-disposition" in r.headers:
                cd = r.headers.get('content-disposition')
                filename = cd.split('filename=')[-1].strip('"')
            else:
                filename = url.split('/')[-1].split('?')[0] or filename
        
        context.user_data['url'] = url
        context.user_data['filename'] = filename

        keyboard = [[InlineKeyboardButton("لغو عملیات ❌", callback_data='cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message = await update.message.reply_text(
            f"⏳ در حال آماده‌سازی برای دانلود...\n\n"
            f"**نام فایل:** `{filename}`",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        context.user_data['status_message_id'] = message.message_id

        # شروع دانلود
        return await download_file(update, context)

    except requests.exceptions.RequestException as e:
        logger.error(f"خطا در پردازش URL: {url} - خطا: {e}")
        await update.message.reply_text(f"متاسفانه در اتصال به لینک مشکلی پیش آمد: \n`{e}`")
        return WAITING_URL

async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """فایل را دانلود و نوار پیشرفت را به‌روزرسانی می‌کند."""
    url = context.user_data['url']
    filename = context.user_data['filename']
    chat_id = update.effective_chat.id
    message_id = context.user_data['status_message_id']

    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded_size = 0
            last_update_time = 0

            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    # بررسی اینکه آیا کاربر عملیات را لغو کرده است
                    if context.user_data.get('cancel_download'):
                        await context.bot.edit_message_text(
                            "عملیات دانلود توسط شما لغو شد.",
                            chat_id=chat_id,
                            message_id=message_id
                        )
                        return ConversationHandler.END

                    f.write(chunk)
                    downloaded_size += len(chunk)
                    current_time = time.time()

                    # به‌روزرسانی نوار پیشرفت هر ۲ ثانیه یک‌بار برای جلوگیری از اسپم API
                    if current_time - last_update_time > 2:
                        last_update_time = current_time
                        await update_progress(context, message_id, chat_id, downloaded_size, total_size, filename)
            
        logger.info(f"فایل {filename} با موفقیت دانلود شد.")
        
        await context.bot.edit_message_text("✅ دانلود کامل شد. در حال آپلود فایل برای شما...", chat_id=chat_id, message_id=message_id)
        
        await context.bot.send_document(chat_id=chat_id, document=open(filename, 'rb'))
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)

    except Exception as e:
        logger.error(f"خطای پیش‌بینی نشده در دانلود: {e}")
        await context.bot.edit_message_text(f"یک خطای غیرمنتظره رخ داد: `{e}`", chat_id=chat_id, message_id=message_id)
    
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            logger.info(f"فایل موقت {filename} از سرور پاک شد.")
    
    return ConversationHandler.END

async def update_progress(context: ContextTypes.DEFAULT_TYPE, message_id, chat_id, downloaded, total, filename):
    """پیام وضعیت را با نوار پیشرفت آپدیت می‌کند."""
    percent = (downloaded / total) * 100 if total > 0 else 0
    progress_bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
    text = (
        f"**در حال دانلود...**\n\n"
        f"**فایل:** `{filename}`\n"
        f"`{progress_bar}` {percent:.1f}%\n"
        f"`{downloaded // 1024 // 1024} MB / {total // 1024 // 1024} MB`"
    )
    keyboard = [[InlineKeyboardButton("لغو عملیات ❌", callback_data='cancel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        # ممکن است پیام تغییری نکرده باشد، این خطا طبیعی است
        if "Message is not modified" not in str(e):
            logger.warning(f"خطا در آپدیت نوار پیشرفت: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """عملیات دانلود را لغو می‌کند."""
    query = update.callback_query
    await query.answer("در حال لغو کردن...")
    
    if context.user_data.get('status_message_id'):
        context.user_data['cancel_download'] = True
    else:
        # اگر دانلود هنوز شروع نشده بود
        await query.edit_message_text("عملیات لغو شد.")
        return ConversationHandler.END
    return DOWNLOADING # در همین حالت می‌ماند تا حلقه دانلود آن را تشخیص دهد

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """در صورت بروز خطا یا اتمام مکالمه، وضعیت را پاک می‌کند."""
    context.user_data.clear()
    return ConversationHandler.END


def main() -> None:
    """ربات را راه‌اندازی می‌کند."""
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_url)],
            DOWNLOADING: [CallbackQueryHandler(cancel, pattern='^cancel$')]
        },
        fallbacks=[CommandHandler("start", start)], # با دستور استارت مجدد، ریست می‌شود
        conversation_timeout=300 # مکالمه پس از ۵ دقیقه عدم فعالیت، تمام می‌شود
    )

    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
