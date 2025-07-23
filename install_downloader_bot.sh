#!/bin/bash

set -e

echo "🚀 Telegram File Downloader Bot Setup"

INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
PYTHON_SCRIPT="download_bot.py"

# گرفتن اطلاعات از کاربر
read -p "Enter your Telegram Bot Token: " bot_token

# دایرکتوری نصب توسط setup_downloader_bot.sh ایجاد و کلون شده است، پس فقط به آن cd می‌کنیم
# mkdir -p "$INSTALL_DIR" # این خط دیگر لازم نیست
# cd "$INSTALL_DIR" || exit # این cd در setup_downloader_bot.sh انجام شده است

# کپی کردن فایل‌های ربات و requirements
# این خطوط دیگر لازم نیستند زیرا فایل‌ها توسط git clone از قبل در $INSTALL_DIR موجودند.
# cp "$(dirname "$0")/$PYTHON_SCRIPT" "$INSTALL_DIR/"
# cp "$(dirname "$0")/requirements.txt" "$INSTALL_DIR/"

# جایگزینی توکن در فایل پایتون
echo "Updating bot token in $PYTHON_SCRIPT..."
# چون اسکریپت install_downloader_bot.sh اکنون داخل $INSTALL_DIR اجرا می‌شود،
# نیازی به استفاده از $(dirname "$0") برای مسیردهی به $PYTHON_SCRIPT نیست.
sed -i "s|TOKEN = \"YOUR_TELEGRAM_BOT_TOKEN\"|TOKEN = \"$bot_token\"|" "$PYTHON_SCRIPT"

echo "✅ Bot token updated successfully."

# نصب ابزار لازم
echo "Installing system dependencies (python3-venv, git)..."
apt update -y
apt install python3-venv git -y

# ساخت محیط مجازی و نصب پکیج‌ها
echo "Creating Python virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt # requirements.txt نیز در همین دایرکتوری است
deactivate

echo "✅ Python dependencies installed."

# ساخت systemd سرویس
echo "Creating systemd service file for $SERVICE_NAME..."
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram File Downloader Bot
After=network.target

[Service]
ExecStart=$(pwd)/venv/bin/python $(pwd)/$PYTHON_SCRIPT
WorkingDirectory=$(pwd)
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# فعال‌سازی و اجرا
echo "Reloading systemd, enabling and starting $SERVICE_NAME..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "✅ Installation and setup completed successfully."
echo "📡 Check bot status with: sudo systemctl status $SERVICE_NAME"
echo "📖 View bot logs with: sudo journalctl -u $SERVICE_NAME -f"
