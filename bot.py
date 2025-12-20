import os
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================
# Telegram Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is alive and responding!")

# =========================
# Health Endpoint
# =========================

async def health(request):
    return web.Response(text="OK")

# =========================
# Main Runner
# =========================

async def main():
    TOKEN = os.environ["TELEGRAM_TOKEN"]
    PORT = int(os.environ.get("PORT", 10000))
    BASE_URL = os.environ["RENDER_EXTERNAL_URL"]

    # ---- Telegram app ----
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    # ---- Aiohttp app (health check) ----
    web_app = web.Application()
    web_app.router.add_get("/", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print("🚀 Bot + Health server running")

    # ---- Start Telegram webhook ----
    await application.bot.set_webhook(f"{BASE_URL}/{TOKEN}")

    await application.initialize()
    await application.start()
    await application.stop_when_idle()

if __name__ == "__main__":
    asyncio.run(main())