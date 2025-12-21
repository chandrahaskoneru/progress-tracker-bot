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
# Google Sheets setup
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds = Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=SCOPES,
)

gc = gspread.authorize(creds)

spreadsheet = gc.open_by_key(os.environ["SHEET_ID"])
summary = spreadsheet.worksheet("Summary")
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

def col_index(col):
    return HEADERS.index(col) + 1

def is_plan(col):
    return col.lower().endswith("plan")

def actual_columns():
    return [
        h for h in HEADERS
        if h not in ("Client", "Project", "Item Description", "Tasks", "Completed", "Status (%)")
        and not is_plan(h)
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

def find_last_filled_process(row):
    for col in reversed(actual_columns()):
        if safe_int(summary.cell(row, col_index(col)).value) > 0:
            return col
    return None

# =========================
# UI Screens
# =========================

async def show_clients(update, edit=False):
    clients = sorted({
        r["Client"] for r in summary.get_all_records() if r.get("Client")
    })
    kb = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]

    if edit:
        await update.effective_message.edit_text(
            "📁 Select Client",
            reply_markup=InlineKeyboardMarkup(kb),
        )
    else:
        await update.message.reply_text(
            "📁 Select Client",
            reply_markup=InlineKeyboardMarkup(kb),
        )

async def show_projects(update, context):
    client = context.user_data["client"]
    projects = sorted({
        r["Project"] for r in summary.get_all_records()
        if norm(r.get("Client")) == norm(client)
    })

    kb = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

    await update.effective_message.edit_text(
        f"📂 Client: {client}",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_items(update, context):
    client = context.user_data["client"]
    project = context.user_data["project"]

    items = sorted({
        r["Item Description"] for r in summary.get_all_records()
        if norm(r.get("Client")) == norm(client)
        and norm(r.get("Project")) == norm(project)
    })

    kb = [[InlineKeyboardButton(i, callback_data=f"item|{i}")] for i in items]
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

    await update.effective_message.edit_text(
        f"📦 Project: {project}",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def show_processes(update, context):
    kb = [[InlineKeyboardButton(p.replace(" ", ""), callback_data=f"proc|{p}")]
          for p in actual_columns()]

    kb.append([
        InlineKeyboardButton("📊 %", callback_data="status"),
        InlineKeyboardButton("❌ Undo", callback_data="undo"),
    ])
    kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_items")])

    await update.effective_message.edit_text(
        "⚙ Select Process",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def ask_quantity(update, context):
    kb = [
        [InlineKeyboardButton("🔄 Edit Qty", callback_data="edit_qty")],
        [InlineKeyboardButton("⬅ Back", callback_data="back_processes")],
    ]
    await update.effective_message.edit_text(
        f"✏ Enter quantity for {context.user_data['process']}",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# =========================
# Commands & Buttons
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await show_clients(update)

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("client|"):
        context.user_data["client"] = data.split("|")[1]
        await show_projects(update, context)

    elif data.startswith("project|"):
        context.user_data["project"] = data.split("|")[1]
        await show_items(update, context)

    elif data.startswith("item|"):
        context.user_data["item"] = data.split("|")[1]
        await show_processes(update, context)

    elif data.startswith("proc|"):
        context.user_data["process"] = data.split("|")[1]
        await ask_quantity(update, context)

    elif data == "edit_qty":
        context.user_data["edit_mode"] = True
        await update.effective_message.edit_text("🔄 Enter corrected quantity:")

    elif data == "undo":
        await undo_last(update, context)

    elif data == "status":
        await show_status(update, context)

    elif data == "back_clients":
        await show_clients(update, edit=True)

    elif data == "back_projects":
        await show_projects(update, context)

    elif data == "back_items":
        await show_items(update, context)

    elif data == "back_processes":
        await show_processes(update, context)

# =========================
# Quantity Input
# =========================

async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text)
    except:
        await update.message.reply_text("❌ Enter a valid number")
        return

    client = context.user_data["client"]
    project = context.user_data["project"]
    item = context.user_data["item"]
    process = context.user_data["process"]

    row = find_row(client, project, item)
    col = col_index(process)

    if context.user_data.pop("edit_mode", False):
        summary.update_cell(row, col, qty)
    else:
        current = safe_int(summary.cell(row, col).value)
        summary.update_cell(row, col, current + qty)

    logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        update.effective_user.username,
        client,
        project,
        item,
    ])

    await update.message.reply_text("✅ Quantity updated")
    await show_clients(update)

# =========================
# Extra Features
# =========================

async def undo_last(update, context):
    row = find_row(
        context.user_data["client"],
        context.user_data["project"],
        context.user_data["item"],
    )

    last_proc = find_last_filled_process(row)
    if not last_proc:
        await update.effective_message.edit_text("❌ Nothing to undo")
        return

    summary.update_cell(row, col_index(last_proc), 0)
    await update.effective_message.edit_text(f"❌ Undone: {last_proc}")

async def show_status(update, context):
    row = find_row(
        context.user_data["client"],
        context.user_data["project"],
        context.user_data["item"],
    )

    total_plan = 0
    total_actual = 0

    for h in HEADERS:
        if is_plan(h):
            total_plan += safe_int(summary.cell(row, col_index(h)).value)
        elif h in actual_columns():
            total_actual += safe_int(summary.cell(row, col_index(h)).value)

    percent = (total_actual / total_plan * 100) if total_plan else 0

    await update.effective_message.edit_text(
        f"📊 Status\n\n"
        f"Completed: {total_actual}\n"
        f"Planned: {total_plan}\n\n"
        f"✅ {percent:.2f} %"
    )

# =========================
# Webhook / Server
# =========================

async def webhook(request):
    app = request.app["telegram_app"]
    update = Update.de_json(await request.json(), app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

async def health(request):
    return web.Response(text="OK")

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
        runner,
        "0.0.0.0",
        int(os.environ.get("PORT", 10000)),
    ).start()

    await app.bot.set_webhook(
        f"{os.environ['RENDER_EXTERNAL_URL']}/{os.environ['TELEGRAM_TOKEN']}"
    )

    print("✅ Bot running successfully")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
