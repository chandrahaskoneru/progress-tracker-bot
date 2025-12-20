import os
import asyncio
from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ==================================================
# Telegram Handlers
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot is alive and responding!\n\n"
        "Webhook + aiohttp setup is working."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "/start - Check bot status\n"
        "/help  - Show this help message"
    )

# ==================================================
# aiohttp Handlers
# ==================================================

async def health(request):
    # Used by browser / UptimeRobot
    return web.Response(text="OK")

async def telegram_webhook(request):
    """
    Receives Telegram updates and forwards them
    to python-telegram-bot Application
    """
    telegram_app: Application = request.app["telegram_app"]

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)

    return web.Response(text="OK")

# ==================================================
# Main
# ==================================================

async def main():
    # ---- Environment variables ----
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN not set")
    if not BASE_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL not set")

    # ---- Telegram Application ----
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_cmd))

    await telegram_app.initialize()
    await telegram_app.start()

    # ---- aiohttp Web Server ----
    web_app = web.Application()
    web_app["telegram_app"] = telegram_app

    # Health check endpoint
    web_app.router.add_get("/", health)

    # Telegram webhook endpoint
    web_app.router.add_post(f"/{TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    # ---- Register Telegram Webhook ----
    await telegram_app.bot.set_webhook(
        url=f"{BASE_URL}/{TOKEN}",
        drop_pending_updates=True,
    )

    print("🚀 Bot fully running (webhook + aiohttp)")

    # ---- Keep process alive forever ----
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())