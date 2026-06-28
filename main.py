import threading
import uvicorn
import os
import time
import logging
import asyncio

logging.basicConfig(level=logging.INFO)


def run_api():
    uvicorn.run(
        "webapp_api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )


def run_admin_bot():
    """Admin bot alohida thread'da, alohida event loop bilan ishlaydi.
    Agar ADMIN_BOT_TOKEN sozlanmagan bo'lsa, bu thread shunchaki
    hech narsa qilmay to'xtaydi - asosiy bot va API ishlashda davom etadi."""
    if not os.environ.get("ADMIN_BOT_TOKEN"):
        logging.warning("ADMIN_BOT_TOKEN sozlanmagan - admin bot ishga tushmaydi.")
        return

    from aiogram.utils import executor as admin_executor
    from aiogram.utils.exceptions import TerminatedByOtherGetUpdates
    from admin_bot import admin_dp

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            logging.info("Admin bot polling boshlanmoqda...")
            admin_executor.start_polling(admin_dp, skip_updates=True)
            break
        except TerminatedByOtherGetUpdates:
            logging.warning("Admin bot: TerminatedByOtherGetUpdates, 5s dan keyin qayta urinish.")
            time.sleep(5)
        except Exception as e:
            logging.error(f"Admin bot kutilmagan xato bilan to'xtadi: {e}")
            time.sleep(5)


if __name__ == "__main__":
    import database as db
    db.init_db()

    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    admin_bot_thread = threading.Thread(target=run_admin_bot, daemon=True)
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
