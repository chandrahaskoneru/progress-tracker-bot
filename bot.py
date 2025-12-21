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
# Google Sheets (Sheets API ONLY)
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=SCOPES,
)

gc = gspread.authorize(creds)

spreadsheet = gc.open_by_key(os.environ["SHEET_ID"])
summary = spreadsheet.worksheet(os.environ.get("SHEET_TAB", "Summary"))
logs = spreadsheet.worksheet("Logs")

HEADERS = summary.row_values(1)

# =========================
# Helpers
# =========================

def norm(v):
    return str(v).strip().lower()

def safe_int(v):
    try:
        return int(v)
    except:
        return 0

def get_clients():
    return sorted({
        r["Client"].strip()
        for r in summary.get_all_records()
        if r.get("Client")
    })

def get_projects(client):
    return sorted({
        r["Project"].strip()
        for r in summary.get_all_records()
        if norm(r.get("Client")) == norm(client) and r.get("Project")
    })

def get_items(client, project):
    return sorted({
        r["Item Description"].strip()
        for r in summary.get_all_records()
        if norm(r.get("Client")) == norm(client)
        and norm(r.get("Project")) == norm(project)
        and r.get("Item Description")
    })

def get_process_columns():
    ignore = ("plan", "tasks", "completed", "status")
    return [
        h for h in HEADERS
        if h and h not in ("Client", "Project", "Item Description")
        and not any(x in h.lower() for x in ignore)
    ]

def find_row(client, project, item):
    for i, r in enumerate(summary.get_all_records(), start=2):
        if (
            norm(r.get("Client")) == norm(client)
            and norm(r.get("Project")) == norm(project)
            and norm(r.get("Item Description")) == norm(item)
        ):
            return i
    return None

def col_index(col):
    return HEADERS.index(col) + 1

# =========================
# UI Screens (SAFE)
# =========================

async def show_clients(update, context, edit=False):
    kb = [[InlineKeyboardButton(c, callback_data=f"client|{c}")]
          for c in get_clients()]

    if edit:
        await update.effective_message.edit_text(
            "📁 Select Client:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "📁 Select Client:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

async def show_projects(update, context):
    client = context.user_data["client"]
    kb = [[InlineKeyboardButton(p, callback_data=f"project|{p}")]
          for p in get_projects(client)]
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

    await update.effective_message.edit_text(
        f"Client: {client}\n📂 Select Project:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_items(update, context):
    client = context.user_data["client"]
    project = context.user_data["project"]
    kb = [[InlineKeyboardButton(i, callback_data=f"item|{i}")]
          for i in get_items(client, project)]
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

    await update.effective_message.edit_text(
        f"Project: {project}\n📦 Select Item:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_processes(update, context):
    kb = [[InlineKeyboardButton(p, callback_data=f"proc|{p}")]
          for p in get_process_columns()]
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_items")])

    await update.effective_message.edit_text(
        "⚙ Select Process:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def ask_quantity(update, context):
    kb = [[InlineKeyboardButton("⬅ Back", callback_data="back_processes")]]
    await update.effective_message.edit_text(
        f"✏ Enter quantity for {context.user_data['process']}:",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await show_clients(update, context, edit=False)

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("client|"):
        context.user_data["client"] = data.split("|", 1)[1]
        await show_projects(update, context)

    elif data.startswith("project|"):
        context.user_data["project"] = data.split("|", 1)[1]
        await show_items(update, context)

    elif data.startswith("item|"):
        context.user_data["item"] = data.split("|", 1)[1]
        await show_processes(update, context)

    elif data.startswith("proc|"):
        context.user_data["process"] = data.split("|", 1)[1]
        await ask_quantity(update, context)

    elif data == "back_clients":
        await show_clients(update, context, edit=True)

    elif data == "back_projects":
        await show_projects(update, context)

    elif data == "back_items":
        await show_items(update, context)

    elif data == "back_processes":
        await show_processes(update, context)

async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "process" not in context.user_data:
        return

    try:
        qty = int(update.message.text.replace("+", ""))
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number")
        return

    client = context.user_data["client"]
    project = context.user_data["project"]
    item = context.user_data["item"]
    process = context.user_data["process"]

    row = find_row(client, project, item)
    col = col_index(process)

    current = safe_int(summary.cell(row, col).value)
    summary.update_cell(row, col, current + qty)

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
        item,
    ])

    await update.message.reply_text("✅ Quantity updated & logged")
    await show_clients(update, context, edit=False)

# =========================
# Webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def webhook(request):
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
    web_app.router.add_post(f"/{os.environ['TELEGRAM_TOKEN']}", webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(
        runner, "0.0.0.0", int(os.environ.get("PORT", 10000))
    ).start()

    await app.bot.set_webhook(
        f"{os.environ['RENDER_EXTERNAL_URL']}/{os.environ['TELEGRAM_TOKEN']}"
    )

    print("✅ Bot running (edit/reply safe)")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
