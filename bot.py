import os
import json
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

def get_sheet():
    return get_gspread_client().open(os.environ.get("SHEET_NAME", "ProgressLog"))

def summary_ws():
    return get_sheet().worksheet("Summary")

def logs_ws():
    return get_sheet().worksheet("Logs")

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
    clients = set()
    for r in rows:
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            clients.add(r[0].strip())
    return sorted(clients)

def get_projects(client):
    rows = summary_ws().get_all_values()[1:]
    return sorted({
        r[1].strip()
        for r in rows
        if len(r) >= 2 and r[0].strip() == client and r[1].strip()
    })

def get_tasks():
    hdr = headers()
    ignore = {"Client", "Project", "Tasks", "Completed", "Status (%)"}
    return [
        h for h in hdr
        if h not in ignore and not h.endswith("Plan")
    ]

def find_row(client, project):
    rows = summary_ws().get_all_values()
    for idx, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0] == client and r[1] == project:
            return idx
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
# COMMANDS
# ======================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state.pop(update.effective_user.id, None)

    buttons = [
        [InlineKeyboardButton(c, callback_data=f"client|{c}")]
        for c in get_clients()
    ]

    await update.message.reply_text(
        "👋 Select Client:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

# ======================================================
# BUTTON FLOW
# ======================================================

async def show_projects(query, user_id):
    client = user_state[user_id]["client"]
    buttons = [
        [InlineKeyboardButton(p, callback_data=f"project|{p}")]
        for p in get_projects(client)
    ]
    buttons.append([
        InlineKeyboardButton("⬅ Back", callback_data="back"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ])
    await query.edit_message_text(
        f"Client: *{client}*\nSelect Project:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )

async def show_tasks(query, user_id):
    buttons = [
        [InlineKeyboardButton(t, callback_data=f"task|{t}")]
        for t in get_tasks()
    ]
    buttons.append([
        InlineKeyboardButton("⬅ Back", callback_data="back"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ])
    await query.edit_message_text(
        "Select Task:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data.split("|")

    if data[0] == "cancel":
        user_state.pop(user_id, None)
        await query.edit_message_text("❌ Cancelled")
        return

    if data[0] == "back":
        step = user_state.get(user_id, {}).get("step")
        if step == "task":
            await show_projects(query, user_id)
        else:
            await start_cmd(update, context)
        return

    if data[0] == "client":
        user_state[user_id] = {"client": data[1], "step": "project"}
        await show_projects(query, user_id)

    elif data[0] == "project":
        user_state[user_id]["project"] = data[1]
        user_state[user_id]["step"] = "task"
        await show_tasks(query, user_id)

    elif data[0] == "task":
        user_state[user_id]["task"] = data[1]
        user_state[user_id]["step"] = "batch"
        await query.edit_message_text(
            f"Enter batch name for *{data[1]}*:",
            parse_mode="Markdown",
        )

# ======================================================
# TEXT INPUT (BATCH + QTY)
# ======================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_state:
        return

    state = user_state[user_id]

    if state["step"] == "batch":
        state["batch"] = update.message.text.strip()
        state["step"] = "qty"
        await update.message.reply_text("Enter quantity completed:")
        return

    if state["step"] == "qty":
        try:
            qty = float(update.message.text)
        except ValueError:
            await update.message.reply_text("❌ Enter a number")
            return

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

        await update.message.reply_text(
            f"✅ {state['task']} | Batch {state['batch']} updated by {qty}"
        )

        user_state.pop(user_id, None)

# ======================================================
# HEALTH CHECK (UPTIMEROBOT)
# ======================================================

async def health(request):
    return web.Response(text="OK")

async def post_init(application: Application):
    application.web_app.router.add_get("/", health)

# ======================================================
# MAIN
# ======================================================

def main():
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_TOKEN"])
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ["RENDER_EXTERNAL_URL"]

    print("🚀 Bot running (PTB 20.7 + Webhooks + Health Check)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ["TELEGRAM_TOKEN"],
        webhook_url=f"{URL}/{os.environ['TELEGRAM_TOKEN']}",
    )

if __name__ == "__main__":
    main()