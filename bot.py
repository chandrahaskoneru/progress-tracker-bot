import os
import json
import asyncio
from datetime import datetime
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
# Google Sheets (Sheets API only)
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=SCOPES
)

gc = gspread.authorize(creds)

summary = gc.open_by_key(os.environ["SHEET_ID"]).worksheet(os.environ["SHEET_TAB"])
logs = gc.open(os.environ["SHEET_NAME"]).worksheet("Logs")

HEADERS = summary.row_values(1)

# Process columns = columns without "Plan" and not meta columns
EXCLUDE = {"Client", "Project", "Item Description", "Tasks", "Completed", "Status (%)"}
PROCESS_COLUMNS = [
    h for h in HEADERS
    if h and "Plan" not in h and h not in EXCLUDE
]

# =========================
# Helpers
# =========================

def safe_int(val):
    try:
        return int(val)
    except:
        return 0

def get_clients():
    return sorted(set(summary.col_values(1)[1:]))

def get_projects(client):
    return sorted(set(
        r["Project"]
        for r in summary.get_all_records()
        if r["Client"] == client
    ))

def get_items(client, project):
    return sorted(set(
        r["Item Description"]
        for r in summary.get_all_records()
        if r["Client"] == client and r["Project"] == project
    ))

def find_row(client, project, item):
    rows = summary.get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if r[0] == client and r[1] == project and r[2] == item:
            return i
    return None

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(c, callback_data=f"client|{c}")]
        for c in get_clients()
    ]
    await update.message.reply_text(
        "📁 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|")

    if data[0] == "client":
        context.user_data["client"] = data[1]
        projects = get_projects(data[1])
        kb = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])
        await q.edit_message_text(
            f"Client: {data[1]}\n📂 Select Project:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "project":
        context.user_data["project"] = data[1]
        items = get_items(context.user_data["client"], data[1])
        kb = [[InlineKeyboardButton(i, callback_data=f"item|{i}")] for i in items]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])
        await q.edit_message_text(
            f"Project: {data[1]}\n📦 Select Item:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "item":
        context.user_data["item"] = data[1]
        kb = [[InlineKeyboardButton(p, callback_data=f"process|{p}")] for p in PROCESS_COLUMNS]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_items")])
        await q.edit_message_text(
            f"Item: {data[1]}\n⚙ Select Process:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "process":
        context.user_data["process"] = data[1]
        await q.edit_message_text(
            f"✏ Enter quantity for *{data[1]}*:",
            parse_mode="Markdown"
        )

    elif data[0].startswith("back"):
        await start(update, context)

async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "process" not in context.user_data:
        return

    qty = safe_int(update.message.text)
    client = context.user_data["client"]
    project = context.user_data["project"]
    item = context.user_data["item"]
    process = context.user_data["process"]

    row = find_row(client, project, item)
    col = HEADERS.index(process) + 1

    current = summary.cell(row, col).value
    new_value = safe_int(current) + qty
    summary.update_cell(row, col, new_value)

    # Username fallback FIX
    user = update.effective_user
    username = (
        f"@{user.username}" if user.username
        else user.full_name if user.full_name
        else str(user.id)
    )

    logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        username,
        client,
        project,
        item
    ])

    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Added {qty} to {process}\n\nUse /start for next entry"
    )

# =========================
# Webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    app: Application = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
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
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()

    await app.bot.set_webhook(
        f"{os.environ['RENDER_EXTERNAL_URL']}/{os.environ['TELEGRAM_TOKEN']}"
    )

    print("✅ Bot fully running")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
