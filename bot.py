import os
import json
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ================= GOOGLE SHEETS =================

def get_client():
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = service_account.Credentials.from_service_account_info(
        creds, scopes=scopes
    )
    return gspread.authorize(credentials)


def get_sheet():
    return get_client().open(os.environ.get("SHEET_NAME", "ProgressLog"))


def logs_ws():
    return get_sheet().worksheet("Logs")


def summary_ws():
    return get_sheet().worksheet("Summary")


# ================= HELPERS =================

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")


def summary_headers():
    return summary_ws().row_values(1)


def find_summary_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 2:
            if r[0].strip().lower() == client.lower() and r[1].strip().lower() == project.lower():
                return i
    return None


def create_summary_row(client, project):
    headers = summary_headers()
    row = [client, project] + [0] * (len(headers) - 2)
    summary_ws().append_row(row, value_input_option="USER_ENTERED")


def ensure_summary_row(client, project):
    if not find_summary_row(client, project):
        create_summary_row(client, project)


def add_quantity(client, project, task, qty):
    ws = summary_ws()
    ensure_summary_row(client, project)
    row = find_summary_row(client, project)
    headers = summary_headers()

    if task not in headers:
        return False, f"Task '{task}' not found in Summary"

    col = headers.index(task) + 1
    current = ws.cell(row, col).value
    current = float(current) if current else 0
    ws.update_cell(row, col, current + qty)
    return True, None


# ================= COMMANDS =================

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text(
            "Use:\n/log Client | Project | description"
        )
        return

    client, project, desc = [x.strip() for x in text.split("|", 2)]
    user = update.effective_user
    username = user.username or user.first_name or ""

    logs_ws().append_row(
        [now_ist(), username, client, project, desc],
        value_input_option="USER_ENTERED",
    )

    await update.message.reply_text("✅ Logged")


async def qty_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /qty Client | Project | Task | +number
    """
    text = " ".join(context.args)
    if text.count("|") < 3:
        await update.message.reply_text(
            "Use:\n/qty Client | Project | Task | +number"
        )
        return

    client, project, task, qty = [x.strip() for x in text.split("|", 3)]

    try:
        qty = float(qty.replace("+", "").strip())
    except ValueError:
        await update.message.reply_text("❌ Quantity must be a number")
        return

    ok, err = add_quantity(client, project, task, qty)
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return

    logs_ws().append_row(
        [now_ist(), update.effective_user.username or "", client, project, f"{task} +{qty}"],
        value_input_option="USER_ENTERED",
    )

    await update.message.reply_text(
        f"✅ Updated\n{client} / {project}\n{task}: +{qty}"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text(
            "Use:\n/status Client | Project"
        )
        return

    client, project = [x.strip() for x in text.split("|", 1)]
    ws = summary_ws()
    row = find_summary_row(client, project)

    if not row:
        await update.message.reply_text("❌ Project not found in Summary")
        return

    headers = ws.row_values(1)

    try:
        tasks_col = headers.index("Tasks") + 1
        completed_col = headers.index("Completed") + 1
        status_col = headers.index("Status (%)") + 1
    except ValueError:
        await update.message.reply_text("❌ Summary columns missing")
        return

    tasks = ws.cell(row, tasks_col).value
    completed = ws.cell(row, completed_col).value
    status = ws.cell(row, status_col).value

    await update.message.reply_text(
        f"📊 {client} / {project}\n"
        f"Tasks: {tasks}\n"
        f"Completed: {completed}\n"
        f"Status: {status}"
    )


# ================= MAIN =================

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("qty", qty_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    print("🚀 Bot started in POLLING mode")
    app.run_polling()


if __name__ == "__main__":
    main()
