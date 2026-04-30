#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot - Python 3.11.0 Compatible
Instagram, TikTok, Shazam, YouTube Music Search
Optimized for Render Cloud
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
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

# Telegram Bot
import telebot
from telebot import types
from telebot.apihelper import ApiException

# Music Recognition
from shazamio import Shazam

# Video/Audio Download
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

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit
CLEANUP_INTERVAL = 600  # 10 minutes

# ==================== GLOBAL STATE ====================
user_sessions: Dict[int, Dict] = {}
bot_instance: Optional[telebot.TeleBot] = None

# ==================== RENDER KEEP-ALIVE SERVER ====================
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running successfully on Render!")

    def log_message(self, format, *args):
        pass  # Server loglarini o'chirib qo'yish

def start_dummy_server():
    """Render port tekshiruvidan o'tish uchun mitti server"""
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    logger.info(f"✅ Render Web Server {port}-portda ishga tushdi")

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

# ==================== YT-DLP CONFIGURATION ====================
BASE_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'retries': 3,
    'fragment_retries': 3,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'prefer_insecure': True,
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
}

SEARCH_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'socket_timeout': 20,
}

# ==================== UTILITY FUNCTIONS ====================
def cleanup_old_files() -> None:
    try:
        current_time = time.time()
        deleted_count = 0
        for filepath in TEMP_DIR.iterdir():
            if filepath.is_file():
                if current_time - filepath.stat().st_mtime > CLEANUP_INTERVAL:
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
    if not text: return "audio"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text)
    text = re.sub(r'\s+', '_', text)
    return text[:50].strip('_') or "audio"

def format_duration(seconds: Optional[int | float]) -> str:
    try:
        total_seconds = int(float(seconds))
        return f" ({total_seconds // 60}:{total_seconds % 60:02d})"
    except (TypeError, ValueError):
        return ""

def is_instagram_url(url: str) -> bool:
    patterns = [r'instagram\.com/(p|reel|reels|tv)/', r'instagram\.com/stories/']
    return any(re.search(pattern, url.lower().strip()) for pattern in patterns)

def is_tiktok_url(url: str) -> bool:
    patterns = [r'tiktok\.com/', r'vm\.tiktok\.com/', r'vt\.tiktok\.com/']
    return any(re.search(pattern, url.lower().strip()) for pattern in patterns)

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
            return {
                'found': True,
                'title': result['track'].get('title', 'Unknown'),
                'artist': result['track'].get('subtitle', 'Unknown'),
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
        
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([f"ytsearch1:{query}"])
        
        if output_path.exists():
            return output_path
        
        mp3_files = sorted(TEMP_DIR.glob('audio_*.mp3'), key=lambda f: f.stat().st_mtime, reverse=True)
        if mp3_files and (time.time() - mp3_files[0].stat().st_mtime) < 120:
            return mp3_files[0]
            
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}")
    return None

def extract_audio_from_video(video_path: str | Path, duration: int = 10) -> Optional[Path]:
    try:
        video_path = Path(video_path)
        audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
        command = [
            'ffmpeg', '-i', str(video_path), '-t', str(duration), '-vn',
            '-acodec', 'mp3', '-ar', '44100', '-ab', '128k', '-y', str(audio_path)
        ]
        subprocess.run(command, capture_output=True, timeout=60, check=False)
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
    except Exception as e:
        logger.error(f"Audio extraction xatosi: {e}")
    return None

# ==================== MESSAGE HANDLERS ====================
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

# ==================== AUDIO/VOICE HANDLER ====================
@bot.message_handler(content_types=['audio', 'voice'])
def handle_audio_message(message: types.Message) -> None:
    status_msg = audio_file_path = None
    try:
        status_msg = bot.reply_to(message, "🎵 Musiqa aniqlanmoqda...")
        file_id = message.audio.file_id if message.audio else message.voice.file_id
        file_info = bot.get_file(file_id)
        audio_data = bot.download_file(file_info.file_path)
        
        result = recognize_audio(audio_data)
        if not result['found']:
            bot.edit_message_text("❌ Musiqa tanilmadi\n\nBoshqa audio yuboring", message.chat.id, status_msg.message_id)
            return
            
        title, artist = result['title'], result['artist']
        bot.edit_message_text(f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...", message.chat.id, status_msg.message_id)
        
        audio_file_path = download_youtube_audio(f"{artist} {title}", f"{artist}_{title}")
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as f:
                bot.send_audio(message.chat.id, f, title=title[:64], performer=artist[:64], caption=f"🎵 {title}\n👤 {artist}")
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
            bot.edit_message_text(f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi", message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try: bot.edit_message_text("❌ Xatolik yuz berdi", message.chat.id, status_msg.message_id)
            except: pass
    finally:
        safe_delete(audio_file_path)

# ==================== INSTAGRAM HANDLER ====================
@bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
def handle_instagram(message: types.Message) -> None:
    status_msg = video_path = None
    try:
        url = message.text.strip().split('?')[0]
        status_msg = bot.reply_to(message, "⏳ Instagram yuklanmoqda...")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best',
            'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
            'socket_timeout': 30,
            'retries': 5,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
        }
        
        # Dinamik Cookie tekshiruvi
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            logger.info("🍪 cookies.txt topildi, u orqali ulanilmoqda.")
        else:
            logger.warning("⚠️ cookies.txt topilmadi! Ochiq linklar orqali urinib ko'riladi.")
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
            
        video_files = list(TEMP_DIR.glob(f"ig_{video_id}*")) or sorted(
            list(TEMP_DIR.glob('ig_*.mp4')) + list(TEMP_DIR.glob('ig_*.webm')),
            key=lambda f: f.stat().st_mtime, reverse=True)
            
        if not video_files:
            bot.edit_message_text("❌ Video yuklanmadi. Agar video yopiq (private) bo'lsa, cookies.txt fayli kerak bo'ladi.", message.chat.id, status_msg.message_id)
            return
            
        video_path = video_files[0]
        
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y'], check=True)
                safe_delete(video_path)
                video_path = mp4_path
            except: pass
            
        if video_path.stat().st_size > MAX_FILE_SIZE:
            bot.edit_message_text("❌ Video hajmi 50 MB dan katta.", message.chat.id, status_msg.message_id)
            return
            
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{btn_hash}"))
        
        with open(video_path, 'rb') as f:
            bot.send_video(message.chat.id, f, reply_markup=markup, caption="📱 Instagram")
            
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        bot.delete_message(message.chat.id, status_msg.message_id)
        
    except Exception as e:
        logger.error(f"Instagram xatosi: {e}")
        if status_msg:
            bot.edit_message_text("❌ Video yuklashda xatolik. Video shaxsiy bo'lishi mumkin.", message.chat.id, status_msg.message_id)
    finally:
        if video_path:
            threading.Thread(target=lambda: (time.sleep(60), safe_delete(video_path)), daemon=True).start()

# ==================== TIKTOK HANDLER ====================
@bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
def handle_tiktok(message: types.Message) -> None:
    status_msg = video_path = None
    try:
        url = message.text.strip()
        status_msg = bot.reply_to(message, "📱 TikTok yuklanmoqda...")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best',
            'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
            'nocheckcertificate': True,
            'geo_bypass': True,
        }
        
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
            
        video_files = list(TEMP_DIR.glob(f"tt_{video_id}*")) or sorted(
            list(TEMP_DIR.glob('tt_*.mp4')) + list(TEMP_DIR.glob('tt_*.webm')),
            key=lambda f: f.stat().st_mtime, reverse=True)
            
        if not video_files:
            bot.edit_message_text("❌ TikTok yuklanmadi", message.chat.id, status_msg.message_id)
            return
            
        video_path = video_files[0]
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y'], check=True)
                safe_delete(video_path)
                video_path = mp4_path
            except: pass
            
        if video_path.stat().st_size > MAX_FILE_SIZE:
            bot.edit_message_text("❌ Video juda katta", message.chat.id, status_msg.message_id)
            return
            
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{btn_hash}"))
        
        with open(video_path, 'rb') as f:
            bot.send_video(message.chat.id, f, reply_markup=markup, caption="📱 TikTok")
            
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        bot.delete_message(message.chat.id, status_msg.message_id)
        
    except Exception as e:
        logger.error(f"TikTok xatosi: {e}")
        if status_msg: bot.edit_message_text("❌ TikTok yuklanmadi", message.chat.id, status_msg.message_id)
    finally:
        if video_path:
            threading.Thread(target=lambda: (time.sleep(60), safe_delete(video_path)), daemon=True).start()

# ==================== VIDEO MUSIC RECOGNITION ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('music_'))
def handle_video_music_recognition(call: types.CallbackQuery) -> None:
    audio_path = video_path = audio_file_path = None
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
        if not audio_path:
            bot.send_message(call.message.chat.id, "❌ Audio ajratilmadi")
            return
            
        with open(audio_path, 'rb') as f:
            result = recognize_audio(f.read())
            
        if not result['found']:
            bot.send_message(call.message.chat.id, "❌ Musiqa tanilmadi")
            return
            
        title, artist = result['title'], result['artist']
        bot.send_message(call.message.chat.id, f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...")
        
        audio_file_path = download_youtube_audio(f"{artist} {title}", f"{artist}_{title}")
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as f:
                bot.send_audio(call.message.chat.id, f, title=title[:64], performer=artist[:64], caption=f"🎵 {title}\n👤 {artist}")
        else:
            bot.send_message(call.message.chat.id, f"✅ Topildi: {title} - {artist}\n❌ Yuklanmadi")
    except Exception as e:
        logger.error(f"Video music xatosi: {e}")
    finally:
        safe_delete(audio_path)
        safe_delete(audio_file_path)
        safe_delete(video_path)

# ==================== SEARCH HANDLER ====================
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
            bot.edit_message_text("❌ Hech narsa topilmadi", message.chat.id, status_msg.message_id)
            return
            
        user_sessions[message.chat.id] = {'query': query, 'songs': songs, 'page': 0, 'timestamp': datetime.now()}
        show_search_results(message.chat.id, 0)
        bot.delete_message(message.chat.id, status_msg.message_id)
    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg: bot.edit_message_text("❌ Qidiruvda xatolik", message.chat.id, status_msg.message_id)

def show_search_results(chat_id: int, page: int = 0) -> None:
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Sessiya muddati tugagan")
        return
        
    query, songs = session['query'], session['songs']
    total_songs, page_size = len(songs), 10
    total_pages = (total_songs + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * page_size
    page_songs = songs[start_idx:start_idx + page_size]
    
    text_lines = [f"🔍 *{query}*", f"📄 Sahifa: {page + 1}/{total_pages} | Jami: {total_songs} ta", ""]
    markup = types.InlineKeyboardMarkup(row_width=5)
    current_row, button_rows = [], []
    
    for idx, song in enumerate(page_songs, start=1):
        if not song: continue
        global_idx = start_idx + idx
        title = song.get('title', 'Nomaʼlum')[:45]
        text_lines.append(f"{global_idx}. {title}{format_duration(song.get('duration'))}")
        
        url = song.get('url') or song.get('webpage_url')
        if url:
            h = create_hash(f"{url}_{global_idx}")
            (TEMP_DIR / f"song_{h}.txt").write_text(f"{url}|{title}|{global_idx}")
            current_row.append(types.InlineKeyboardButton(str(global_idx), callback_data=f"dl_{h}"))
            if len(current_row) == 5:
                button_rows.append(current_row)
                current_row = []
                
    if current_row: button_rows.append(current_row)
    for row in button_rows: markup.add(*row)
    
    nav_buttons = []
    if page > 0: nav_buttons.append(types.InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page_{page-1}"))
    nav_buttons.append(types.InlineKeyboardButton("❌", callback_data="close_page"))
    if page < total_pages - 1: nav_buttons.append(types.InlineKeyboardButton("Keyingi ➡️", callback_data=f"page_{page+1}"))
    if nav_buttons: markup.row(*nav_buttons)
    
    markup.row(types.InlineKeyboardButton("🔄 Yangi", callback_data="nav_new"), types.InlineKeyboardButton("🏠 Menyu", callback_data="nav_home"))
    user_sessions[chat_id]['page'] = page
    
    try: bot.send_message(chat_id, "\n".join(text_lines), reply_markup=markup, parse_mode='Markdown')
    except: bot.send_message(chat_id, "\n".join(text_lines).replace('*', ''), reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith('page_'))
def handle_page_navigation(call: types.CallbackQuery) -> None:
    try:
        page = int(call.data.split('_')[1])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_search_results(call.message.chat.id, page)
        bot.answer_callback_query(call.id)
    except: bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data == "close_page")
def handle_close_page(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Yopildi")
    except: pass

@bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
def handle_song_download(call: types.CallbackQuery) -> None:
    audio_file_path = None
    try:
        btn_hash = call.data.split('_')[1]
        data_file = TEMP_DIR / f"song_{btn_hash}.txt"
        if not data_file.exists():
            return bot.answer_callback_query(call.id, "❌ Vaqt o'tgan", show_alert=True)
            
        data = data_file.read_text().strip().split('|', 2)
        url, title = data[0], data[1] if len(data) > 1 else 'Audio'
        if len(data) == 3: title = f"{data[2]}. {title}"
        
        bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")
        audio_file_path = download_youtube_audio(url, title)
        
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as f:
                bot.send_audio(call.message.chat.id, f, title=title[:64], caption=f"✅ {title}", timeout=300)
        else:
            bot.send_message(call.message.chat.id, "❌ Yuklashda xatolik")
        safe_delete(data_file)
    except Exception as e:
        logger.error(f"Download xatosi: {e}")
    finally:
        safe_delete(audio_file_path)

@bot.callback_query_handler(func=lambda c: c.data.startswith('nav_'))
def handle_navigation(call: types.CallbackQuery) -> None:
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    if call.data == 'nav_home': start_command(call.message)
    elif call.data == 'nav_new': bot.send_message(call.message.chat.id, "🔍 Qo'shiq nomini yozing:")

# ==================== SHUTDOWN & CLEANUP ====================
def shutdown_handler(signum, frame) -> None:
    logger.info("\n🛑 Bot to'xtatilmoqda...")
    try:
        cleanup_old_files()
        bot.stop_polling()
    except: pass
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

def start_periodic_cleanup() -> None:
    def cleanup_loop():
        while True:
            time.sleep(CLEANUP_INTERVAL)
            cleanup_old_files()
    threading.Thread(target=cleanup_loop, daemon=True).start()

# ==================== MAIN ====================
def main() -> None:
    logger.info("=" * 60)
    logger.info("🎵 TELEGRAM MUSIC BOT - RENDER READY")
    logger.info("=" * 60)
    
    # Render uchun Web Serverni ishga tushirish (Crash bo'lishini oldini oladi)
    start_dummy_server()
    
    cleanup_old_files()
    start_periodic_cleanup()
    
    try:
        logger.info("🔄 Polling boshlandi...")
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
    except KeyboardInterrupt:
        shutdown_handler(None, None)
    except Exception as e:
        logger.error(f"❌ Fatal xatolik: {e}")
        time.sleep(3)
        try: bot.infinity_polling(skip_pending=True)
        except: sys.exit(1)

if __name__ == '__main__':
    main()
