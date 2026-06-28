import threading
import uvicorn
import os
import time
import logging

logging.basicConfig(level=logging.INFO)

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
    from aiogram.utils.exceptions import TerminatedByOtherGetUpdates

    # Deploy paytida eski va yangi instans bir lahza parallel ishlab qolishi
    # mumkin (Render zero-downtime deploy). Bu holatda Telegram eski
    # instansni "TerminatedByOtherGetUpdates" bilan to'xtatadi. Quyidagi
    # retry mexanizmi shu holatda botni avtomatik qayta tiklaydi, shu
    # bilan birga eski instansni ham xotirjam to'xtatadi (chunki u retry
    # qilmaydi va process tugaydi, faqat yangisi qoladi).
    while True:
        try:
            logging.info("Polling boshlanmoqda...")
            executor.start_polling(dp, skip_updates=True)
            break  # start_polling normal yopilsa, chiqamiz
        except TerminatedByOtherGetUpdates:
            logging.warning(
                "TerminatedByOtherGetUpdates: boshqa instans aniqlandi. "
                "5 soniyadan keyin qayta urinib ko'riladi."
            )
            time.sleep(5)
        except Exception as e:
            logging.error(f"Polling kutilmagan xato bilan to'xtadi: {e}")
            time.sleep(5)
            
