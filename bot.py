import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
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

def logs_ws():
    return sheet().worksheet("Logs")

def summary_ws():
    return sheet().worksheet("Summary")

# ================= HELPERS =================

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

def headers():
    return summary_ws().row_values(1)

def find_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0].lower() == client.lower() and r[1].lower() == project.lower():
            return i
    return None

def ensure_row(client, project):
    if not find_row(client, project):
        summary_ws().append_row([client, project] + [0] * (len(headers()) - 2))

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

# ================= ASYNC WORK =================

async def process_qty(update: Update, client, project, task, qty):
    add_quantity(client, project, task, qty)
    logs_ws().append_row(
        [now_ist(), update.effective_user.username or "", client, project, f"{task} +{qty}"],
        value_input_option="USER_ENTERED",
    )

async def process_log(update: Update, client, project, desc):
    logs_ws().append_row(
        [now_ist(), update.effective_user.username or "", client, project, desc],
        value_input_option="USER_ENTERED",
    )

# ================= COMMANDS =================

async def qty_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if text.count("|") < 3:
        await update.message.reply_text("Use: /qty Client | Project | Task | +number")
        return

    client, project, task, qty = [x.strip() for x in text.split("|", 3)]
    qty = float(qty.replace("+", "").strip())

    # ACK immediately
    await update.message.reply_text("✅ Updating…")

    # Run slow work AFTER ACK
    asyncio.create_task(process_qty(update, client, project, task, qty))


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /log Client | Project | description")
        return

    client, project, desc = [x.strip() for x in text.split("|", 2)]

    await update.message.reply_text("✅ Logged")
    asyncio.create_task(process_log(update, client, project, desc))


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /status Client | Project")
        return

    client, project = [x.strip() for x in text.split("|", 1)]
    ws = summary_ws()
    row = find_row(client, project)

    if not row:
        await update.message.reply_text("Project not found")
        return

    hdr = headers()
    tasks = ws.cell(row, hdr.index("Tasks") + 1).value
    completed = ws.cell(row, hdr.index("Completed") + 1).value
    status = ws.cell(row, hdr.index("Status (%)") + 1).value

    await update.message.reply_text(
        f"📊 {client} / {project}\nTasks: {tasks}\nCompleted: {completed}\nStatus: {status}"
    )

# ================= MAIN =================

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("qty", qty_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    PORT = int(os.environ.get("PORT", 10000))
    URL = os.environ["RENDER_EXTERNAL_URL"]

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ["TELEGRAM_TOKEN"],
        webhook_url=f"{URL}/{os.environ['TELEGRAM_TOKEN']}",
    )

if __name__ == "__main__":
    main()
