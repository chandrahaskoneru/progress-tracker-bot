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
    "https://www.googleapis.com/auth/drive.readonly",
]

creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)

gc = gspread.authorize(creds)

sheet = gc.open_by_key(
    os.environ["SHEET_ID"]
).worksheet(os.environ["SHEET_TAB"])

# =========================
# Sheet helpers (SAFE)
# =========================

def get_clients():
    # Column A = Client (skip header)
    col = sheet.col_values(1)[1:]
    return sorted({c.strip() for c in col if c.strip()})

def get_projects(client):
    rows = sheet.get_all_values()[1:]
    projects = set()
    for r in rows:
        if len(r) >= 2 and r[0].strip() == client:
            if r[1].strip():
                projects.add(r[1].strip())
    return sorted(projects)

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
            await query.edit_message_text(
                f"❌ No projects found for {client}"
            )
            return

        keyboard = [
            [InlineKeyboardButton(p, callback_data=f"project|{client}|{p}")]
            for p in projects
        ]
        keyboard.append(
            [InlineKeyboardButton("⬅ Back", callback_data="back")]
        )

        await query.edit_message_text(
            f"Client: {client}\n\nSelect Project:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif parts[0] == "project":
        await query.edit_message_text(
            f"✅ Selected\n\nClient: {parts[1]}\nProject: {parts[2]}"
        )

    elif parts[0] == "back":
        await start(update, context)

# =========================
# Webhook handlers
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

    print("✅ Bot running with buttons + Google Sheets")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
