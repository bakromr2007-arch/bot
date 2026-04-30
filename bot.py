#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot - Python 3.11.0 Compatible
Optimized for Render & YouTube Bot Bypass (No Cookies Required)
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

# ==================== CONFIG ====================
BOT_TOKEN = "8575775719:AAFk71ow9WR7crlONGpnP56qAZjO88Hj4eI"
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit
CLEANUP_INTERVAL = 600  # 10 minut

# ==================== RENDER KEEP-ALIVE SERVER ====================
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is active!")

    def log_message(self, format, *args): pass

def start_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"✅ Render Web Server {port}-portda ishga tushdi")

# ==================== YT-DLP CONFIGURATION ====================
# YouTube bot-tekshiruvini chetlab o'tish uchun asosiy sozlamalar
BASE_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'retries': 5,
    'fragment_retries': 5,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'prefer_insecure': True,
    # 🔥 MUHIM: YouTube'ni aldash (Mobil ilova sifatida)
    'extractor_args': {
        'youtube': ['player_client=android,ios,web_creator']
    }
}

AUDIO_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'bestaudio/best',
    'outtmpl': str(TEMP_DIR / 'audio_%(title)s.%(ext)s'),
    'restrictfilenames': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '196',
    }],
}

SEARCH_OPTIONS = {
    **BASE_OPTIONS,
    'extract_flat': True,
}

# ==================== BOT INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN, threaded=False, skip_pending=True)

# ==================== UTILITY FUNCTIONS ====================
def cleanup_old_files():
    try:
        current_time = time.time()
        for filepath in TEMP_DIR.iterdir():
            if filepath.is_file() and (current_time - filepath.stat().st_mtime > CLEANUP_INTERVAL):
                filepath.unlink()
    except Exception as e: logger.error(f"Cleanup xatosi: {e}")

def safe_delete(path):
    try:
        if path and Path(path).exists(): Path(path).unlink()
    except: pass

def create_hash(text): return hashlib.md5(str(text).encode()).hexdigest()[:12]

def clean_filename(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text or "audio")
    return re.sub(r'\s+', '_', text)[:50].strip('_')

# ==================== DOWNLOAD LOGIC ====================
def download_youtube_audio(query, filename_hint=""):
    try:
        clean_name = clean_filename(filename_hint or query)
        output_path = TEMP_DIR / f"audio_{clean_name}.mp3"
        opts = AUDIO_OPTIONS.copy()
        opts['outtmpl'] = str(TEMP_DIR / f"audio_{clean_name}.%(ext)s")
        
        # Agar cookies.txt bo'lsa, zaxira sifatida ishlatish
        if os.path.exists('cookies.txt'): opts['cookiefile'] = 'cookies.txt'
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"ytsearch1:{query}"])
        
        if output_path.exists(): return output_path
        # Fallback
        files = sorted(TEMP_DIR.glob('audio_*.mp3'), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[0] if files else None
    except Exception as e:
        logger.error(f"Yuklash xatosi: {e}")
        return None

async def recognize_audio_async(audio_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3', dir=TEMP_DIR) as tmp:
        tmp.write(audio_bytes)
        path = tmp.name
    try:
        shazam = Shazam()
        res = await shazam.recognize(path)
        if res and 'track' in res:
            return {'found': True, 'title': res['track']['title'], 'artist': res['track']['subtitle']}
    except: pass
    finally: safe_delete(path)
    return {'found': False}

def recognize_audio(data):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    res = loop.run_until_complete(recognize_audio_async(data))
    loop.close()
    return res

# ==================== HANDLERS ====================
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🎵 *Musiqa botga xush kelibsiz!* \n\nNomini yozing yoki link yuboring.", parse_mode='Markdown')

@bot.message_handler(content_types=['audio', 'voice'])
def audio_handler(message):
    status = bot.reply_to(message, "🔍 Tanilmoqda...")
    file_info = bot.get_file(message.audio.file_id if message.audio else message.voice.file_id)
    data = bot.download_file(file_info.file_path)
    res = recognize_audio(data)
    
    if res['found']:
        t, a = res['title'], res['artist']
        bot.edit_message_text(f"✅ {t} - {a}\n⏳ Yuklanmoqda...", message.chat.id, status.message_id)
        path = download_youtube_audio(f"{a} {t}", f"{a}_{t}")
        if path:
            with open(path, 'rb') as f:
                bot.send_audio(message.chat.id, f, title=t, performer=a)
            bot.delete_message(message.chat.id, status.message_id)
        else: bot.edit_message_text("❌ Yuklab bo'lmadi", message.chat.id, status.message_id)
    else: bot.edit_message_text("❌ Musiqa topilmadi", message.chat.id, status.message_id)

@bot.message_handler(func=lambda m: "instagram.com" in m.text or "tiktok.com" in m.text)
def social_handler(message):
    status = bot.reply_to(message, "⏳ Yuklanmoqda...")
    url = message.text.strip().split('?')[0]
    opts = {**BASE_OPTIONS, 'format': 'best', 'outtmpl': str(TEMP_DIR / 'vid_%(id)s.%(ext)s')}
    if os.path.exists('cookies.txt'): opts['cookiefile'] = 'cookies.txt'
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            v_id = info.get('id', 'video')
        
        v_path = next(TEMP_DIR.glob(f"vid_{v_id}*"), None)
        if v_path:
            with open(v_path, 'rb') as f:
                bot.send_video(message.chat.id, f, caption="📱 Tayyor!")
            bot.delete_message(message.chat.id, status.message_id)
            threading.Thread(target=lambda: (time.sleep(30), safe_delete(v_path))).start()
        else: bot.edit_message_text("❌ Fayl topilmadi", message.chat.id, status.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Xato: {str(e)[:50]}", message.chat.id, status.message_id)

@bot.message_handler(func=lambda m: True)
def search_handler(message):
    query = message.text.strip()
    status = bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")
    try:
        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)
            songs = info.get('entries', [])
        
        if not songs: return bot.edit_message_text("❌ Topilmadi", message.chat.id, status.message_id)
        
        markup = types.InlineKeyboardMarkup()
        for i, s in enumerate(songs, 1):
            h = create_hash(s['id'])
            (TEMP_DIR / f"s_{h}.txt").write_text(f"{s['id']}|{s['title']}")
            markup.add(types.InlineKeyboardButton(f"{i}. {s['title'][:40]}", callback_data=f"dl_{h}"))
        
        bot.edit_message_text("Natijalar:", message.chat.id, status.message_id, reply_markup=markup)
    except: bot.edit_message_text("❌ Qidiruvda xato", message.chat.id, status.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
def inline_dl(call):
    h = call.data.split('_')[1]
    data_f = TEMP_DIR / f"s_{h}.txt"
    if not data_f.exists(): return bot.answer_callback_query(call.id, "❌ Muddati o'tgan")
    
    bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")
    v_id, title = data_f.read_text().split('|', 1)
    path = download_youtube_audio(v_id, title)
    
    if path:
        with open(path, 'rb') as f:
            bot.send_audio(call.message.chat.id, f, title=title)
        safe_delete(path)
    else: bot.send_message(call.message.chat.id, "❌ Yuklab bo'lmadi")

# ==================== RUN ====================
if __name__ == '__main__':
    start_dummy_server()
    threading.Thread(target=lambda: (while True: (time.sleep(600), cleanup_old_files())), daemon=True).start()
    logger.info("🚀 Bot polling boshlandi...")
    bot.infinity_polling(timeout=30)
