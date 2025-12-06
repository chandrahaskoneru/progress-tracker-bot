import os
import json
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2 import service_account

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ---------- Google Sheets helpers ----------

def get_gsheet_client():
    """
    Build a gspread client from the JSON stored in the env var
    GOOGLE_SERVICE_ACCOUNT_JSON.
    """
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is missing")

    info = json.loads(creds_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=scopes
    )
    gc = gspread.authorize(credentials)
    return gc


def get_sheet_name():
    return os.environ.get("SHEET_NAME", "ProgressLog")


def get_logs_worksheet():
    """
    Returns the worksheet used to log individual tasks.
    SHEET_NAME: Google Sheet name (not the tab name).
    SHEET_TAB: Tab name inside the sheet (for logs).
    """
    sheet_name = get_sheet_name()
    sheet_tab = os.environ.get("SHEET_TAB", "Logs")

    gc = get_gsheet_client()
    sh = gc.open(sheet_name)
    ws = sh.worksheet(sheet_tab)
    return ws


def get_summary_worksheet():
    """
    Returns the Summary worksheet used for client/project overview.

    Expected columns:
      A: Client
      B: Project
      C: Tasks (total planned tasks)
      D: Completed (can be formula or left for bot to fill if desired)
      E: Status (%)
    """
    sheet_name = get_sheet_name()
    sheet_tab = os.environ.get("SHEET_SUMMARY_TAB", "Summary")

    gc = get_gsheet_client()
    sh = gc.open(sheet_name)
    ws = sh.worksheet(sheet_tab)
    return ws


def log_to_sheet(username: str, client: str, project: str, description: str):
    """
    Append a single row to the Logs sheet.

    Logs columns:
      Timestamp | Username | Client | Project | Description
    """
    ws = get_logs_worksheet()

    # India time (Asia/Kolkata = UTC+5:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    ws.append_row(
        [ts, username or "", client, project, description],
        value_input_option="USER_ENTERED",
    )


def compute_project_status(client: str, project: str):
    """
    Compute (completed_tasks, total_tasks, percentage) for a given client+project.

    - total_tasks is taken from Summary!C (Tasks)
    - completed_tasks is counted from Logs sheet rows matching client & project
    """
    # 1) Find total_tasks in Summary sheet
    summary_ws = get_summary_worksheet()
    summary_rows = summary_ws.get_all_values()  # list of lists

    # header: Client | Project | Tasks | Completed | Status (%)
    total_tasks = None
    row_index = None

    client_lower = client.strip().lower()
    project_lower = project.strip().lower()

    for i, row in enumerate(summary_rows[1:], start=2):  # skip header row
        if len(row) < 3:
            continue
        row_client = row[0].strip().lower()
        row_project = row[1].strip().lower()
        if row_client == client_lower and row_project == project_lower:
            row_index = i
            tasks_str = row[2].strip()
            if tasks_str:
                try:
                    total_tasks = int(tasks_str)
                except ValueError:
                    total_tasks = None
            break

    if row_index is None:
        # No matching client+project row in Summary
        return None, None, None

    if not total_tasks or total_tasks <= 0:
        # Tasks not properly defined
        return 0, None, None

    # 2) Count completed tasks from Logs sheet
    logs_ws = get_logs_worksheet()
    log_rows = logs_ws.get_all_values()

    completed = 0
    for row in log_rows[1:]:  # skip header
        if len(row) < 4:
            continue
        log_client = row[2].strip().lower()
        log_project = row[3].strip().lower()
        if log_client == client_lower and log_project == project_lower:
            completed += 1

    percentage = (completed / total_tasks) * 100 if total_tasks else None
    return completed, total_tasks, percentage


def list_all_clients():
    """
    Return a sorted list of unique clients from the Summary sheet.
    """
    summary_ws = get_summary_worksheet()
    rows = summary_ws.get_all_values()

    clients = set()
    for row in rows[1:]:
        if not row:
            continue
        if len(row) < 1:
            continue
        client = row[0].strip()
        if client:
            clients.add(client)

    return sorted(clients)


def list_projects_for_client(client: str):
    """
    Return a sorted list of projects for a given client from the Summary sheet.
    """
    summary_ws = get_summary_worksheet()
    rows = summary_ws.get_all_values()

    projects = set()
    client_lower = client.strip().lower()

    for row in rows[1:]:
        if len(row) < 2:
            continue
        row_client = row[0].strip().lower()
        row_project = row[1].strip()
        if row_client == client_lower and row_project:
            projects.add(row_project)

    return sorted(projects)


# ---------- Telegram handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Hi! I’m your progress tracking bot.\n\n"
        "Commands:\n"
        "  /log client | project | description  – log a completed task\n"
        "  /status client | project             – show % completed\n"
        "  /clients                             – list all clients\n"
        "  /projects client                     – list all projects for a client\n\n"
        "Example log:\n"
        "  /log Client A | Website Revamp | Homepage UI done\n\n"
        "Example status:\n"
        "  /status Client A | Website Revamp"
    )
    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ️ Commands:\n\n"
        "/start – Welcome message.\n"
        "/help – This help message.\n\n"
        "/log client | project | description\n"
        "  Log a completed task.\n"
        "  Example:\n"
        "  /log Client A | Website Revamp | Homepage UI done\n\n"
        "/status client | project\n"
        "  Show progress for a project (based on total tasks in Summary sheet).\n"
        "  Example:\n"
        "  /status Client A | Website Revamp\n\n"
        "/clients\n"
        "  List all clients from the Summary sheet.\n\n"
        "/projects client\n"
        "  List all projects for a given client from the Summary sheet.\n"
        "  Example:\n"
        "  /projects Client A"
    )
    await update.message.reply_text(msg)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /log client | project | description

    Example:
      /log Client A | Website Revamp | Homepage UI done
    """
    if not context.args:
        await update.message.reply_text(
            "Please use:\n"
            "/log client | project | description\n\n"
            "Example:\n"
            "/log Client A | Website Revamp | Homepage UI done"
        )
        return

    full_text = " ".join(context.args)
    parts = [p.strip() for p in full_text.split("|")]

    if len(parts) < 3:
        await update.message.reply_text(
            "I need 3 parts separated by '|':\n"
            "client | project | description\n\n"
            "Example:\n"
            "/log Client A | Website Revamp | Homepage UI done"
        )
        return

    client, project, description = parts[0], parts[1], "|".join(parts[2:]).strip()

    user = update.effective_user
    username = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()

    try:
        log_to_sheet(username, client, project, description)
    except Exception as e:
        print("Error logging to sheet:", e)
        await update.message.reply_text(
            "❌ I couldn't write to Google Sheets. Please check configuration on the server."
        )
        return

    await update.message.reply_text(
        f"✅ Logged task for client '{client}', project '{project}'."
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /status client | project

    Example:
      /status Client A | Website Revamp
    """
    if not context.args:
        await update.message.reply_text(
            "Please use:\n"
            "/status client | project\n\n"
            "Example:\n"
            "/status Client A | Website Revamp"
        )
        return

    full_text = " ".join(context.args)
    parts = [p.strip() for p in full_text.split("|")]

    if len(parts) < 2:
        await update.message.reply_text(
            "I need 2 parts separated by '|':\n"
            "client | project\n\n"
            "Example:\n"
            "/status Client A | Website Revamp"
        )
        return

    client, project = parts[0], parts[1]

    try:
        completed, total, percentage = compute_project_status(client, project)
    except Exception as e:
        print("Error computing status:", e)
        await update.message.reply_text(
            "❌ I couldn't read from Google Sheets. Please check configuration on the server."
        )
        return

    if total is None:
        await update.message.reply_text(
            "I couldn't find this client/project in the Summary sheet.\n\n"
            "Please add a row in the Summary tab with:\n"
            "Client | Project | Tasks (total)\n"
            f"Client: {client}\n"
            f"Project: {project}"
        )
        return

    if total == 0:
        await update.message.reply_text(
            "Total tasks for this project are not properly set in the Summary sheet."
        )
        return

    pct_str = f"{percentage:.1f}%" if percentage is not None else "N/A"

    await update.message.reply_text(
        f"📊 Status for {client} / {project}:\n"
        f"Completed: {completed} / {total}\n"
        f"Progress: {pct_str}"
    )


async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    List all clients from the Summary sheet.
    """
    try:
        clients = list_all_clients()
    except Exception as e:
        print("Error listing clients:", e)
        await update.message.reply_text(
            "❌ I couldn't read clients from Google Sheets. Please check configuration."
        )
        return

    if not clients:
        await update.message.reply_text(
            "No clients found in the Summary sheet.\n"
            "Please add rows with Client and Project in the Summary tab."
        )
        return

    text = "👥 Clients:\n" + "\n".join(f"- {c}" for c in clients)
    await update.message.reply_text(text)


async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      /projects client

    Example:
      /projects Client A
    """
    if not context.args:
        await update.message.reply_text(
            "Please specify a client name.\n\n"
            "Example:\n"
            "/projects Client A"
        )
        return

    client = " ".join(context.args).strip()

    try:
        projects = list_projects_for_client(client)
    except Exception as e:
        print("Error listing projects:", e)
        await update.message.reply_text(
            "❌ I couldn't read projects from Google Sheets. Please check configuration."
        )
        return

    if not projects:
        await update.message.reply_text(
            f"No projects found for client '{client}' in the Summary sheet.\n"
            "Make sure the client and projects are listed there."
        )
        return

    text = f"📁 Projects for {client}:\n" + "\n".join(f"- {p}" for p in projects)
    await update.message.reply_text(text)


# ---------- Main entry ----------

def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN env var is missing")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("log", log_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("clients", clients_cmd))
    application.add_handler(CommandHandler("projects", projects_cmd))

    # Detect environment: local (no RENDER_EXTERNAL_URL) => polling
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    port = int(os.environ.get("PORT", "8000"))

    if render_url:
        # Running on Render → use webhook
        webhook_url = f"{render_url}/{token}"

        print(f"Starting webhook. Public URL: {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=webhook_url,
        )
    else:
        # Local development: long polling
        print("RENDER_EXTERNAL_URL not set. Using polling.")
        application.run_polling()


if __name__ == "__main__":
    main()
