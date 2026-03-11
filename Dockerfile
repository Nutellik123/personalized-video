FROM python:3.11-slim

# Ставим FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Рабочая папка внутри контейнера
WORKDIR /app

# Копируем и ставим зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Создаём папку для генерируемых видео
RUN mkdir -p /app/static/generated

# Открываем порт
EXPOSE 8000

# Запускаем сервер
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]