#!/bin/bash
INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
GITHUB_REPO="https://github.com/0fariid0/TelegramFileDownloaderBot.git"

show_menu() {
  clear
  echo "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓"
  echo "┃ ⚙️ Telegram File Downloader Bot Setup ┃"
  echo "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
  echo ""
  echo "1) 🛠 Install or Reinstall the bot"
  echo "2) ⚙️ Configure the bot (Change Token)"
  echo "3) 🔄 Update the bot"
  echo "4) ❌ Uninstall the bot"
  echo "0) 🚪 Exit"
  echo ""
  read -p "Your choice: " choice
}

install_bot() {
  echo "📦 Installing the bot..."
  if [ -d "$INSTALL_DIR" ]; then
    echo "Existing installation found. Performing a clean re-installation."
    systemctl stop "$SERVICE_NAME" &>/dev/null
    rm -rf "$INSTALL_DIR"
  fi

  echo "Cloning the repository from $GITHUB_REPO to $INSTALL_DIR..."
  git clone "$GITHUB_REPO" "$INSTALL_DIR" || { echo "❌ Failed to clone repository."; exit 1; }
  
  cd "$INSTALL_DIR" || exit
  bash install_downloader_bot.sh
  
  cd - > /dev/null
  echo "✅ Installation process finished."
  read -p "⏎ Press Enter to return to the menu..." _
}

configure_bot() {
  if [ ! -d "$INSTALL_DIR" ]; then
    echo "⚠️ Bot is not installed. Please install it first."
  else
    cd "$INSTALL_DIR" || exit
    read -p "Enter your new Telegram Bot Token: " new_bot_token
    echo "TOKEN = \"$new_bot_token\"" > bot_config.py
    echo "🔄 Restarting the bot service..."
    systemctl restart "$SERVICE_NAME"
    echo "✅ Bot token updated and service restarted."
    cd - > /dev/null
  fi
  read -p "⏎ Press Enter to return to the menu..." _
}

update_bot() {
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "⚠️ Git repository not found. Please install the bot first."
  else
    echo "🔄 Updating the bot to the latest version..."
    cd "$INSTALL_DIR" || exit
    
    echo "Stashing local changes (like bot_config.py)..."
    git stash
    
    echo "Pulling latest changes from GitHub..."
    if git pull origin main; then
        echo "Re-applying local changes..."
        git stash pop 2>/dev/null || echo "No local changes to re-apply."
        
        echo "Re-installing dependencies to ensure compatibility..."
        source venv/bin/activate
        pip install --upgrade pip wheel > /dev/null
        pip install -r requirements.txt > /dev/null
        deactivate
        
        echo "🔄 Restarting the bot service..."
        systemctl restart "$SERVICE_NAME"
        echo "✅ Bot updated and restarted successfully."
    else
        echo "❌ Failed to update repository. Rolling back changes..."
        git stash pop 2>/dev/null
        echo "Update failed. Your local files are safe."
    fi
    cd - > /dev/null
  fi
  read -p "⏎ Press Enter to return to the menu..." _
}

uninstall_bot() {
  read -p "Are you sure you want to uninstall the bot completely? (y/n): " confirm
  if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
    echo "❌ Uninstalling the bot completely..."
    systemctl stop "$SERVICE_NAME" &>/dev/null
    systemctl disable "$SERVICE_NAME" &>/dev/null
    rm -f "/etc/systemd/system/$SERVICE_NAME.service"
    systemctl daemon-reload
    rm -rf "$INSTALL_DIR"
    echo "✅ Bot and all its files have been removed."
  else
    echo "Uninstall cancelled."
  fi
  read -p "⏎ Press Enter to return to the menu..." _
}

while true; do
  show_menu
  case $choice in
    1) install_bot ;;
    2) configure_bot ;;
    3) update_bot ;;
    4) uninstall_bot ;;
    0) echo "👋 Exiting. Goodbye!"; exit 0 ;;
    *) echo "❌ Invalid option. Please choose a valid one."; sleep 2 ;;
  esac
done
