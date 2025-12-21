import os
import asyncio
from aiohttp import web

import gspread
from google.oauth2.service_account import Credentials

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# Google Sheets
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(
    "credentials.json", scopes=SCOPES
)
gc = gspread.authorize(creds)

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
sheet = gc.open_by_key(SHEET_ID).worksheet("Summary")

# =========================
# Helpers
# =========================

def get_clients():
    return sorted(set(c for c in sheet.col_values(1)[1:] if c))

def get_projects(client):
    rows = sheet.get_all_values()[1:]
    return [r[1] for r in rows if r[0] == client and r[1]]

# =========================
# Telegram handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clients = get_clients()
    keyboard = [[InlineKeyboardButton(c, callback_data=f"client|{c}")]
                for c in clients]

    await update.message.reply_text(
        "👋 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")

    # Client selected
    if data[0] == "client":
        client = data[1]
        keyboard = [
            [InlineKeyboardButton(p, callback_data=f"project|{client}|{p}")]
            for p in get_projects(client)
        ]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="home")])

        await query.edit_message_text(
            f"📂 Client: *{client}*\nSelect Project:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # Project selected
    elif data[0] == "project":
        context.user_data["client"] = data[1]
        context.user_data["project"] = data[2]
        context.user_data["awaiting_qty"] = True

        await query.edit_message_text(
            f"🏗 *{data[2]}*\nSend quantity like `+5` or `-2`",
            parse_mode="Markdown",
        )

    # Back to home
    elif data[0] == "home":
        await start(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_qty"):
        return

    try:
        qty = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Enter a number only")
        return

    client = context.user_data["client"]
    project = context.user_data["project"]

    await update.message.reply_text(
        f"✅ `{qty}` recorded for *{project}*",
        parse_mode="Markdown",
    )

    context.user_data.clear()

# =========================
# Webhook / aiohttp
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    tg_app = request.app["tg_app"]
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return web.Response(text="OK")

# =========================
# Main
# =========================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    tg_app = Application.builder().token(TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CallbackQueryHandler(callbacks))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

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
    print("🚀 Bot running correctly (buttons + webhook)")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
