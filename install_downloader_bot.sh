#!/bin/bash

set -e

echo "🚀 Telegram File Downloader Bot Setup"

INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
PYTHON_SCRIPT="download_bot.py"

# گرفتن اطلاعات از کاربر
read -p "Enter your Telegram Bot Token: " bot_token

# ایجاد دایرکتوری نصب
echo "Creating installation directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR" || exit

# کپی کردن فایل‌های ربات و requirements
echo "Copying bot files..."
# فرض می‌کنیم اسکریپت install_downloader_bot.sh و فایل‌های bot.py و requirements.txt در یک دایرکتوری هستند
# اگر در دایرکتوری فعلی نیستند، باید مسیرهای کامل را مشخص کنید
cp "$(dirname "$0")/$PYTHON_SCRIPT" "$INSTALL_DIR/"
cp "$(dirname "$0")/requirements.txt" "$INSTALL_DIR/"

# جایگزینی توکن در فایل پایتون
echo "Updating bot token in $PYTHON_SCRIPT..."
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
pip install -r requirements.txt
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
