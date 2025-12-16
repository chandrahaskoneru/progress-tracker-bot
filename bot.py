import os, json
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2 import service_account
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------------- SHEETS ----------------

def gc():
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return gspread.authorize(
        service_account.Credentials.from_service_account_info(creds, scopes=scopes)
    )

def sheet():
    return gc().open(os.environ.get("SHEET_NAME", "ProgressLog"))

def logs_ws():
    return sheet().worksheet("Logs")

def summary_ws():
    return sheet().worksheet("Summary")

# ---------------- HELPERS ----------------

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

def get_headers():
    return summary_ws().row_values(1)

def find_summary_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0].strip().lower() == client.lower() and r[1].strip().lower() == project.lower():
            return i
    return None

def create_summary_row(client, project):
    headers = get_headers()
    task_cols = headers[2:-3]  # C:M
    row = [client, project] + ["-" for _ in task_cols] + ["", "", ""]
    summary_ws().append_row(row, value_input_option="USER_ENTERED")

def update_task(client, project, task_name, value):
    ws = summary_ws()
    headers = get_headers()

    if task_name not in headers:
        return False, f"Task '{task_name}' not found in Summary header"

    row = find_summary_row(client, project)
    if not row:
        create_summary_row(client, project)
        row = find_summary_row(client, project)

    col = headers.index(task_name) + 1
    ws.update_cell(row, col, value)
    return True, None

# ---------------- COMMANDS ----------------

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /log Client | Project | Task completed/skip")
        return

    client, project, desc = [x.strip() for x in text.split("|", 2)]
    user = update.effective_user
    username = user.username or user.first_name

    logs_ws().append_row(
        [now_ist(), username, client, project, desc],
        value_input_option="USER_ENTERED",
    )

    desc_lower = desc.lower()

    # Try auto task update
    headers = get_headers()
    task_columns = headers[2:-3]

    for task in task_columns:
        if task.lower() in desc_lower:
            if "skip" in desc_lower or "not required" in desc_lower:
                update_task(client, project, task, "-")
                status = "skipped"
            else:
                update_task(client, project, task, "1")
                status = "completed"

            await update.message.reply_text(
                f"✅ Logged + updated Summary\n{client} / {project}\nTask: {task} → {status}"
            )
            return

    await update.message.reply_text("✅ Logged (no task auto-detected)")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if "|" not in text:
        await update.message.reply_text("Use: /status Client | Project")
        return

    client, project = [x.strip() for x in text.split("|", 1)]
    ws = summary_ws()
    row = find_summary_row(client, project)

    if not row:
        await update.message.reply_text("Project not found in Summary")
        return

    completed = ws.cell(row, ws.col_count - 1).value
    percent = ws.cell(row, ws.col_count).value

    await update.message.reply_text(
        f"📊 {client} / {project}\nCompleted: {completed}\nStatus: {percent}"
    )

# ---------------- MAIN ----------------

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    url = os.environ.get("RENDER_EXTERNAL_URL")
    port = int(os.environ.get("PORT", 8000))

    if url:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=os.environ["TELEGRAM_TOKEN"],
            webhook_url=f"{url}/{os.environ['TELEGRAM_TOKEN']}",
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
