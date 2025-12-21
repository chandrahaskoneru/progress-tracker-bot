import os
import json
import asyncio
from aiohttp import web

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

import gspread
from google.oauth2.service_account import Credentials


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


def get_rows():
    rows = sheet.get_all_values()[1:]  # skip header
    cleaned = []
    for r in rows:
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            cleaned.append([c.strip() for c in r])
    return cleaned


# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_rows()
    clients = sorted({r[0] for r in rows})

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]
    await update.message.reply_text(
        "👋 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def client_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    client = query.data.split("|", 1)[1]
    context.user_data["client"] = client

    rows = get_rows()
    projects = sorted({r[1] for r in rows if r[0] == client})

    keyboard = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

    await query.edit_message_text(
        f"📁 Client: {client}\nSelect Project:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    project = query.data.split("|", 1)[1]
    context.user_data["project"] = project

    processes = [
        "Raw Material",
        "Rough Turning",
        "Heat treatment",
        "Final Machining",
        "Keyway",
        "GC",
        "Spline",
        "CG",
        "SG",
        "IH",
        "GG",
    ]

    keyboard = [
        [InlineKeyboardButton(p, callback_data=f"process|{p}")]
        for p in processes
    ]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

    await query.edit_message_text(
        f"📂 Project: {project}\nSelect Process:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def process_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    process = query.data.split("|", 1)[1]
    client = context.user_data["client"]
    project = context.user_data["project"]

    await query.edit_message_text(
        f"✅ Logged\n\nClient: {client}\nProject: {project}\nProcess: {process}"
    )


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_clients":
        await start(update, context)

    elif query.data == "back_projects":
        client = context.user_data.get("client")
        rows = get_rows()
        projects = sorted({r[1] for r in rows if r[0] == client})

        keyboard = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

        await query.edit_message_text(
            f"📁 Client: {client}\nSelect Project:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# =========================
# Aiohttp (Webhook)
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
    telegram_app.add_handler(CallbackQueryHandler(client_selected, pattern="^client\\|"))
    telegram_app.add_handler(CallbackQueryHandler(project_selected, pattern="^project\\|"))
    telegram_app.add_handler(CallbackQueryHandler(process_selected, pattern="^process\\|"))
    telegram_app.add_handler(CallbackQueryHandler(back_handler, pattern="^back_"))

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

    print("🚀 Bot running (Webhook + Google Sheets + Buttons)")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
