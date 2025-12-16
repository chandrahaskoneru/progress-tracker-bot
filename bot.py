import os
import json
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# ------------------ GOOGLE SHEETS ------------------

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


# ------------------ HELPERS ------------------

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")


def find_summary_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if r[0].strip().lower() == client.lower() and r[1].strip().lower() == project.lower():
            return i, rows[0]
    return None, None


def update_task(client, project, task, value):
    ws = summary_ws()
    row, headers = find_summary_row(client, project)

    if not row:
        return False, "Client / Project not found in Summary"

    if task not in headers:
        return False, f"Task '{task}' not found. Check column name."

    col = headers.index(task) + 1
    ws.update_cell(row, col, value)
    return True, None


# ------------------ COMMANDS ------------------

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /log client | project | description")
        return

    client, project, desc = [x.strip() for x in text.split("|", 2)]
    user = update.effective_user
    username = user.username or user.first_name

    logs_ws().append_row(
        [now_ist(), username, client, project, desc],
        value_input_option="USER_ENTERED",
    )

    await update.message.reply_text("✅ Logged")


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await task_update(update, context, "1")


async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await task_update(update, context, "-")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await task_update(update, context, "0")


async def task_update(update, context, value):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text(
            "Use: /done client | project | task"
        )
        return

    client, project, task = [x.strip() for x in text.split("|", 2)]
    ok, err = update_task(client, project, task, value)

    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return

    await update.message.reply_text(
        f"✅ {task} updated for {client} / {project}"
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /status client | project")
        return

    client, project = [x.strip() for x in text.split("|", 1)]
    ws = summary_ws()
    row, _ = find_summary_row(client, project)

    if not row:
        await update.message.reply_text("Not found in Summary")
        return

    completed = ws.cell(row, ws.col_count - 1).value
    percent = ws.cell(row, ws.col_count).value

    await update.message.reply_text(
        f"📊 {client} / {project}\nCompleted: {completed}\nStatus: {percent}"
    )


async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = summary_ws().get_all_values()[1:]
    clients = sorted(set(r[0] for r in rows if r[0]))
    await update.message.reply_text("Clients:\n" + "\n".join(clients))


async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client = " ".join(context.args).strip().lower()
    rows = summary_ws().get_all_values()[1:]
    projects = sorted(r[1] for r in rows if r[0].lower() == client)
    await update.message.reply_text("Projects:\n" + "\n".join(projects))


# ------------------ MAIN ------------------

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("clients", clients_cmd))
    app.add_handler(CommandHandler("projects", projects_cmd))

    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    port = int(os.environ.get("PORT", 8000))

    if render_url:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=os.environ["TELEGRAM_TOKEN"],
            webhook_url=f"{render_url}/{os.environ['TELEGRAM_TOKEN']}",
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
