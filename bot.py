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
    CallbackQueryHandler,
    ContextTypes,
)

# =========================
# Google Sheets setup
# =========================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)
sheet = gc.open(os.environ["SHEET_NAME"]).worksheet(os.environ["SHEET_TAB"])

# =========================
# Sheet helpers
# =========================

def get_clients():
    # Column A = Client
    values = sheet.col_values(1)[1:]  # skip header
    return sorted({v.strip() for v in values if v.strip()})

def get_projects(client):
    records = sheet.get_all_records()
    return sorted({
        r["Project"]
        for r in records
        if r.get("Client") == client and r.get("Project")
    })

# =========================
# Telegram handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [
        [InlineKeyboardButton(c, callback_data=f"client|{c}")]
        for c in clients
    ]

    await update.message.reply_text(
        "📋 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")

    if parts[0] == "client":
        client = parts[1]
        projects = get_projects(client)

        if not projects:
            await query.edit_message_text("❌ No projects found.")
            return

        keyboard = [
            [InlineKeyboardButton(p, callback_data=f"project|{client}|{p}")]
            for p in projects
        ]
        keyboard.append(
            [InlineKeyboardButton("⬅ Back", callback_data="back")]
        )

        await query.edit_message_text(
            f"Client: {client}\nSelect Project:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif parts[0] == "project":
        client, project = parts[1], parts[2]

        await query.edit_message_text(
            f"✅ Selected\nClient: {client}\nProject: {project}"
        )

    elif parts[0] == "back":
        await start(update, context)

# =========================
# Webhook (aiohttp)
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    telegram_app: Application = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
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
    telegram_app.add_handler(CallbackQueryHandler(handle_buttons))

    await telegram_app.initialize()
    await telegram_app.start()

    web_app = web.Application()
    web_app["telegram_app"] = telegram_app

    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await telegram_app.bot.set_webhook(f"{BASE_URL}/{TOKEN}")

    print("✅ Bot running with client & project buttons")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
