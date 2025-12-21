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

# =========================
# Google Sheets (Sheets API ONLY)
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=SCOPES,
)

gc = gspread.authorize(creds)

# 🔴 IMPORTANT: open_by_key (NO DRIVE API)
sheet = gc.open_by_key(
    os.environ["SHEET_ID"]
).worksheet(os.environ["SHEET_TAB"])

HEADERS = sheet.row_values(1)

def col_index(name):
    return HEADERS.index(name) + 1

# =========================
# Data helpers
# =========================

def get_clients():
    return sorted({
        r["Client"].strip()
        for r in sheet.get_all_records()
        if r.get("Client")
    })

def get_projects(client):
    return sorted({
        r["Project"]
        for r in sheet.get_all_records()
        if r.get("Client") == client
    })

def get_items(client, project):
    return sorted({
        r.get("Item Description", "")
        for r in sheet.get_all_records()
        if r.get("Client") == client and r.get("Project") == project
    })

PROCESSES = [
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

def find_row(client, project, item):
    rows = sheet.get_all_records()
    for i, r in enumerate(rows, start=2):
        if (
            r.get("Client") == client
            and r.get("Project") == project
            and r.get("Item Description") == item
        ):
            return i
    return None

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found.")
        return

    keyboard = [[InlineKeyboardButton(c, callback_data=f"client|{c}")]
                for c in clients]

    await update.message.reply_text(
        "📋 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|")

    if data[0] == "client":
        context.user_data["client"] = data[1]
        projects = get_projects(data[1])

        keyboard = [[InlineKeyboardButton(p, callback_data=f"project|{p}")]
                    for p in projects]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back")])

        await q.edit_message_text(
            "📁 Select Project:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data[0] == "project":
        context.user_data["project"] = data[1]
        items = get_items(context.user_data["client"], data[1])

        keyboard = [[InlineKeyboardButton(i, callback_data=f"item|{i}")]
                    for i in items]

        await q.edit_message_text(
            "📦 Select Item Description:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data[0] == "item":
        context.user_data["item"] = data[1]

        keyboard = [[InlineKeyboardButton(p, callback_data=f"proc|{p}")]
                    for p in PROCESSES]

        await q.edit_message_text(
            "⚙ Select Process:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data[0] == "proc":
        context.user_data["process"] = data[1]
        context.user_data["await_qty"] = True

        await q.edit_message_text(
            "✏️ Enter quantity to add (example: +2 or 5)"
        )

async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_qty"):
        return

    try:
        qty = int(update.message.text.replace("+", ""))
    except ValueError:
        await update.message.reply_text("❌ Enter a number like +2 or 5")
        return

    row = find_row(
        context.user_data["client"],
        context.user_data["project"],
        context.user_data["item"],
    )

    col = col_index(context.user_data["process"])
    current = sheet.cell(row, col).value
    current = int(current) if current else 0

    sheet.update_cell(row, col, current + qty)

    await update.message.reply_text(
        f"✅ Updated\n\n"
        f"Client: {context.user_data['client']}\n"
        f"Project: {context.user_data['project']}\n"
        f"Item: {context.user_data['item']}\n"
        f"Process: {context.user_data['process']}\n"
        f"Added: {qty}"
    )

    context.user_data.clear()

# =========================
# Webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    app = request.app["telegram_app"]
    update = Update.de_json(await request.json(), app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# =========================
# Main
# =========================

async def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_input))

    await app.initialize()
    await app.start()

    web_app = web.Application()
    web_app["telegram_app"] = app
    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{os.environ['TELEGRAM_TOKEN']}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()

    await app.bot.set_webhook(
        f"{os.environ['RENDER_EXTERNAL_URL']}/{os.environ['TELEGRAM_TOKEN']}"
    )

    print("🚀 Bot running (NO Drive API)")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
