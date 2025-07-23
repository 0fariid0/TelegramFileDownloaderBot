#!/bin/bash

INSTALL_DIR="/opt/telegram_downloader_bot"
SERVICE_NAME="telegramdownloaderbot"
PYTHON_SCRIPT="download_bot.py"
GITHUB_REPO="https://github.com/0fariid0/TelegramFileDownloaderBot.git"

# Function to display the menu
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

# Function to install the bot
install_bot() {
  echo "📦 Installing the bot..."
  if [ -d "$INSTALL_DIR" ]; then
    echo "Existing installation found. Removing old files..."
    systemctl stop "$SERVICE_NAME" || true
    rm -rf "$INSTALL_DIR"
  fi

  echo "Cloning the repository from $GITHUB_REPO to $INSTALL_DIR..."
  git clone "$GITHUB_REPO" "$INSTALL_DIR" || { echo "❌ Failed to clone repository."; exit 1; }
  
  cd "$INSTALL_DIR" || exit
  
  # Run the configuration part
  configure_bot
  
  echo "Installing system dependencies (python3-venv, git)..."
  apt update -y > /dev/null
  apt install python3-venv git -y > /dev/null
  
  echo "Creating Python virtual environment and installing dependencies..."
  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip > /dev/null
  pip install -r requirements.txt > /dev/null
  deactivate
  
  echo "✅ Python dependencies installed."
  
  # Create and enable systemd service
  create_service
  
  echo "✅ Installation and setup completed successfully."
  read -p "⏎ Press Enter to return to the menu..." _
}

# Function to configure the bot token
configure_bot() {
    cd "$INSTALL_DIR" || { echo "Installation directory not found!"; return; }
    read -p "Enter your Telegram Bot Token: " bot_token
    # Create a separate file for the token to avoid git conflicts
    echo "TOKEN = \"$bot_token\"" > token.py
    # Modify the main script to import the token
    if ! grep -q "from token import TOKEN" "$PYTHON_SCRIPT"; then
        sed -i '1i from token import TOKEN' "$PYTHON_SCRIPT"
        sed -i '/^TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"$/d' "$PYTHON_SCRIPT"
    fi
    echo "✅ Bot token configured successfully."
}

# Function to create and start the systemd service
create_service() {
    echo "Creating systemd service file for $SERVICE_NAME..."
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
    
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram File Downloader Bot
After=network.target

[Service]
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/$PYTHON_SCRIPT
WorkingDirectory=$INSTALL_DIR
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    echo "Reloading systemd, enabling and starting $SERVICE_NAME..."
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
}

# Function to update the bot
update_bot() {
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "⚠️ Git repository not found in $INSTALL_DIR. Please install the bot first."
  else
    echo "🔄 Updating the bot to the latest version..."
    cd "$INSTALL_DIR" || exit
    
    # Stash local changes (like the token file) before pulling
    git stash
    
    # Pull the latest changes
    git pull origin main || { 
        echo "❌ Failed to update repository. Rolling back changes..."; 
        git stash pop;
        cd - > /dev/null;
        read -p "⏎ Press Enter to return to the menu..." _;
        return 1; 
    }
    
    # Re-apply the stashed changes
    git stash pop || echo "No local changes to re-apply."

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
  rm -f /etc/systemd/system/"$SERVICE_NAME".service
  systemctl daemon-reload
  rm -rf "$INSTALL_DIR"
  echo "✅ Bot and all files have been removed."
  read -p "⏎ Press Enter to return to the menu..." _
}

# Main loop
while true; do
  show_menu
  case $choice in
    1) install_bot ;;
    2) configure_bot ; systemctl restart "$SERVICE_NAME"; echo "Bot restarted with new token."; read -p "Press Enter...";;
    3) update_bot ;;
    4) uninstall_bot ;;
    0) exit ;;
    *) echo "Invalid choice. Please try again." ; sleep 2 ;;
  esac
done
