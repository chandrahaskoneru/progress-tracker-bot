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

def get_records():
    return summary.get_all_records()

def get_clients():
    return sorted({r["Client"].strip() for r in get_records() if r.get("Client")})

def get_projects(client):
    return sorted({
        r["Project"].strip()
        for r in get_records()
        if norm(r.get("Client")) == norm(client) and r.get("Project")
    })

def get_items(client, project):
    return sorted({
        r["Item Description"].strip()
        for r in get_records()
        if norm(r.get("Client")) == norm(client)
        and norm(r.get("Project")) == norm(project)
        and r.get("Item Description")
    })

def get_process_columns():
    ignore = ("plan", "tasks", "completed", "status")
    processes = []
    for h in HEADERS:
        if not h:
            continue
        hl = h.lower()
        if h in ("Client", "Project", "Item Description"):
            continue
        if any(x in hl for x in ignore):
            continue
        processes.append(h)
    return processes

def find_row(client, project, item):
    for i, r in enumerate(get_records(), start=2):
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
# Telegram Flow
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found.")
        return

    kb = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]
    await update.message.reply_text(
        "📁 Select Client:",
        reply_markup=InlineKeyboardMarkup(kb),
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
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif data[0] == "project":
        context.user_data["project"] = data[1]
        items = get_items(context.user_data["client"], data[1])

        kb = [[InlineKeyboardButton(i, callback_data=f"item|{i}")] for i in items]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

        await q.edit_message_text(
            f"Project: {data[1]}\n📦 Select Item:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif data[0] == "item":
        context.user_data["item"] = data[1]
        processes = get_process_columns()

        kb = [[InlineKeyboardButton(p, callback_data=f"proc|{p}")] for p in processes]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_items")])

        await q.edit_message_text(
            f"Item: {data[1]}\n⚙ Select Process:",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif data[0] == "proc":
        context.user_data["process"] = data[1]
        await q.edit_message_text(
            f"✏ Enter quantity for **{data[1]}**:"
        )

    elif data[0] == "back_clients":
        await start(update, context)

    elif data[0] == "back_projects":
        await start(update, context)

    elif data[0] == "back_items":
        client = context.user_data["client"]
        await buttons(
            Update(update.update_id, callback_query=q._replace(data=f"client|{client}")),
            context,
        )

async def quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    current_val = summary.cell(row, col).value
    current = int(current_val) if current_val and str(current_val).isdigit() else 0

    new_val = current + qty
    summary.update_cell(row, col, new_val)

    # ===== LOG ENTRY =====
    logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        update.effective_user.username or "",
        client,
        project,
        item,
    ])

    await update.message.reply_text(
        f"✅ Updated\n{process}: {current} → {new_val}"
    )

    context.user_data.pop("process")

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity))

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

    print("✅ Progress Tracker Bot RUNNING")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
