import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from aiohttp import web

# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is alive!\n\n/start command received successfully."
    )

async def health(request):
    return web.Response(text="OK")

# =========================
# Main
# =========================

def main():
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN not set")

    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ.get("RENDER_EXTERNAL_URL")

    if not BASE_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL not set")

    app = Application.builder().token(TOKEN).build()

    # Register command
    app.add_handler(CommandHandler("start", start))

    # ---- Health endpoint for Render / UptimeRobot ----
    app.web_app.router.add_get("/", health)

    print("🚀 Bot running (minimal webhook test)")

    # ---- Webhook ----
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{BASE_URL}/{TOKEN}",
    )

if __name__ == "__main__":
    main()