import os
import json
import asyncio
from aiohttp import web

import gspread
from google.oauth2.service_account import Credentials

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Google Sheets
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=SCOPES,
)

gc = gspread.authorize(creds)
sheet = gc.open(os.environ["SHEET_NAME"]).worksheet(os.environ["SHEET_TAB"])

# =========================
# Helpers
# =========================

def get_clients():
    rows = sheet.get_all_values()[1:]  # skip header
    clients = []

    for r in rows:
        if len(r) >= 2:
            client = r[0].strip()
            project = r[1].strip()
            if client and project:
                clients.append(client)

    return sorted(set(clients))

def get_projects(client):
    rows = sheet.get_all_values()[1:]
    projects = []

    for r in rows:
        if len(r) >= 2:
            if r[0].strip() == client and r[1].strip():
                projects.append(r[1].strip())

    return sorted(set(projects))

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [[c] for c in clients]
    keyboard.append(["❌ Cancel"])

    await update.message.reply_text(
        "👋 Select Client:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True
        ),
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ Cancel":
        context.user_data.clear()
        await update.message.reply_text("Cancelled.")
        return

    clients = get_clients()

    # Client selected
    if text in clients:
        context.user_data["client"] = text
        projects = get_projects(text)

        keyboard = [[p] for p in projects]
        keyboard.append(["⬅ Back"])

        await update.message.reply_text(
            f"📁 Client: {text}\nSelect Project:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard, resize_keyboard=True
            ),
        )
        return

    # Back
    if text == "⬅ Back":
        await start(update, context)
        return

    # Project selected
    if "client" in context.user_data:
        await update.message.reply_text(
            f"✅ Selected\n\n"
            f"Client: {context.user_data['client']}\n"
            f"Project: {text}\n\n"
            f"(Next step: quantity buttons)"
        )
    else:
        await update.message.reply_text("Use /start")

# =========================
# Webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    tg_app: Application = request.app["telegram_app"]
    update = Update.de_json(await request.json(), tg_app.bot)
    await tg_app.process_update(update)
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
    telegram_app.add_handler(MessageHandler(filters.TEXT, text_handler))

    await telegram_app.initialize()
    await telegram_app.start()

    web_app = web.Application()
    web_app["telegram_app"] = telegram_app
    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await telegram_app.bot.set_webhook(f"{BASE_URL}/{TOKEN}")

    print("🚀 Bot running (Webhook + Buttons)")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
