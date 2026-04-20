# Python 3.12 image
FROM python:3.12-slim

# System tools va dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    libasound2-dev \
    curl \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Rust o'rnatish (agar kerak bo'lsa)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Working directory
WORKDIR /app

# Requirements faylini containerga ko'chirish
COPY requirements.txt .

# Python paketlarini o'rnatish
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir beautifulsoup4 lxml requests

# Loyiha fayllarini ko'chirish
COPY . .

# Temp katalog yaratish
RUN mkdir -p temp

# Botni ishga tushirish
CMD ["python", "bot.py"]
