import os, json
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2 import service_account
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------- GOOGLE SHEETS ----------

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

# ---------- HELPERS ----------

def now_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

def headers():
    return summary_ws().row_values(1)

def find_row(client, project):
    rows = summary_ws().get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0].lower() == client.lower() and r[1].lower() == project.lower():
            return i
    return None

def ensure_row(client, project):
    ws = summary_ws()
    if not find_row(client, project):
        cols = len(headers())
        ws.append_row([client, project] + [0] * (cols - 2))

def add_quantity(client, project, task, qty):
    ws = summary_ws()
    ensure_row(client, project)
    row = find_row(client, project)
    hdr = headers()

    done_col_name = f"{task} Done"
    if done_col_name not in hdr:
        return False, f"Task '{task}' not found"

    col = hdr.index(done_col_name) + 1
    current = ws.cell(row, col).value
    current = float(current) if current else 0
    ws.update_cell(row, col, current + qty)
    return True, None

# ---------- COMMANDS ----------

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
    qty = float(qty)

    ok, err = add_quantity(client, project, task, qty)
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return

    logs_ws().append_row(
        [now_ist(), update.effective_user.username, client, project, f"{task} +{qty}"],
        value_input_option="USER_ENTERED",
    )

    await update.message.reply_text(
        f"✅ Updated\n{client} / {project}\n{task}: +{qty}"
    )

# ---------- MAIN ----------

def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()
    app.add_handler(CommandHandler("qty", qty_cmd))

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
