import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= GOOGLE SHEETS =================

def get_client():
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return gspread.authorize(
        service_account.Credentials.from_service_account_info(creds, scopes=scopes)
    )

def sheet():
    return get_client().open(os.environ.get("SHEET_NAME", "ProgressLog"))

def summary_ws():
    return sheet().worksheet("Summary")

def logs_ws():
    return sheet().worksheet("Logs")

# ================= HELPERS =================

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

def headers():
    return summary_ws().row_values(1)

def get_clients():
    ws = summary_ws()
    values = ws.col_values(1)[1:]
    return sorted(set(v for v in values if v))

def get_projects(client):
    rows = summary_ws().get_all_values()[1:]
    return sorted(set(r[1] for r in rows if r and r[0] == client))

def get_tasks():
    hdr = headers()
    tasks = []
    for h in hdr:
        if h.endswith("Plan"):
            continue
        if h in ("Client", "Project", "Tasks", "Completed", "Status (%)"):
            continue
        tasks.append(h)
    return tasks

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
    hdr = headers()

    if task not in hdr:
        return False, "Task not found"

    col = hdr.index(task) + 1
    current = ws.cell(row, col).value
    current = float(current) if current else 0
    ws.update_cell(row, col, current + qty)
    return True, None

# ================= USER STATE =================

user_state = {}

# ================= COMMANDS =================

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

# ================= BUTTON FLOW =================

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
        if step == "project":
            await start_cmd(update, context)
        elif step == "task":
            await show_projects(query, user_id)
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
        user_state[user_id]["step"] = "qty"
        await query.edit_message_text(
            f"Enter quantity completed for *{data[1]}*:",
            parse_mode="Markdown",
        )

# ================= QUANTITY INPUT =================

async def qty_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_state:
        return
    if user_state[user_id].get("step") != "qty":
        return

    try:
        qty = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Please enter a number only")
        return

    s = user_state[user_id]
    add_quantity(s["client"], s["project"], s["task"], qty)

    logs_ws().append_row(
        [
            now_ist(),
            update.effective_user.username or "",
            s["client"],
            s["project"],
            f"{s['task']} +{qty}",
        ],
        value_input_option="USER_ENTERED",
    )

    await update.message.reply_text(
        f"✅ {s['task']} updated by {qty}"
    )

    user_state.pop(user_id, None)

# ================= MAIN =================

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qty_text_handler))

    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ["RENDER_EXTERNAL_URL"]

    print("🚀 Bot running with dynamic buttons (FREE Render)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ["TELEGRAM_TOKEN"],
        webhook_url=f"{URL}/{os.environ['TELEGRAM_TOKEN']}",
    )

if __name__ == "__main__":
    main()