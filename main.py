import threading
import uvicorn
import os
import subprocess
import sys

def run_api():
    uvicorn.run(
        "webapp_api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )

if __name__ == "__main__":
    import database as db
    db.init_db()

    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    from bot import dp, bot
    from aiogram.utils import executor
    executor.start_polling(dp, skip_updates=True)
