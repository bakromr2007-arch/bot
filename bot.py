import os
import sys
import re
import logging
import subprocess
import tempfile
import asyncio
import aiohttp
from pathlib import Path
from urllib.parse import urlparse, unquote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8522256341:AAGTDzFspSqojiTiJFLLUl8bBSQ2uoYqo8")

MAX_TG_SIZE  = 20  * 1024 * 1024   # 20 MB  — Telegram fayl yuklash limiti
MAX_URL_SIZE = 100 * 1024 * 1024   # 100 MB — URL orqali yuklash limiti

CONV_MAP = {
    "pdf":  ["docx", "pptx", "xlsx"],
    "docx": ["pdf", "pptx"],
    "pptx": ["pdf", "docx"],
    "xlsx": ["pdf", "csv"],
    "doc":  ["pdf", "docx"],
    "xls":  ["pdf", "xlsx"],
    "csv":  ["xlsx", "pdf"],
    "odt":  ["pdf", "docx"],
    "odp":  ["pdf", "pptx"],
    "ods":  ["pdf", "xlsx"],
}

FMT_EMOJI = {
    "pdf": "📄", "docx": "📝", "pptx": "📊",
    "xlsx": "📈", "csv": "📋", "doc": "📝",
    "xls": "📈", "odt": "📝", "odp": "📊", "ods": "📈",
}

# user_id -> {source: "file"|"url", ...}
user_files = {}


# ──────────────────────────────────────────────
# LibreOffice yo'lini topish (Win + Linux)
# ──────────────────────────────────────────────
def get_libreoffice() -> str:
    if sys.platform == "win32":
        for p in [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]:
            if Path(p).exists():
                return p
        raise FileNotFoundError("LibreOffice topilmadi: https://www.libreoffice.org/download/download/")
    for cmd in ["libreoffice", "soffice"]:
        r = subprocess.run(["which", cmd], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    raise FileNotFoundError("LibreOffice topilmadi. `apt install libreoffice` qiling.")

try:
    LIBREOFFICE = get_libreoffice()
    logger.info(f"LibreOffice: {LIBREOFFICE}")
except FileNotFoundError as e:
    logger.error(str(e))
    LIBREOFFICE = None


# ──────────────────────────────────────────────
# Google Drive / Dropbox havolalarini to'g'ri linkka o'tkazish
# ──────────────────────────────────────────────
def normalize_url(url: str) -> str:
    # Google Drive: /file/d/FILE_ID/view → /uc?export=download&id=FILE_ID
    gdrive = re.match(r"https://drive\.google\.com/file/d/([^/]+)", url)
    if gdrive:
        return f"https://drive.google.com/uc?export=download&id={gdrive.group(1)}"

    # Dropbox: ?dl=0 → ?dl=1
    if "dropbox.com" in url:
        return re.sub(r"[?&]dl=0", "?dl=1", url)

    return url


def guess_ext_from_url(url: str, content_type: str = "") -> str:
    """URL yoki Content-Type dan kengaytma aniqlash"""
    path = unquote(urlparse(url).path)
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in CONV_MAP:
        return ext

    # Content-Type orqali
    ct_map = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/msword": "doc",
        "text/csv": "csv",
    }
    for ct, e in ct_map.items():
        if ct in content_type:
            return e
    return ext


# ──────────────────────────────────────────────
# /start va /help
# ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lo = "✅ LibreOffice tayyor" if LIBREOFFICE else "❌ LibreOffice o'rnatilmagan!"
    await update.message.reply_text(
        "👋 Salom! Men *Fayl Konverter* botman.\n\n"
        "📎 *Ikki usulda fayl yuborishingiz mumkin:*\n\n"
        "1️⃣ *To'g'ridan fayl* — 20 MB gacha\n"
        "   Telegramga fayl biriktiring va yuboring\n\n"
        "2️⃣ *Havola (URL)* — 100 MB gacha 🔥\n"
        "   Google Drive, Dropbox yoki to'g'ri link yuboring\n"
        "   Misol: `https://drive.google.com/file/d/ABC.../view`\n\n"
        "📋 *Formatlar:*\n"
        "• PDF ↔ DOCX, PPTX, XLSX\n"
        "• DOCX → PDF, PPTX\n"
        "• PPTX → PDF, DOCX\n"
        "• XLSX → PDF, CSV\n\n"
        f"{lo}\n\n"
        "Boshlash uchun fayl yuboring yoki link yozing! 🚀",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Qo'llanma:*\n\n"
        "*Usul 1 — Fayl (20 MB gacha):*\n"
        "Faylni bevosita chatga yuboring\n\n"
        "*Usul 2 — Havola (100 MB gacha):*\n"
        "• Google Drive havolasini yuboring\n"
        "  (`drive.google.com/file/d/...`)\n"
        "• Dropbox havolasini yuboring\n"
        "• Yoki boshqa to'g'ri fayl linkini yuboring\n\n"
        "⚠️ Google Drive da fayl *\"Havola orqali ko'rish\"* rejimida bo'lishi kerak",
        parse_mode="Markdown"
    )


# ──────────────────────────────────────────────
# Fayl yuborilganda (20 MB gacha)
# ──────────────────────────────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LIBREOFFICE:
        await update.message.reply_text("❌ Server xatosi: LibreOffice o'rnatilmagan.")
        return

    doc = update.message.document
    file_name = doc.file_name or "file"
    ext = Path(file_name).suffix.lower().lstrip(".")

    if doc.file_size and doc.file_size > MAX_TG_SIZE:
        size_mb = doc.file_size / 1024 / 1024
        await update.message.reply_text(
            f"❌ *Fayl juda katta ({size_mb:.1f} MB)*\n\n"
            f"Telegram orqali max *20 MB* yuborishingiz mumkin.\n\n"
            f"*100 MB gacha fayllar uchun:*\n"
            f"Faylni Google Drive ga yuklang va havolasini yuboring:\n"
            f"`https://drive.google.com/file/d/.../view`",
            parse_mode="Markdown"
        )
        return

    if ext not in CONV_MAP:
        await update.message.reply_text(
            f"❌ *.{ext}* formati qo'llab-quvvatlanmaydi.\n\n"
            f"Qabul qilinadiganlar: {', '.join(f'.{k}' for k in CONV_MAP)}",
            parse_mode="Markdown"
        )
        return

    user_files[update.effective_user.id] = {
        "source": "file",
        "file_id": doc.file_id,
        "file_name": file_name,
        "ext": ext,
    }
    await send_target_keyboard(update, file_name, ext, doc.file_size or 0)


# ──────────────────────────────────────────────
# Matn (URL) yuborilganda
# ──────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # URL emasmi?
    if not text.startswith("http://") and not text.startswith("https://"):
        await update.message.reply_text(
            "📎 Fayl yuboring yoki havola (URL) yozing.\n\n"
            "Misol:\n`https://drive.google.com/file/d/ABC.../view`",
            parse_mode="Markdown"
        )
        return

    if not LIBREOFFICE:
        await update.message.reply_text("❌ Server xatosi: LibreOffice o'rnatilmagan.")
        return

    wait_msg = await update.message.reply_text("🔍 Havola tekshirilmoqda...")

    try:
        real_url = normalize_url(text)

        async with aiohttp.ClientSession() as session:
            async with session.head(real_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                content_type = resp.headers.get("Content-Type", "")
                content_length = int(resp.headers.get("Content-Length", 0))
                final_url = str(resp.url)

        ext = guess_ext_from_url(final_url, content_type)

        if not ext or ext not in CONV_MAP:
            await wait_msg.edit_text(
                f"❌ Fayl formati aniqlanmadi yoki qo'llab-quvvatlanmaydi.\n\n"
                f"Qabul qilinadiganlar: {', '.join(f'.{k}' for k in CONV_MAP)}\n\n"
                f"Havola to'g'ridan-to'g'ri fayl bo'lishi kerak.",
            )
            return

        if content_length and content_length > MAX_URL_SIZE:
            size_mb = content_length / 1024 / 1024
            await wait_msg.edit_text(
                f"❌ Fayl juda katta ({size_mb:.1f} MB)\n"
                f"Maksimal: 100 MB"
            )
            return

        size_mb = content_length / 1024 / 1024 if content_length else 0
        file_name = Path(unquote(urlparse(final_url).path)).name or f"file.{ext}"
        if not file_name.endswith(f".{ext}"):
            file_name = f"file.{ext}"

        user_files[update.effective_user.id] = {
            "source": "url",
            "url": real_url,
            "file_name": file_name,
            "ext": ext,
        }

        await wait_msg.delete()
        await send_target_keyboard(update, file_name, ext, content_length)

    except aiohttp.ClientError as e:
        await wait_msg.edit_text(f"❌ Havolaga ulanib bo'lmadi.\n\nXato: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"URL check error: {e}")
        await wait_msg.edit_text(f"❌ Xatolik: {str(e)[:150]}")


# ──────────────────────────────────────────────
# Format tanlash klaviaturasini yuborish
# ──────────────────────────────────────────────
async def send_target_keyboard(update: Update, file_name: str, ext: str, size_bytes: int):
    targets = CONV_MAP[ext]
    keyboard = []
    row = []
    for i, t in enumerate(targets):
        row.append(InlineKeyboardButton(f"{FMT_EMOJI.get(t,'📄')} {t.upper()}", callback_data=f"convert_{t}"))
        if len(row) == 2 or i == len(targets) - 1:
            keyboard.append(row); row = []
    keyboard.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])

    size_str = f"{size_bytes/1024/1024:.1f} MB" if size_bytes else "noma'lum"
    await update.message.reply_text(
        f"{FMT_EMOJI.get(ext,'📄')} *{file_name}* qabul qilindi!\n"
        f"📦 Hajm: {size_str}\n\n"
        f"Qaysi formatga o'tkazish kerak?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ──────────────────────────────────────────────
# Tugma bosilganda — konvertatsiya
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "cancel":
        user_files.pop(user_id, None)
        await query.edit_message_text("✅ Bekor qilindi.")
        return

    if not query.data.startswith("convert_"):
        return

    target_fmt = query.data.replace("convert_", "")
    info = user_files.get(user_id)
    if not info:
        await query.edit_message_text("❌ Fayl topilmadi. Qayta yuboring.")
        return

    await query.edit_message_text(
        f"⏳ *{info['file_name']}* → *{target_fmt.upper()}*\n\nKonvertatsiya qilinmoqda...",
        parse_mode="Markdown"
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / info["file_name"]

            if info["source"] == "file":
                tg_file = await context.bot.get_file(info["file_id"])
                file_bytes = await tg_file.download_as_bytearray()
                src_path.write_bytes(file_bytes)

            elif info["source"] == "url":
                await query.edit_message_text(
                    f"⬇️ Fayl yuklanmoqda...\n_{info['file_name']}_",
                    parse_mode="Markdown"
                )
                async with aiohttp.ClientSession() as session:
                    async with session.get(info["url"], timeout=aiohttp.ClientTimeout(total=180)) as resp:
                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}: Fayl yuklab bo'lmadi")
                        total = 0
                        with open(src_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 512):
                                f.write(chunk)
                                total += len(chunk)
                                if total > MAX_URL_SIZE:
                                    raise Exception("Fayl 100 MB dan oshib ketdi!")

                await query.edit_message_text(
                    f"⚙️ Konvertatsiya qilinmoqda...\n_{info['file_name']} → {target_fmt.upper()}_",
                    parse_mode="Markdown"
                )

            result_path = convert_file(src_path, target_fmt, tmpdir)

            if not result_path:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Konvertatsiya muvaffaqiyatsiz.\nFayl buzilgan yoki format mos kelmayapti."
                )
                return

            out_name = Path(info["file_name"]).stem + "." + target_fmt
            result_size = result_path.stat().st_size

            if result_size > MAX_TG_SIZE:
                # Natija ham katta bo'lsa — xabar ber
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=(
                        f"⚠️ Konvertatsiya muvaffaqiyatli, lekin natija fayl\n"
                        f"*{result_size/1024/1024:.1f} MB* — Telegram orqali yuborib bo'lmaydi (max 50 MB).\n\n"
                        f"Faylni kichiklashtirish kerak."
                    ),
                    parse_mode="Markdown"
                )
                return

            with open(result_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=out_name,
                    caption=(
                        f"{FMT_EMOJI.get(info['ext'],'📄')} {info['ext'].upper()} → "
                        f"{FMT_EMOJI.get(target_fmt,'📄')} {target_fmt.upper()}\n"
                        f"✅ Muvaffaqiyatli!"
                    )
                )

    except Exception as e:
        err = str(e)
        logger.error(f"Convert error: {err}")
        if "100 MB" in err:
            msg = "❌ Fayl 100 MB dan katta, yuklab bo'lmadi."
        elif "HTTP" in err:
            msg = f"❌ Fayl yuklab bo'lmadi.\n{err}"
        elif "File is too big" in err:
            msg = "❌ Fayl 20 MB dan katta. Havola orqali yuboring."
        elif "timed out" in err.lower():
            msg = "❌ Vaqt tugadi. Internet sekin yoki fayl juda katta."
        else:
            msg = f"❌ Xatolik: `{err[:200]}`"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode="Markdown")
    finally:
        user_files.pop(user_id, None)


# ──────────────────────────────────────────────
# LibreOffice konvertatsiya
# ──────────────────────────────────────────────
def convert_file(src_path: Path, target_fmt: str, output_dir: str) -> Path | None:
    fmt_map = {"docx": "docx", "doc": "docx", "xlsx": "xlsx", "xls": "xlsx",
               "pptx": "pptx", "pdf": "pdf", "csv": "csv"}
    lo_fmt = fmt_map.get(target_fmt, target_fmt)

    cmd = [LIBREOFFICE, "--headless", "--convert-to", lo_fmt, "--outdir", output_dir, str(src_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if proc.returncode != 0:
        logger.error(f"LibreOffice error: {proc.stderr}")
        return None

    result = Path(output_dir) / f"{src_path.stem}.{lo_fmt}"
    if result.exists():
        return result

    for f in Path(output_dir).iterdir():
        if f.suffix.lower() == f".{lo_fmt}" and f != src_path:
            return f
    return None


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN o'rnatilmagan!")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
