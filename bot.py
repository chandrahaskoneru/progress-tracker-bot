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
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=scopes
    )
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


def log_to_sheet(username: str, client: str, project: str, description: str):
    """
    Append a single row to the sheet.

    Sheet columns:
      Timestamp | Username | Client | Project | Description
    """
    ws = get_worksheet()

    # India time (Asia/Kolkata = UTC+5:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    ws.append_row(
        [ts, username or "", client, project, description],
        value_input_option="USER_ENTERED",
    )


# ---------- Telegram handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Hi! I’m your progress tracking bot.\n\n"
        "Use the /log command to save completed tasks to Google Sheets.\n\n"
        "Format:\n"
        "  /log client | project | description\n\n"
        "Example:\n"
        "  /log Client A | Website Revamp | Homepage UI done"
    )
    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ️ Available commands:\n"
        "/start – Welcome message.\n"
        "/help – This help message.\n"
        "/log client | project | description – Save a completed task.\n\n"
        "Example:\n"
        "/log Client A | Website Revamp | Homepage UI done"
    )
    await update.message.reply_text(msg)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /log client | project | description

    Example:
      /log Client A | Website Revamp | Homepage UI done
    """
    if not context.args:
        await update.message.reply_text(
            "Please use:\n"
            "/log client | project | description\n\n"
            "Example:\n"
            "/log Client A | Website Revamp | Homepage UI done"
        )
        return

    # Rebuild full text after /log, then split by '|'
    full_text = " ".join(context.args)
    parts = [p.strip() for p in full_text.split("|")]

    if len(parts) < 3:
        await update.message.reply_text(
            "I need 3 parts separated by '|':\n"
            "client | project | description\n\n"
            "Example:\n"
            "/log Client A | Website Revamp | Homepage UI done"
        )
        return

    client, project, description = parts[0], parts[1], "|".join(parts[2:]).strip()

    user = update.effective_user
    username = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()

    try:
        log_to_sheet(username, client, project, description)
    except Exception as e:
        print("Error logging to sheet:", e)
        await update.message.reply_text(
            "❌ I couldn't write to Google Sheets. Please check configuration on the server."
        )
        return

    await update.message.reply_text(
        f"✅ Logged task for client '{client}', project '{project}'."
    )


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
