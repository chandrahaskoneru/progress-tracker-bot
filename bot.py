import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account
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
    MessageHandler,
    ContextTypes,
    filters,
)

# ======================================================
# GOOGLE SHEETS
# ======================================================

def get_gspread_client():
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return gspread.authorize(
        service_account.Credentials.from_service_account_info(creds, scopes=scopes)
    )

def sheet():
    return get_gspread_client().open(os.environ.get("SHEET_NAME", "ProgressLog"))

def summary_ws():
    return sheet().worksheet("Summary")

def logs_ws():
    return sheet().worksheet("Logs")

# ======================================================
# HELPERS
# ======================================================

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

def headers():
    return summary_ws().row_values(1)

def get_clients():
    rows = summary_ws().get_all_values()[1:]
    return sorted({
        r[0].strip()
        for r in rows
        if len(r) >= 2 and r[0].strip() and r[1].strip()
    })

def get_projects(client):
    rows = summary_ws().get_all_values()[1:]
    return sorted({
        r[1].strip()
        for r in rows
        if len(r) >= 2 and r[0].strip() == client and r[1].strip()
    })

def get_tasks():
    ignore = {"Client", "Project", "Tasks", "Completed", "Status (%)"}
    return [
        h for h in headers()
        if h not in ignore and not h.endswith("Plan")
    ]

def find_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0] == client and r[1] == project:
            return i
    return None

def ensure_row(client, project):
    if not find_row(client, project):
        summary_ws().append_row(
            [client, project] + [0] * (len(headers()) - 2),
            value_input_option="USER_ENTERED",
        )

def add_quantity(client, project, task, qty):
    ws = summary_ws()
    ensure_row(client, project)
    row = find_row(client, project)
    col = headers().index(task) + 1
    current = float(ws.cell(row, col).value or 0)
    ws.update_cell(row, col, current + qty)

# ======================================================
# USER STATE
# ======================================================

user_state = {}

# ======================================================
# BOT HANDLERS
# ======================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_user.id, None)
    buttons = [[InlineKeyboardButton(c, callback_data=f"client|{c}")]
               for c in get_clients()]
    await update.message.reply_text(
        "👋 Select Client:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data.split("|")

    if data[0] == "client":
        user_state[uid] = {"client": data[1], "step": "project"}
        buttons = [[InlineKeyboardButton(p, callback_data=f"project|{p}")]
                   for p in get_projects(data[1])]
        await query.edit_message_text(
            "Select Project:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data[0] == "project":
        user_state[uid]["project"] = data[1]
        user_state[uid]["step"] = "task"
        buttons = [[InlineKeyboardButton(t, callback_data=f"task|{t}")]
                   for t in get_tasks()]
        await query.edit_message_text(
            "Select Task:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data[0] == "task":
        user_state[uid]["task"] = data[1]
        user_state[uid]["step"] = "batch"
        await query.edit_message_text("Enter batch name:")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_state:
        return

    state = user_state[uid]

    if state["step"] == "batch":
        state["batch"] = update.message.text
        state["step"] = "qty"
        await update.message.reply_text("Enter quantity:")
        return

    if state["step"] == "qty":
        qty = float(update.message.text)
        add_quantity(state["client"], state["project"], state["task"], qty)

        logs_ws().append_row(
            [
                now_ist(),
                update.effective_user.username or "",
                state["client"],
                state["project"],
                state["task"],
                state["batch"],
                qty,
            ],
            value_input_option="USER_ENTERED",
        )

        await update.message.reply_text("✅ Updated")
        user_state.pop(uid, None)

# ======================================================
# HEALTH SERVER (INDEPENDENT)
# ======================================================

async def health(request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()

# ======================================================
# MAIN
# ======================================================

async def main():
    await start_health_server()

    application = Application.builder().token(
        os.environ["TELEGRAM_TOKEN"]
    ).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    await application.bot.set_webhook(
        f"{os.environ['RENDER_EXTERNAL_URL']}/{os.environ['TELEGRAM_TOKEN']}"
    )

    await application.initialize()
    await application.start()
    await application.updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=os.environ["TELEGRAM_TOKEN"],
    )

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())