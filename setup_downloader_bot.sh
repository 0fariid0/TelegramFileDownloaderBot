#!/bin/bash

INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
PYTHON_SCRIPT="download_bot.py"
GITHUB_REPO="https://github.com/0fariid0/TelegramFileDownloaderBot.git" # آدرس گیت‌هاب شما

# Function to display the menu
show_menu() {
  clear
  echo "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓"
  echo "┃ ⚙️ Telegram File Downloader Bot Setup ┃"
  echo "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛"
  echo ""
  echo "1) 🛠 Install the bot"
  echo "2) ⚙️ Configure the bot (Change Token)"
  echo "3) 🔄 Update the bot"
  echo "4) ❌ Uninstall the bot"
  echo "0) 🚪 Exit"
  echo ""
  read -p "Your choice: " choice
}

# Function to install the bot
install_bot() {
  echo "📦 Installing the bot..."
  # Clean up previous installation if exists
  if [ -d "$INSTALL_DIR" ]; then
    echo "Existing installation found. Removing old files..."
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true
    rm -f /etc/systemd/system/"$SERVICE_NAME".service || true
    systemctl daemon-reload || true
    rm -rf "$INSTALL_DIR" || true
  fi

  # Clone the repository
  echo "Cloning the repository from $GITHUB_REPO to $INSTALL_DIR..."
  git clone "$GITHUB_REPO" "$INSTALL_DIR" || { echo "❌ Failed to clone repository."; read -p "⏎ Press Enter to return to the menu..." _; return 1; }
  
  # Go into the installed directory to run the actual install script
  cd "$INSTALL_DIR" || exit

  # Run the actual installation script within the cloned directory
  bash install_downloader_bot.sh
  
  cd - > /dev/null # Go back to previous directory silently

  echo "✅ Installation completed successfully."
  read -p "⏎ Press Enter to return to the menu..." _
}

# Function to configure the bot (change token)
configure_bot() {
  BOT_FILE="$INSTALL_DIR/$PYTHON_SCRIPT"
  if [ ! -f "$BOT_FILE" ]; then
    echo "⚠️ Bot script not found. Please install the bot first."
  else
    read -p "Enter new Telegram Bot Token: " new_bot_token
    sed -i "s|TOKEN = \".*\"|TOKEN = \"$new_bot_token\"|" "$BOT_FILE"
    echo "🔄 Restarting the bot service..."
    systemctl restart "$SERVICE_NAME"
    echo "✅ Configuration saved and bot restarted."
  fi
  read -p "⏎ Press Enter to return to the menu..." _
}

# Function to update the bot (pulling latest changes from git)
update_bot() {
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "⚠️ Git repository not found in $INSTALL_DIR. Please install the bot first."
  else
    echo "🔄 Updating the bot to the latest version..."
    cd "$INSTALL_DIR" || exit
    git pull origin main || { echo "❌ Failed to update repository. Check for local changes or network issues."; read -p "⏎ Press Enter to return to the menu..." _; cd - > /dev/null; return 1; }
    echo "🔄 Restarting the bot service..."
    systemctl restart "$SERVICE_NAME"
    echo "✅ Bot updated and restarted successfully."
    cd - > /dev/null # Go back to previous directory silently
  fi
  read -p "⏎ Press Enter to return to the menu..." _
}


# Function to uninstall the bot
uninstall_bot() {
  echo "❌ Uninstalling the bot completely..."
  systemctl stop "$SERVICE_NAME" || true
  systemctl disable "$SERVICE_NAME" || true
  rm -f /etc/systemd/system/"$SERVICE_NAME".service || true
  systemctl daemon-reload || true
  rm -rf "$INSTALL_DIR" || true
  echo "✅ Bot and all files have been removed."
  read -p "⏎ Press Enter to return to the menu..." _
}

# Main loop
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
