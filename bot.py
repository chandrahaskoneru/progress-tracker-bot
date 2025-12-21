import os
import json
import asyncio
from datetime import datetime
from aiohttp import web

import gspread
from google.oauth2.service_account import Credentials

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

# =========================
# Google Sheets (NO Drive API)
# =========================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)

SHEET_ID = os.environ["SHEET_ID"]

summary = gc.open_by_key(SHEET_ID).worksheet("Summary")
logs = gc.open_by_key(SHEET_ID).worksheet("Logs")

# =========================
# Process columns (NO plan columns)
# =========================

PROCESS_COLUMNS = {
    "Raw Material": 5,
    "Rough Turning": 7,
    "Heat Treatment": 9,
    "Final Machining": 11,
    "Keyway": 13,
    "GC": 15,
    "Spline": 17,
    "CG": 19,
    "SG": 21,
    "IH": 23,
    "GG": 25,
}

# =========================
# Helpers
# =========================

def rows():
    return summary.get_all_records()

def get_clients():
    return sorted({r["Client"] for r in rows() if r.get("Client")})

def get_projects(client):
    return sorted({
        r["Project"] for r in rows()
        if r.get("Client") == client and r.get("Project")
    })

def get_items(client, project):
    return sorted({
        r["Item Description"] for r in rows()
        if r.get("Client") == client
        and r.get("Project") == project
        and r.get("Item Description")
    })

def find_row(client, project, item):
    for idx, r in enumerate(rows(), start=2):
        if (
            r["Client"] == client
            and r["Project"] == project
            and r["Item Description"] == item
        ):
            return idx
    return None

# =========================
# Telegram Flow
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    clients = get_clients()

    if not clients:
        await update.message.reply_text("❌ No clients found in sheet.")
        return

    kb = [[InlineKeyboardButton(c, callback_data=f"client|{c}")] for c in clients]
    await update.message.reply_text(
        "📋 Select Client:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|")

    if data[0] == "client":
        context.user_data["client"] = data[1]
        projects = get_projects(data[1])

        kb = [[InlineKeyboardButton(p, callback_data=f"project|{p}")] for p in projects]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_clients")])

        await q.edit_message_text(
            "📁 Select Project:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "project":
        context.user_data["project"] = data[1]
        items = get_items(context.user_data["client"], data[1])

        kb = [[InlineKeyboardButton(i, callback_data=f"item|{i}")] for i in items]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_projects")])

        await q.edit_message_text(
            "📦 Select Item Description:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "item":
        context.user_data["item"] = data[1]

        kb = [
            [InlineKeyboardButton(p, callback_data=f"process|{p}")]
            for p in PROCESS_COLUMNS.keys()
        ]
        kb.append([InlineKeyboardButton("⬅ Back", callback_data="back_items")])

        await q.edit_message_text(
            "⚙️ Select Process:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    elif data[0] == "process":
        context.user_data["process"] = data[1]
        await q.edit_message_text("✏️ Enter quantity to add:")

    elif data[0].startswith("back"):
        await start(update, context)

# =========================
# Quantity input
# =========================

async def quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "process" not in context.user_data:
        return

    try:
        qty = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number.")
        return

    c = context.user_data["client"]
    p = context.user_data["project"]
    i = context.user_data["item"]
    proc = context.user_data["process"]

    row = find_row(c, p, i)
    col = PROCESS_COLUMNS[proc]

    current = summary.cell(row, col).value or 0
    summary.update_cell(row, col, int(current) + qty)

    logs.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        update.effective_user.username,
        c,
        p,
        f"{proc} +{qty}"
    ])

    await update.message.reply_text("✅ Quantity updated successfully!")
    context.user_data.clear()

# =========================
# Webhook
# =========================

async def health(request):
    return web.Response(text="OK")

async def telegram_webhook(request):
    app = request.app["telegram_app"]
    update = Update.de_json(await request.json(), app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# =========================
# Main
# =========================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_input))

    await app.initialize()
    await app.start()

    web_app = web.Application()
    web_app["telegram_app"] = app
    web_app.router.add_get("/", health)
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    await app.bot.set_webhook(f"{BASE_URL}/{TOKEN}")
    print("🚀 Bot fully operational")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
