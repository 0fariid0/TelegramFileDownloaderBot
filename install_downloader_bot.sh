#!/bin/bash
set -e

SERVICE_NAME="telegramdownloaderbot"
CONFIG_FILE="bot_config.py"

# دریافت اطلاعات از کاربر
read -p "Enter your Telegram Bot Token: " bot_token
read -p "Enter your Numerical Admin ID: " admin_id

# ذخیره در فایل کانفیگ
cat > "$CONFIG_FILE" <<EOF
TOKEN = "$bot_token"
ADMIN_ID = $admin_id
EOF

echo "✅ Configuration saved to $CONFIG_FILE"

echo "Installing system dependencies..."
apt update -y > /dev/null
apt install python3-venv git -y > /dev/null

echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel > /dev/null
# نصب کتابخانه‌های مورد نیاز (اگر requirements.txt وجود ندارد، دستی نصب می‌شوند)
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt > /dev/null
else
    pip install python-telegram-bot httpx > /dev/null
fi
deactivate
echo "✅ Dependencies installed."

echo "Creating systemd service file..."
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
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

echo "Starting service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "✅ Installation completed successfully."
