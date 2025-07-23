import os
import requests
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# فعال کردن لاگ‌گیری برای دیدن خطاها
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# توکن ربات خود را اینجا قرار دهید
# این مقدار توسط اسکریپت نصب جایگذاری خواهد شد
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# این تابع زمانی اجرا می‌شود که کاربر دستور /start را ارسال کند
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"سلام {user.mention_html()}!\n\n"
        f"لطفاً یک لینک مستقیم برای دانلود فایل برای من ارسال کنید. من آن را دانلود کرده و برای شما ارسال می‌کنم.",
    )

# این تابع اصلی است که لینک را پردازش، دانلود و ارسال می‌کند
async def download_and_send_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text
    chat_id = update.message.chat_id

    if not url.startswith('http'):
        await update.message.reply_text("این یک لینک معتبر به نظر نمی‌رسد. لطفاً یک لینک با http یا https ارسال کنید.")
        return

    message = await update.message.reply_text("در حال پردازش لینک... لطفاً صبر کنید.")

    try:
        filename = url.split('/')[-1].split('?')[0] # بخش پارامترهای GET را از نام فایل حذف می‌کند
        if not filename:
            filename = "downloaded_file"

        await message.edit_text(f"شروع دانلود فایل: `{filename}`\nاین مرحله ممکن است زمان‌بر باشد...")

        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        logger.info(f"فایل {filename} با موفقیت دانلود شد.")

        await message.edit_text("دانلود کامل شد. در حال ارسال فایل برای شما...")
        
        await context.bot.send_document(chat_id=chat_id, document=open(filename, 'rb'))

        await context.bot.delete_message(chat_id=chat_id, message_id=message.message_id)

    except requests.exceptions.RequestException as e:
        logger.error(f"خطا در دانلود از URL: {url} - خطا: {e}")
        await message.edit_text(f"متاسفانه در دانلود فایل از این لینک مشکلی پیش آمد: \n`{e}`")
    except Exception as e:
        logger.error(f"یک خطای پیش‌بینی نشده رخ داد: {e}")
        await message.edit_text(f"یک خطای غیرمنتظره رخ داد. لطفاً دوباره تلاش کنید. \nخطا: `{e}`")
    finally:
        if os.path.exists(filename):
            os.remove(filename)
            logger.info(f"فایل {filename} از سرور پاک شد.")

def main() -> None:
    """ربات را راه‌اندازی می‌کند."""
    # ساخت اپلیکیشن با استفاده از توکن
    application = Application.builder().token(TOKEN).build()

    # ثبت handler ها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send_file))

    # راه‌اندازی ربات
    application.run_polling()

if __name__ == '__main__':
    main()
