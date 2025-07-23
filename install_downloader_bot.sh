#!/bin/bash
#
# فایل: install_downloader_bot.sh
#
set -e

SERVICE_NAME="telegramdownloaderbot"

# گرفتن اطلاعات از کاربر
read -p "Enter your Telegram Bot Token: " bot_token
echo "TOKEN = \"$bot_token\"" > token.py
echo "✅ Bot token saved to token.py"

# نصب ابزار لازم
echo "Installing system dependencies (python3-venv, git)..."
apt update -y > /dev/null
apt install python3-venv git -y > /dev/null

# ساخت محیط مجازی و نصب پکیج‌ها
echo "Creating Python virtual environment and installing dependencies..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel > /dev/null
pip install -r requirements.txt > /dev/null
deactivate
echo "✅ Python dependencies installed."

# ساخت systemd سرویس
echo "Creating systemd service file for $SERVICE_NAME..."
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

# Get absolute path for the service file
INSTALL_DIR=$(pwd)
PYTHON_EXEC="$INSTALL_DIR/venv/bin/python"
PYTHON_SCRIPT_PATH="$INSTALL_DIR/download_bot.py"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram File Downloader Bot
After=network.target

[Service]
ExecStart=$PYTHON_EXEC $PYTHON_SCRIPT_PATH
WorkingDirectory=$INSTALL_DIR
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
