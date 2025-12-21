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
    MessageHandler,
    ContextTypes,
    filters,
)

# ==================================================
# Google Sheets setup (ENV BASED – Render SAFE)
# ==================================================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

service_account_info = json.loads(
    os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
)

creds = Credentials.from_service_account_info(
    service_account_info,
    scopes=SCOPES,
)

gc = gspread.authorize(creds)

SHEET_NAME = os.environ["SHEET_NAME"]
SHEET_TAB = os.environ["SHEET_TAB"]

sheet = gc.open(SHEET_NAME).worksheet(SHEET_TAB)

# ==================================================
# Helpers
# ==================================================

def get_clients():
    values = sheet.get_all_values()
    clients = set()
    for row in values[1:]:
        if row and row[0].strip():
            clients.add(row[0].strip())
    return sorted(clients)

def get_projects(client):
    values = sheet.get_all_values()
    projects = []
    for row in values[1:]:
        if len(row) >= 2 and row[0].strip() == client and row[1].strip():
            projects.append(row[1].strip())
    return sorted(set(projects))

# ==================================================
# Telegram Handlers
# ==================================================

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
        "👋 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("|")

    # ======================
    # Client selected
    # ======================
    if data[0] == "client":
        client = data[1]
        projects = get_projects(client)

        keyboard = [
            [InlineKeyboardButton(p, callback_data=f"project|{client}|{p}")]
            for p in projects
        ]
        keyboard.append(
            [InlineKeyboardButton("⬅ Back", callback_data="home")]
        )

        await query.edit_message_text(
            f"📂 Client: *{client}*\nSelect Project:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ======================
    # Project selected
    # ======================
    elif data[0] == "project":
        context.user_data.clear()
        context.user_data["client"] = data[1]
        context.user_data["project"] = data[2]
        context.user_data["awaiting_qty"] = True

        await query.edit_message_text(
            f"🏗 *{data[2]}*\n\n"
            "Send quantity like:\n"
            "`+5` or `-2`",
            parse_mode="Markdown",
        )

    # ======================
    # Back to home
    # ======================
    elif data[0] == "home":
        await start(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_qty"):
        return

    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Please send a number like +5 or -2")
        return

    client = context.user_data["client"]
    project = context.user_data["project"]

    # 👉 Here you will later update sheet quantities
    # For now, we confirm input (safe & stable)

    await update.message.reply_text(
        f"✅ Quantity `{qty}` received for:\n"
        f"*Client:* {client}\n"
        f"*Project:* {project}",
        parse_mode="Markdown",
    )

    context.user_data.clear()

# ==================================================
# Aiohttp Webhook
# ==================================================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    tg_app: Application = request.app["tg_app"]
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return web.Response(text="OK")

# ==================================================
# Main
# ==================================================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    tg_app = Application.builder().token(TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CallbackQueryHandler(callbacks))
    tg_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    await tg_app.initialize()
    await tg_app.start()

    web_app = web.Application()
    web_app["tg_app"] = tg_app

    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await tg_app.bot.set_webhook(f"{BASE_URL}/{TOKEN}")

    print("🚀 Bot running correctly (Webhook + Buttons + Sheets)")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
