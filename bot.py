#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot - Python 3.11.0 Compatible
Instagram, TikTok, Shazam, YouTube Music Search
No cookies required – uses mobile client detection bypass
"""

import sys
import os
import asyncio
import tempfile
import subprocess
import hashlib
import re
import time
import signal
import logging
from importlib.metadata import version
from pathlib import Path
from typing import Optional, Dict, List
import http.server
import socketserver
import threading

from datetime import datetime

import telebot
from telebot import types
from telebot.apihelper import ApiException

from shazamio import Shazam
import yt_dlp

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ==================== CONFIG ====================
BOT_TOKEN = "8575775719:AAFk71ow9WR7crlONGpnP56qAZjO88Hj4eI"
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024
CLEANUP_INTERVAL = 600
PORT = int(os.environ.get("PORT", 8000))

user_sessions: Dict[int, Dict] = {}
bot_instance: Optional[telebot.TeleBot] = None

# ==================== BOT INITIALIZATION ====================
def init_bot() -> telebot.TeleBot:
    global bot_instance
    try:
        temp_bot = telebot.TeleBot(BOT_TOKEN)
        temp_bot.remove_webhook()
        logger.info("✅ Webhook o'chirildi")
    except Exception as e:
        logger.warning(f"⚠️ Webhook o'chirish xatosi: {e}")

    bot_instance = telebot.TeleBot(
        BOT_TOKEN,
        parse_mode=None,
        threaded=False,
        skip_pending=True
    )
    return bot_instance

bot = init_bot()

# ==================== DUMMY HTTP SERVER ====================
class DummyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def start_dummy_server():
    try:
        server = socketserver.TCPServer(("0.0.0.0", PORT), DummyHandler)
        logger.info(f"🌐 Dummy HTTP server ishga tushdi: 0.0.0.0:{PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Dummy server xatosi: {e}")

# ==================== YT-DLP CONFIGURATION (NO COOKIES) ====================
# Umumiy mobil foydalanuvchi agenti va botdan qochish sarlavhalari
MOBILE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.163 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'DNT': '1',
}

BASE_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'retries': 5,
    'fragment_retries': 5,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'prefer_insecure': True,
    'http_headers': MOBILE_HEADERS,
}

INSTAGRAM_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best',
    'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
    'extractor_args': {
        'instagram': {
            'app_id': ['com.instagram.android'],
            'webp': ['1'],
        }
    },
}

TIKTOK_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best',
    'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
    'extractor_args': {
        'tiktok': {
            'app_name': ['trill'],
            'app_version': ['28.1.3'],
        }
    },
}

AUDIO_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'bestaudio/best',
    'outtmpl': str(TEMP_DIR / 'audio_%(title)s.%(ext)s'),
    'restrictfilenames': True,
    'windowsfilenames': True,
    'socket_timeout': 600,
    'read_timeout': 600,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '196',
    }],
    'extractor_args': {
        'youtube': {
            # Muhim: bir nechta player_client sinab ko‘riladi, bot blokidan qochiladi
            'player_client': ['android', 'ios', 'web'],
            'skip': ['webpage'],
        }
    },
}

SEARCH_OPTIONS = {
    **BASE_OPTIONS,
    'extract_flat': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios', 'web'],
            'skip': ['webpage'],
        }
    },
}

# ==================== UTILITY FUNCTIONS ====================
def cleanup_old_files() -> None:
    try:
        current_time = time.time()
        deleted_count = 0
        for filepath in TEMP_DIR.iterdir():
            if filepath.is_file():
                file_age = current_time - filepath.stat().st_mtime
                if file_age > CLEANUP_INTERVAL:
                    filepath.unlink()
                    deleted_count += 1
        if deleted_count > 0:
            logger.info(f"🧹 {deleted_count} ta eski fayl o'chirildi")
    except Exception as e:
        logger.error(f"Cleanup xatosi: {e}")

def safe_delete(filepath: Optional[str | Path]) -> None:
    try:
        if filepath:
            path = Path(filepath)
            if path.exists() and path.is_file():
                path.unlink()
    except Exception as e:
        logger.debug(f"Delete xatosi: {e}")

def create_hash(text: str) -> str:
    return hashlib.md5(str(text).encode('utf-8')).hexdigest()[:12]

def clean_filename(text: str) -> str:
    if not text:
        return "audio"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text)
    text = re.sub(r'\s+', '_', text)
    text = text[:50]
    return text.strip('_') or "audio"

def format_duration(seconds: Optional[int | float]) -> str:
    try:
        total_seconds = int(float(seconds))
        minutes = total_seconds // 60
        secs = total_seconds % 60
        return f" ({minutes}:{secs:02d})"
    except (TypeError, ValueError):
        return ""

def is_instagram_url(url: str) -> bool:
    patterns = [r'instagram\.com/(p|reel|reels|tv)/', r'instagram\.com/stories/']
    url_lower = url.lower().strip()
    return any(re.search(pattern, url_lower) for pattern in patterns)

def is_tiktok_url(url: str) -> bool:
    patterns = [r'tiktok\.com/', r'vm\.tiktok\.com/', r'vt\.tiktok\.com/']
    url_lower = url.lower().strip()
    return any(re.search(pattern, url_lower) for pattern in patterns)

# ==================== SHAZAM RECOGNITION ====================
async def recognize_audio_async(audio_bytes: bytes) -> Dict:
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3', dir=TEMP_DIR) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name
        shazam = Shazam()
        result = await shazam.recognize(temp_path)
        if result and 'track' in result:
            track = result['track']
            return {
                'found': True,
                'title': track.get('title', 'Unknown'),
                'artist': track.get('subtitle', 'Unknown'),
            }
    except Exception as e:
        logger.error(f"Shazam xatosi: {e}")
    finally:
        if temp_file:
            safe_delete(temp_path)
    return {'found': False}

def recognize_audio(audio_bytes: bytes) -> Dict:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(recognize_audio_async(audio_bytes))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Async loop xatosi: {e}")
        return {'found': False}

# ==================== DOWNLOAD FUNCTIONS ====================
def download_youtube_audio(query: str, filename_hint: str = "") -> Optional[Path]:
    try:
        clean_name = clean_filename(filename_hint or query)
        output_path = TEMP_DIR / f"audio_{clean_name}.mp3"
        options = AUDIO_OPTIONS.copy()
        options['outtmpl'] = str(TEMP_DIR / f"audio_{clean_name}.%(ext)s")

        if re.match(r'https?://', query):
            download_url = query
        else:
            download_url = f"ytsearch1:{query}"

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([download_url])

        if output_path.exists():
            file_size = output_path.stat().st_size
            logger.info(f"✅ Audio yuklandi: {file_size / (1024*1024):.2f} MB")
            return output_path

        mp3_files = sorted(
            TEMP_DIR.glob('audio_*.mp3'),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        if mp3_files and (time.time() - mp3_files[0].stat().st_mtime) < 120:
            file_size = mp3_files[0].stat().st_size
            logger.info(f"✅ Audio topildi: {file_size / (1024*1024):.2f} MB")
            return mp3_files[0]
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}")
    return None

def extract_audio_from_video(video_path: str | Path, duration: int = 10) -> Optional[Path]:
    try:
        video_path = Path(video_path)
        audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
        command = [
            'ffmpeg', '-i', str(video_path), '-t', str(duration),
            '-vn', '-acodec', 'mp3', '-ar', '44100', '-ab', '128k', '-y', str(audio_path)
        ]
        subprocess.run(command, capture_output=True, timeout=60, check=False)
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
    except Exception as e:
        logger.error(f"Audio extraction xatosi: {e}")
    return None

# ==================== MESSAGE HANDLERS ====================
# (qolgan handler funksiyalar avvalgi versiyadagi kabi, faqat yuklash/xatolik logikasi
#  yuqoridagi yangi sozlamalardan foydalanadi)
# -------------------------------------------------------------------
# Pastdagi barcha handlerlar o'zgarishsiz qoldirildi, ular yangi global
# AUDIO_OPTIONS, SEARCH_OPTIONS dan foydalanadi.

@bot.message_handler(commands=['start', 'help'])
def start_command(message: types.Message) -> None:
    cleanup_old_files()
    welcome_text = (
        "👋 *Salom! Musiqa topuvchi botman* 🎵\n\n"
        "📱 *Instagram/TikTok* linki yuboring\n"
        "🎤 *Qo'shiq* yoki *ijrochi* nomini yozing\n"
        "🎵 *Audio* fayl yuboring (aniqlash uchun)\n\n"
        "👨‍💻 Dasturchi: @Rustamov_v1"
    )
    try:
        bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')
    except:
        bot.send_message(message.chat.id, welcome_text.replace('*', ''))

@bot.message_handler(content_types=['audio', 'voice'])
def handle_audio_message(message: types.Message) -> None:
    status_msg = None
    audio_file_path = None
    try:
        status_msg = bot.reply_to(message, "🎵 Musiqa aniqlanmoqda...")
        file_id = message.audio.file_id if message.audio else message.voice.file_id
        file_info = bot.get_file(file_id)
        audio_data = bot.download_file(file_info.file_path)
        result = recognize_audio(audio_data)
        if not result['found']:
            bot.edit_message_text("❌ Musiqa tanilmadi\n\nBoshqa audio yuboring yoki qo'shiq nomini yozing",
                                  message.chat.id, status_msg.message_id)
            return
        title = result['title']
        artist = result['artist']
        bot.edit_message_text(f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...",
                              message.chat.id, status_msg.message_id)
        query = f"{artist} {title}"
        audio_file_path = download_youtube_audio(query, f"{artist}_{title}")
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(message.chat.id, audio_file, title=title[:64],
                               performer=artist[:64], caption=f"🎵 {title}\n👤 {artist}")
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
            bot.edit_message_text(f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi, qayta urinib ko'ring",
                                  message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi", message.chat.id, status_msg.message_id)
            except:
                pass
    finally:
        safe_delete(audio_file_path)

@bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
def handle_instagram(message: types.Message) -> None:
    status_msg = None
    video_path = None
    try:
        url = message.text.strip().split('?')[0]
        status_msg = bot.reply_to(message, "⏳")
        ydl_opts = {**INSTAGRAM_OPTIONS, 'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s')}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
        video_files = list(TEMP_DIR.glob(f"ig_{video_id}*"))
        if not video_files:
            video_files = sorted(list(TEMP_DIR.glob('ig_*.mp4')) + list(TEMP_DIR.glob('ig_*.webm')),
                                 key=lambda f: f.stat().st_mtime, reverse=True)
        if not video_files:
            bot.edit_message_text("❌ Video yuklanmadi\n\nSabablar:\n• Link noto'g'ri\n• Video private\n• Instagram blok qilgan",
                                  message.chat.id, status_msg.message_id)
            return
        video_path = video_files[0]
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y'],
                               capture_output=True, timeout=60, check=True)
                safe_delete(video_path)
                video_path = mp4_path
            except:
                pass
        if video_path.stat().st_size > MAX_FILE_SIZE:
            size_mb = video_path.stat().st_size / (1024*1024)
            bot.edit_message_text(f"❌ Video juda katta ({size_mb:.1f} MB)\nTelegram limit: 50 MB",
                                  message.chat.id, status_msg.message_id)
            return
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{btn_hash}"))
        with open(video_path, 'rb') as video_file:
            bot.send_video(message.chat.id, video_file, reply_markup=markup,
                           caption="📱 Instagram", supports_streaming=True, timeout=120)
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        bot.delete_message(message.chat.id, status_msg.message_id)
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if status_msg:
            if "Private" in error_msg or "login" in error_msg.lower():
                msg = "❌ Bu video private (shaxsiy)"
            elif "unavailable" in error_msg.lower():
                msg = "❌ Video mavjud emas"
            else:
                msg = "❌ Instagram video yuklanmadi\n\nQayta urinib ko'ring"
            bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"Instagram xatosi: {e}")
        if status_msg:
            bot.edit_message_text("❌ Video yuklanmadi", message.chat.id, status_msg.message_id)
    finally:
        if video_path:
            def delayed_delete():
                time.sleep(60)
                safe_delete(video_path)
            threading.Thread(target=delayed_delete, daemon=True).start()

@bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
def handle_tiktok(message: types.Message) -> None:
    status_msg = None
    video_path = None
    try:
        url = message.text.strip()
        status_msg = bot.reply_to(message, "📱 TikTok yuklanmoqda...")
        ydl_opts = {**TIKTOK_OPTIONS, 'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s')}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
        video_files = list(TEMP_DIR.glob(f"tt_{video_id}*"))
        if not video_files:
            video_files = sorted(list(TEMP_DIR.glob('tt_*.mp4')) + list(TEMP_DIR.glob('tt_*.webm')),
                                 key=lambda f: f.stat().st_mtime, reverse=True)
        if not video_files:
            bot.edit_message_text("❌ TikTok video yuklanmadi\n\nSabablar:\n• Link noto'g'ri\n• Video private\n• TikTok blok qilgan",
                                  message.chat.id, status_msg.message_id)
            return
        video_path = video_files[0]
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y'],
                               capture_output=True, timeout=60, check=True)
                safe_delete(video_path)
                video_path = mp4_path
            except:
                pass
        if video_path.stat().st_size > MAX_FILE_SIZE:
            size_mb = video_path.stat().st_size / (1024*1024)
            bot.edit_message_text(f"❌ Video juda katta ({size_mb:.1f} MB)\nTelegram limit: 50 MB",
                                  message.chat.id, status_msg.message_id)
            return
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{btn_hash}"))
        with open(video_path, 'rb') as video_file:
            bot.send_video(message.chat.id, video_file, reply_markup=markup,
                           caption="📱 TikTok", supports_streaming=True, timeout=120)
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        bot.delete_message(message.chat.id, status_msg.message_id)
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if status_msg:
            if "Private" in error_msg or "login" in error_msg.lower():
                msg = "❌ Bu video private (shaxsiy)"
            elif "unavailable" in error_msg.lower():
                msg = "❌ Video mavjud emas"
            else:
                msg = "❌ TikTok video yuklanmadi\n\nQayta urinib ko'ring"
            bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"TikTok xatosi: {e}")
        if status_msg:
            bot.edit_message_text("❌ TikTok yuklanmadi", message.chat.id, status_msg.message_id)
    finally:
        if video_path:
            def delayed_delete():
                time.sleep(60)
                safe_delete(video_path)
            threading.Thread(target=delayed_delete, daemon=True).start()

@bot.callback_query_handler(func=lambda c: c.data.startswith('music_'))
def handle_video_music_recognition(call: types.CallbackQuery) -> None:
    audio_path = None
    video_path = None
    audio_file_path = None
    try:
        btn_hash = call.data.split('_')[1]
        bot.answer_callback_query(call.id, "🎵 Musiqa aniqlanmoqda...")
        path_file = TEMP_DIR / f"{btn_hash}.path"
        if not path_file.exists():
            bot.send_message(call.message.chat.id, "❌ Video topilmadi (vaqt o'tgan)")
            return
        video_path = path_file.read_text().strip()
        if not Path(video_path).exists():
            bot.send_message(call.message.chat.id, "❌ Video fayl o'chirilgan")
            return
        audio_path = extract_audio_from_video(video_path, 10)
        if not audio_path or not audio_path.exists():
            bot.send_message(call.message.chat.id, "❌ Audio ajratilmadi")
            return
        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        result = recognize_audio(audio_data)
        if not result['found']:
            bot.send_message(call.message.chat.id, "❌ Musiqa tanilmadi")
            return
        title = result['title']
        artist = result['artist']
        bot.send_message(call.message.chat.id, f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...")
        query = f"{artist} {title}"
        audio_file_path = download_youtube_audio(query, f"{artist}_{title}")
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(call.message.chat.id, audio_file, title=title[:64],
                               performer=artist[:64], caption=f"🎵 {title}\n👤 {artist}")
        else:
            bot.send_message(call.message.chat.id, f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi")
    except Exception as e:
        logger.error(f"Video music recognition xatosi: {e}")
        bot.send_message(call.message.chat.id, "❌ Xatolik yuz berdi")
    finally:
        safe_delete(audio_path)
        safe_delete(audio_file_path)
        if video_path:
            safe_delete(video_path)

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_search(message: types.Message) -> None:
    status_msg = None
    try:
        query = message.text.strip()
        status_msg = bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")
        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch50:{query}", download=False)
            songs = info.get('entries', [])
        if not songs:
            bot.edit_message_text("❌ Hech narsa topilmadi\n\nBoshqa nom bilan qidiring",
                                  message.chat.id, status_msg.message_id)
            return
        user_sessions[message.chat.id] = {
            'query': query,
            'songs': songs,
            'page': 0,
            'timestamp': datetime.now()
        }
        show_search_results(message.chat.id, 0)
        bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg:
            bot.edit_message_text("❌ Qidiruvda xatolik", message.chat.id, status_msg.message_id)

def show_search_results(chat_id: int, page: int = 0) -> None:
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Sessiya muddati tugagan\n\nYangi qidiruv bering")
        return
    query = session['query']
    songs = session['songs']
    total_songs = len(songs)
    page_size = 10
    total_pages = (total_songs + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_songs)
    page_songs = songs[start_idx:end_idx]

    text_lines = [f"🔍 *{query}*", f"📄 Sahifa: {page + 1}/{total_pages} | Jami: {total_songs} ta", ""]
    markup = types.InlineKeyboardMarkup(row_width=5)
    button_rows = []
    current_row = []
    for idx, song in enumerate(page_songs, start=1):
        global_idx = start_idx + idx
        title = song.get('title', 'Nomaʼlum')[:45]
        duration = format_duration(song.get('duration'))
        text_lines.append(f"{global_idx}. {title}{duration}")
        h = create_hash(f"{title}_{global_idx}")
        (TEMP_DIR / f"song_{h}.txt").write_text(f"{title}|{global_idx}")
        btn = types.InlineKeyboardButton(str(global_idx), callback_data=f"dl_{h}")
        current_row.append(btn)
        if len(current_row) == 5:
            button_rows.append(current_row)
            current_row = []
    if current_row:
        button_rows.append(current_row)
    for row in button_rows:
        markup.add(*row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page_{page-1}"))
    nav_buttons.append(types.InlineKeyboardButton("❌", callback_data="close_page"))
    if page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("Keyingi ➡️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)
    markup.row(
        types.InlineKeyboardButton("🔄 Yangi qidiruv", callback_data="nav_new"),
        types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="nav_home")
    )
    user_sessions[chat_id]['page'] = page
    try:
        bot.send_message(chat_id, "\n".join(text_lines), reply_markup=markup, parse_mode='Markdown')
    except:
        bot.send_message(chat_id, "\n".join(text_lines).replace('*', ''), reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith('page_'))
def handle_page_navigation(call: types.CallbackQuery) -> None:
    try:
        page = int(call.data.split('_')[1])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_search_results(call.message.chat.id, page)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Page navigation xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data == "close_page")
def handle_close_page(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Sahifa yopildi")
        if call.message.chat.id in user_sessions:
            del user_sessions[call.message.chat.id]
    except Exception as e:
        logger.error(f"Close page xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
def handle_song_download(call: types.CallbackQuery) -> None:
    audio_file_path = None
    callback_answered = False
    try:
        btn_hash = call.data.split('_')[1]
        data_file = TEMP_DIR / f"song_{btn_hash}.txt"
        if not data_file.exists():
            bot.answer_callback_query(call.id, "❌ Vaqt o'tgan", show_alert=True)
            callback_answered = True
            return
        data = data_file.read_text().strip()
        parts = data.split('|', 1)
        title = parts[0] if parts else 'Audio'
        bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")
        callback_answered = True
        audio_file_path = download_youtube_audio(title, title)
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(call.message.chat.id, audio_file, title=title[:64],
                               caption=f"✅ {title}", timeout=300)
        else:
            bot.send_message(call.message.chat.id, "❌ Yuklashda xatolik\n\nQayta urinib ko'ring")
        safe_delete(data_file)
    except Exception as e:
        logger.error(f"Download xatosi: {e}")
        if not callback_answered:
            try:
                bot.answer_callback_query(call.id, "❌ Xatolik yuz berdi", show_alert=True)
            except:
                pass
    finally:
        safe_delete(audio_file_path)

@bot.callback_query_handler(func=lambda c: c.data.startswith('nav_'))
def handle_navigation(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    if call.data == 'nav_home':
        start_command(call.message)
    elif call.data == 'nav_new':
        bot.send_message(call.message.chat.id, "🔍 Yangi qidiruv uchun qo'shiq yoki ijrochi nomini yozing:")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_unknown(message: types.Message) -> None:
    if not message.text.startswith('/'):
        handle_search(message)

def shutdown_handler(signum, frame) -> None:
    logger.info("\n🛑 Bot to'xtatilmoqda...")
    try:
        cleanup_old_files()
        bot.stop_polling()
    except Exception as e:
        logger.error(f"Shutdown xatosi: {e}")
    logger.info("✅ Bot to'xtatildi")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

def start_periodic_cleanup() -> None:
    def cleanup_loop():
        while True:
            try:
                time.sleep(CLEANUP_INTERVAL)
                cleanup_old_files()
            except Exception as e:
                logger.error(f"Cleanup loop xatosi: {e}")
    threading.Thread(target=cleanup_loop, daemon=True).start()
    logger.info("🧹 Davriy tozalash yoqildi")

def main() -> None:
    logger.info("=" * 60)
    logger.info("🎵 TELEGRAM MUSIC BOT (No cookies required)")
    logger.info("=" * 60)
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"📦 pyTelegramBotAPI: {version('pyTelegramBotAPI')}")
    logger.info(f"📁 Temp katalog: {TEMP_DIR.absolute()}")
    logger.info("=" * 60)
    logger.info("✅ Bot ishga tushdi!")
    logger.info("⚡ YouTube bot blokisiz ishlaydi (mobil klientlar bilan)")
    logger.info("📱 Instagram, TikTok, Shazam, YouTube")
    logger.info("=" * 60)

    cleanup_old_files()
    start_periodic_cleanup()

    server_thread = threading.Thread(target=start_dummy_server, daemon=True)
    server_thread.start()

    try:
        logger.info("🔄 Polling boshlandi...")
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30, none_stop=True)
    except KeyboardInterrupt:
        shutdown_handler(None, None)
    except Exception as e:
        logger.error(f"❌ Fatal xatolik: {e}")
        logger.info("🔄 3 soniyadan keyin qayta ishga tushiriladi...")
        time.sleep(3)
        try:
            bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30, none_stop=True)
        except Exception as e2:
            logger.error(f"❌ Qayta urinish muvaffaqiyatsiz: {e2}")
            sys.exit(1)

if __name__ == '__main__':
    main()
