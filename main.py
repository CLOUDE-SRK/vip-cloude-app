import threading
import subprocess
import uvicorn
import os
import time
import logging
import sys

logging.basicConfig(level=logging.INFO)


def run_api():
    """FastAPI serverni alohida thread'da ishga tushiradi."""
    uvicorn.run(
        "webapp_api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )


def run_admin_bot_process():
    """Admin botni BUTUNLAY ALOHIDA Python process sifatida ishga
    tushiradi (subprocess orqali). Bu yondashuv eng ishonchli, chunki
    admin bot o'zining mustaqil Python interpreteri va event loop'iga
    ega bo'ladi - asosiy bot/API bilan hech qanday thread yoki
    event-loop ziddiyati bo'lishi mumkin emas.

    Agar ADMIN_BOT_TOKEN sozlanmagan bo'lsa, bu funksiya hech narsa
    qilmaydi - asosiy bot va API normal ishlashda davom etadi."""
    if not os.environ.get("ADMIN_BOT_TOKEN"):
        logging.warning("ADMIN_BOT_TOKEN sozlanmagan - admin bot ishga tushmaydi.")
        return

    while True:
        try:
            logging.info("[Admin bot] Process ishga tushirilmoqda...")
            # admin_bot.py ni alohida process sifatida ishga tushiramiz.
            # Chiqish kodi 0 dan boshqa bo'lsa (xato/crash), qayta
            # urinib ko'ramiz - bu deploy paytidagi vaqtinchalik
            # TerminatedByOtherGetUpdates holatlarini ham qamrab oladi.
            result = subprocess.run(
                [sys.executable, "admin_bot.py"],
                check=False
            )
            logging.warning(
                f"[Admin bot] process tugadi (chiqish kodi: {result.returncode}). "
                f"5 soniyadan keyin qayta ishga tushiriladi."
            )
        except Exception as e:
            logging.error(f"[Admin bot] processni ishga tushirishda xato: {e}")
        time.sleep(5)


if __name__ == "__main__":
    import database as db
    db.init_db()

    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    admin_bot_thread = threading.Thread(target=run_admin_bot_process, daemon=True)
    admin_bot_thread.start()

    from bot import dp, bot
    from aiogram.utils import executor
    from aiogram.utils.exceptions import TerminatedByOtherGetUpdates

    # Deploy paytida eski va yangi instans bir lahza parallel ishlab qolishi
    # mumkin (Render zero-downtime deploy). Bu holatda Telegram eski
    # instansni "TerminatedByOtherGetUpdates" bilan to'xtatadi. Quyidagi
    # retry mexanizmi shu holatda botni avtomatik qayta tiklaydi.
    while True:
        try:
            logging.info("Asosiy bot polling boshlanmoqda...")
            executor.start_polling(dp, skip_updates=True)
            break
        except TerminatedByOtherGetUpdates:
            logging.warning(
                "Asosiy bot: TerminatedByOtherGetUpdates aniqlandi. "
                "5 soniyadan keyin qayta urinib ko'riladi."
            )
            time.sleep(5)
        except Exception as e:
            logging.error(f"Asosiy bot polling kutilmagan xato bilan to'xtadi: {e}")
            time.sleep(5)
