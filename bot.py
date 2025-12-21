import os
import json
import asyncio
from aiohttp import web

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# Google Sheets Setup
# =========================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

sheet = gc.open(os.environ["SHEET_NAME"]).worksheet(os.environ["SHEET_TAB"])

# =========================
# Helpers
# =========================

def get_clients():
    """
    Reads column A (Client) and returns unique non-empty clients
    """
    values = sheet.col_values(1)[1:]  # skip header
    clients = sorted({v.strip() for v in values if v.strip()})
    return clients

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [
        [InlineKeyboardButton(client, callback_data=f"client:{client}")]
        for client in clients
    ]

    await update.message.reply_text(
        "✅ Select a client:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is running.\nWebhook + aiohttp active.")

# =========================
# Webhook Handler
# =========================

async def telegram_webhook(request):
    telegram_app: Application = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return web.Response(text="OK")

async def health(request):
    return web.Response(text="OK")

# =========================
# Main
# =========================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("status", status))

    await telegram_app.initialize()
    await telegram_app.start()

    # aiohttp server
    web_app = web.Application()
    web_app["telegram_app"] = telegram_app

    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    # Set webhook
    await telegram_app.bot.set_webhook(f"{BASE_URL}/{TOKEN}")

    print("🚀 Bot is running (Webhook + Sheets + Buttons)")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
