import os
import json
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ---------- Google Sheets helper ----------

def get_gsheet_client():
    """
    Build a gspread client from the JSON stored in the env var
    GOOGLE_SERVICE_ACCOUNT_JSON.
    """
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is missing")

    info = json.loads(creds_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(credentials)
    return gc


def get_worksheet():
    """
    Returns the worksheet to log to.
    SHEET_NAME: Google Sheet name (not the tab name).
    SHEET_TAB: Tab name inside the sheet.
    """
    sheet_name = os.environ.get("SHEET_NAME", "ProgressLog")
    sheet_tab = os.environ.get("SHEET_TAB", "Logs")

    gc = get_gsheet_client()
    sh = gc.open(sheet_name)
    ws = sh.worksheet(sheet_tab)
    return ws


def log_to_sheet(username: str, user_id: int, text: str):
    """
    Append a single row to the sheet.
    """
    ws = get_worksheet()

    # India time (Asia/Kolkata = UTC+5:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    ws.append_row([ts, username or "", str(user_id), text], value_input_option="USER_ENTERED")


# ---------- Telegram handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Hi! I’m your progress tracking bot.\n\n"
        "Use the /log command to save your progress to Google Sheets.\n"
        "Example:\n"
        "  /log did 20 pushups\n\n"
        "I’ll store timestamp, your username, and the text you send."
    )
    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ️ Available commands:\n"
        "/start – Welcome message.\n"
        "/help – This help message.\n"
        "/log <text> – Save a progress entry.\n\n"
        "Example: /log read 10 pages of a book"
    )
    await update.message.reply_text(msg)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please add some text, e.g.\n/log did 20 pushups")
        return

    text = " ".join(context.args)

    user = update.effective_user
    username = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()
    user_id = user.id

    try:
        log_to_sheet(username, user_id, text)
    except Exception as e:
        print("Error logging to sheet:", e)
        await update.message.reply_text(
            "❌ I couldn't write to Google Sheets. Please check configuration on the server."
        )
        return

    await update.message.reply_text("✅ Logged to Google Sheets!")


# ---------- Main entry ----------

def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN env var is missing")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("log", log_cmd))

    # Detect environment: local (no RENDER_EXTERNAL_URL) => polling
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    port = int(os.environ.get("PORT", "8000"))

    if render_url:
        # Running on Render → use webhook
        webhook_url = f"{render_url}/{token}"

        print(f"Starting webhook. Public URL: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=webhook_url,
        )
    else:
        # Local development: long polling
        print("RENDER_EXTERNAL_URL not set. Using polling.")
        application.run_polling()


if __name__ == "__main__":
    main()
