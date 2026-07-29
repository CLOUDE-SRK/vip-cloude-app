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


def keep_alive_ping():
    """MUHIM: Render'ning bepul tarifida servis 15 daqiqa davomida hech
    qanday TASHQI HTTP so'rov olmasa, butun konteynerni "uxlatib qo'yadi"
    (spin-down). Bizning holatimizda FastAPI, asosiy bot va admin bot —
    HAMMASI shu bitta process ichida ishlaydi, shuning uchun servis
    uxlab qolganda uchalasi ham birdek to'xtab qoladi. Bot Telegram'ga
    o'zi so'rov yuboradi (polling) — bu Render uchun "kiruvchi trafik"
    hisoblanmaydi, shuning uchun /start bosilganda ham servis
    "uyg'onmaydi".

    Yechim: servis o'zi-o'ziga (o'zining ochiq WEBAPP_URL manziliga)
    15 daqiqadan kamroq oraliqda HTTP so'rov yuborib turadi. Bu haqiqiy
    tashqi trafik sifatida hisoblanadi va Render hech qachon uxlab
    qolmaydi.

    DIQQAT: bu Render tomonidan rasman qo'llab-quvvatlanadigan usul
    emas (ular buning uchun pullik tarifni tavsiya qiladi), va servis
    doim tirik turgani uchun oyiga taxminan 720-750 soat sarflanadi —
    bu bepul workspace limitiga juda yaqin (yoki uni to'liq band qiladi).
    Agar shu Render hisobingizda boshqa bepul xizmatlar ham bo'lsa,
    ularga soat yetmay qolishi mumkin.
    """
    import httpx

    url = os.environ.get("WEBAPP_URL", "").strip()
    if not url:
        logging.warning("[KeepAlive] WEBAPP_URL sozlanmagan — o'z-o'ziga ping yuborilmaydi.")
        return

    # Server o'zining HTTP portini ochishi uchun bir oz kutamiz
    time.sleep(30)

    while True:
        try:
            httpx.get(url, timeout=20.0)
            logging.info("[KeepAlive] Ping muvaffaqiyatli yuborildi.")
        except Exception as e:
            logging.warning(f"[KeepAlive] Ping xato: {e}")
        time.sleep(600)  # 10 daqiqa — 15 daqiqalik limitdan kam


if __name__ == "__main__":
    import database as db
    db.init_db()


    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    admin_bot_thread = threading.Thread(target=run_admin_bot_process, daemon=True)
    admin_bot_thread.start()

    keep_alive_thread = threading.Thread(target=keep_alive_ping, daemon=True)
    keep_alive_thread.start()


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
