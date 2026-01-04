#!/bin/bash
INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
GITHUB_REPO="https://github.com/0fariid0/TelegramFileDownloaderBot.git"
CONFIG_FILE="$INSTALL_DIR/bot_config.py"

# تابع کمکی برای خواندن مقادیر فعلی
get_cfg() {
    local key=$1
    if [ -f "$CONFIG_FILE" ]; then
        grep "$key =" "$CONFIG_FILE" | sed "s/.*= //" | tr -d '"'
    else
        echo "Not Set"
    fi
}

show_menu() {
  clear
  echo "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓"
  echo "┃ ⚙️ Telegram Bot Manager (Advanced)   ┃"
  echo "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
  echo "1) 🛠 Install/Reinstall Bot"
  echo "2) ⚙️ Configure (Token & Admin ID)"
  echo "3) 🔄 Update Source Code"
  echo "4) 📜 View Live Logs"
  echo "5) 🛰 Service Status & Restart"
  echo "6) ❌ Uninstall Bot"
  echo "0) 🚪 Exit"
  echo ""
  read -p "Select an option: " choice
}

configure_bot() {
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "⚠️ Bot not installed!"
    else
        curr_token=$(get_cfg "TOKEN")
        curr_admin=$(get_cfg "ADMIN_ID")
        echo "--- Current Settings ---"
        echo "Token: $curr_token"
        echo "Admin: $curr_admin"
        echo "-----------------------"
        read -p "New Token (Enter to skip): " n_token
        read -p "New Admin ID (Enter to skip): " n_admin
        
        # اگر کاربر اینتر زد، همان مقدار قبلی بماند
        [ -z "$n_token" ] && n_token=$curr_token
        [ -z "$n_admin" ] && n_admin=$curr_admin

        cat > "$CONFIG_FILE" <<EOF
TOKEN = "$n_token"
ADMIN_ID = $n_admin
EOF
        systemctl restart "$SERVICE_NAME"
        echo "✅ Settings updated and Bot restarted."
    fi
    read -p "⏎ Press Enter..." _
}

install_bot() {
    echo "📦 Installing..."
    [ -d "$INSTALL_DIR" ] && rm -rf "$INSTALL_DIR"
    git clone "$GITHUB_REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR" && bash install_downloader_bot.sh
    cd - > /dev/null
    read -p "⏎ Press Enter..." _
}

update_bot() {
    echo "🔄 Updating..."
    cd "$INSTALL_DIR" && git stash && git pull origin main && git stash pop
    source venv/bin/activate && pip install -r requirements.txt && deactivate
    systemctl restart "$SERVICE_NAME"
    echo "✅ Updated."
    cd - > /dev/null
    read -p "⏎ Press Enter..." _
}

while true; do
  show_menu
  case $choice in
    1) install_bot ;;
    2) configure_bot ;;
    3) update_bot ;;
    4) journalctl -u "$SERVICE_NAME" -f ;;
    5) 
       systemctl status "$SERVICE_NAME"
       read -p "Do you want to restart? (y/n): " res
       [[ "$res" == "y" ]] && systemctl restart "$SERVICE_NAME" && echo "Restarted."
       read -p "⏎ Press Enter..." _
       ;;
    6) 
       systemctl stop "$SERVICE_NAME" && systemctl disable "$SERVICE_NAME"
       rm -rf "$INSTALL_DIR" "/etc/systemd/system/$SERVICE_NAME.service"
       systemctl daemon-reload
       echo "✅ Uninstalled."
       sleep 2
       ;;
    0) exit 0 ;;
    *) echo "Invalid option"; sleep 1 ;;
  esac
done
