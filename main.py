#!/usr/bin/env python3
"""
Bot va API serverni bir vaqtda ishga tushiradi.
Render.com da bitta service uchun.
"""
import asyncio
import threading
import uvicorn
import os

def run_api():
    uvicorn.run(
        "webapp_api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )

async def run_bot():
    from bot import dp, bot, db
    db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    # API ni alohida thread da ishga tushir
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    # Bot ni async loop da ishga tushir
    asyncio.run(run_bot())
