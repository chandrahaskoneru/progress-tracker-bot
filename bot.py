import os
import json
import asyncio
from aiohttp import web
from datetime import datetime

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

# ======================================================
# Google Sheets setup (Sheets API ONLY – no Drive API)
# ======================================================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)

sheet = gc.open_by_key(os.environ["SHEET_ID"]).worksheet(os.environ["SHEET_TAB"])

# ======================================================
# Helpers
# ======================================================

def get_process_columns():
    """
    Process columns = after Item Description
    AND not ending with 'Plan'
    AND not Tasks / Completed / Status (%)
    """
    headers = sheet.row_values(1)
    process_cols = {}

    for idx, name in enumerate(headers, start=1):
        if not name:
            continue

        name = name.strip()

        if name.endswith("Plan"):
            continue
        if name in ("Client", "Project", "Item Description", "Tasks", "Completed", "Status (%)"):
            continue

        process_cols[name] = idx

    return process_cols


def get_clients():
    return sorted(set(
        v.strip() for v in sheet.col_values(1)[1:] if v.strip()
    ))


def get_projects(client):
    records = sheet.get_all_records()
    return sorted(set(
        r["Project"] for r in records
        if r.get("Client") == client and r.get("Project")
    ))


def get_items(client, project):
    records = sheet.get_all_records()
    return sorted(set(
        r["Item Description"] for r in records
        if r.get("Client") == client
        and r.get("Project") == project
        and r.get("Item Description")
    ))


def find_row(client, project, item):
    records = sheet.get_all_records()
    for idx, r in enumerate(records, start=2):
        if (
            r.get("Client") == client
            and r.get("Project") == project
            and r.get("Item Description") == item
        ):
            return idx
    return None


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ======================================================
# Telegram Handlers
# ======================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    clients = get_clients()
    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]
    await update.message.reply_text(
        "📁 Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("|")

    # ---------------- Client ----------------
    if data[0] == "client":
        client = data[1]
        context.user_data["client"] = client

        projects = get_projects(client)
        keyboard = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_start")])

        await query.edit_message_text(
            f"Client: {client}\n📂 Select Project:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ---------------- Project ----------------
    elif data[0] == "project":
        project = data[1]
        context.user_data["project"] = project

        items = get_items(context.user_data["client"], project)
        keyboard = [[InlineKeyboardButton(i, callback_data=f"item|{i}")] for i in items]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_client")])

        await query.edit_message_text(
            f"Project: {project}\n📦 Select Item Description:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ---------------- Item ----------------
    elif data[0] == "item":
        item = data[1]
        context.user_data["item"] = item

        process_cols = get_process_columns()
        keyboard = [[InlineKeyboardButton(p, callback_data=f"process|{p}")] for p in process_cols]
        keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_project")])

        await query.edit_message_text(
            f"Item: {item}\n⚙ Select Process:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ---------------- Process ----------------
    elif data[0] == "process":
        process = data[1]
        context.user_data["process"] = process

        await query.edit_message_text(
            f"✏ Enter quantity to add for:\n\n"
            f"Client: {context.user_data['client']}\n"
            f"Project: {context.user_data['project']}\n"
            f"Item: {context.user_data['item']}\n"
            f"Process: {process}"
        )

    # ---------------- Back buttons ----------------
    elif data[0] == "back_start":
        await start(update, context)

    elif data[0] == "back_client":
        await buttons(update, context._replace(callback_query=update.callback_query._replace(data=f"client|{context.user_data['client']}")))

    elif data[0] == "back_project":
        await buttons(update, context._replace(callback_query=update.callback_query._replace(data=f"project|{context.user_data['project']}")))


async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "process" not in context.user_data:
        return

    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number.")
        return

    client = context.user_data["client"]
    project = context.user_data["project"]
    item = context.user_data["item"]
    process = context.user_data["process"]

    row = find_row(client, project, item)
    if not row:
        await update.message.reply_text("❌ Row not found.")
        return

    process_cols = get_process_columns()
    col = process_cols[process]

    current = sheet.cell(row, col).value
    new_value = safe_int(current) + qty
    sheet.update_cell(row, col, new_value)

    await update.message.reply_text(
        f"✅ Updated successfully!\n\n"
        f"{process}: {new_value}"
    )

    context.user_data.clear()


# ======================================================
# Webhook (aiohttp)
# ======================================================

async def health(request):
    return web.Response(text="OK")


async def telegram_webhook(request):
    telegram_app: Application = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return web.Response(text="OK")


# ======================================================
# Main
# ======================================================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(buttons))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_input))

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

    print("🚀 Bot fully running (clients → projects → items → process → qty)")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
