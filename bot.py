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
    ContextTypes,
    CallbackQueryHandler,
)
import gspread
from google.oauth2.service_account import Credentials

# =========================
# Google Sheets setup
# =========================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

SHEET_NAME = os.environ["SHEET_NAME"]
SHEET_TAB = os.environ["SHEET_TAB"]

sheet = gc.open(SHEET_NAME).worksheet(SHEET_TAB)

# =========================
# Column mapping (Actual qty columns)
# =========================

PROCESS_COLS = {
    "Raw Material": 4,
    "Rough Turning": 6,
    "Heat treatment": 8,
    "Final Machining": 10,
    "Keyway": 12,
    "GC": 14,
    "Spline": 16,
    "CG": 18,
    "SG": 20,
    "IH": 22,
    "GG": 24,
}

PLAN_OFFSET = -1
TASKS_COL = 25
COMPLETED_COL = 26
STATUS_COL = 27

# =========================
# Helpers
# =========================

def get_rows():
    return sheet.get_all_values()[1:]  # skip header

def find_row(client, project):
    rows = get_rows()
    for idx, row in enumerate(rows, start=2):
        if row[0].strip() == client and row[1].strip() == project:
            return idx, row
    return None, None

def calculate_status(row):
    completed = 0
    total_tasks = int(row[TASKS_COL - 1] or 0)

    for col in PROCESS_COLS.values():
        plan = int(row[col + PLAN_OFFSET - 1] or 0)
        actual = int(row[col - 1] or 0)
        if plan > 0 and actual >= plan:
            completed += 1

    status = round((completed / total_tasks) * 100, 2) if total_tasks else 0
    return completed, status

# =========================
# Telegram handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_rows()
    clients = sorted(set(r[0] for r in rows if r[0].strip()))

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    keyboard = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]
    await update.message.reply_text(
        "Select Client:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def client_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    client = query.data.split("|")[1]
    context.user_data["client"] = client

    rows = get_rows()
    projects = sorted(set(r[1] for r in rows if r[0] == client and r[1].strip()))

    keyboard = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

    await query.edit_message_text(
        f"Client: {client}\nSelect Project:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    project = query.data.split("|")[1]
    context.user_data["project"] = project

    keyboard = [
        [InlineKeyboardButton(p, callback_data=f"process|{p}")]
        for p in PROCESS_COLS.keys()
    ]
    keyboard.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

    await query.edit_message_text(
        f"Project: {project}\nSelect Process:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def process_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    process = query.data.split("|")[1]
    context.user_data["process"] = process

    await query.edit_message_text(
        f"Process: {process}\nSend quantity like:\n+4"
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.startswith("+"):
        return

    qty = int(update.message.text.replace("+", ""))
    client = context.user_data.get("client")
    project = context.user_data.get("project")
    process = context.user_data.get("process")

    if not all([client, project, process]):
        await update.message.reply_text("❌ Select Client → Project → Process first.")
        return

    row_idx, row = find_row(client, project)
    col = PROCESS_COLS[process]

    current = int(row[col - 1] or 0)
    new_val = current + qty
    sheet.update_cell(row_idx, col, new_val)

    # Update status
    updated_row = sheet.row_values(row_idx)
    completed, status = calculate_status(updated_row)
    sheet.update_cell(row_idx, COMPLETED_COL, completed)
    sheet.update_cell(row_idx, STATUS_COL, f"{status}%")

    await update.message.reply_text(
        f"✅ Updated\n{process}: {current} → {new_val}\nStatus: {status}%"
    )

# =========================
# Aiohttp webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    app = request.app["telegram_app"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
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
    telegram_app.add_handler(CallbackQueryHandler(start, pattern="^back_clients$"))
    telegram_app.add_handler(CallbackQueryHandler(client_selected, pattern="^back_projects$"))
    telegram_app.add_handler(CommandHandler("log", start))
    telegram_app.add_handler(CommandHandler("clients", start))
    telegram_app.add_handler(
        CommandHandler("status", start)
    )
    telegram_app.add_handler(
        telegram.ext.MessageHandler(
            telegram.ext.filters.TEXT & ~telegram.ext.filters.COMMAND,
            text_handler,
        )
    )

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

    print("🚀 Progress Tracker Bot running (Webhook + Sheets)")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
