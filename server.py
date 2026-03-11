import uuid
import time
import random
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── Логирование ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("videoapp")

app = FastAPI()

BASE_DIR = Path(__file__).parent
ASSETS_DIR = BASE_DIR / "assets"
STATIC_DIR = BASE_DIR / "static"
GENERATED_DIR = STATIC_DIR / "generated"
TEMPLATES_DIR = BASE_DIR / "templates"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── Настройки ────────────────────────────────────────────
FRAME_TEMPLATE = ASSETS_DIR / "frame_template.png"
FONT_PATH = ASSETS_DIR / "BoldPixels.ttf"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 25
VIDEO_FRAMES = 248
VIDEO_DURATION = VIDEO_FRAMES / VIDEO_FPS

# Текст имени
NAME_FONT_SIZE = 80
NAME_TEXT_COLOR = "02FEF7"
NAME_TEXT_X = 20
NAME_TEXT_Y = 1500

# Текст редкости
RARITY_FONT_SIZE = 50
RARITY_TEXT_X = 920
RARITY_TEXT_Y = 91

MAX_NAME_LENGTH = 21

# Допустимые значения
VALID_BG = {"1", "2", "3", "4"}
VALID_AGE = {"1", "2", "3", "4"}


def get_bg_video(bg_num: str) -> Path:
    """Получить путь к фоновому видео по номеру."""
    path = ASSETS_DIR / f"bg_{bg_num}.mp4"
    if not path.exists():
        raise FileNotFoundError(f"Фоновое видео bg_{bg_num}.mp4 не найдено")
    return path


def get_age_plashka(age_num: str) -> Path:
    """Получить путь к плашке возраста по номеру."""
    path = ASSETS_DIR / f"plashka_age_{age_num}.png"
    if not path.exists():
        raise FileNotFoundError(f"Плашка plashka_age_{age_num}.png не найдена")
    return path


def generate_rarity() -> int:
    """
    Генерация редкости рандомайзером.
    Чем выше процент, тем реже выпадает.
    """
    roll = random.random()

    if roll < 0.40:
        return random.randint(1, 20)
    elif roll < 0.70:
        return random.randint(21, 40)
    elif roll < 0.85:
        return random.randint(41, 60)
    elif roll < 0.94:
        return random.randint(61, 80)
    elif roll < 0.99:
        return random.randint(81, 95)
    else:
        return random.randint(96, 100)


def rarity_to_color(rarity: int) -> str:
    """
    Цвет от #000000 (0%) до #FF8C00 (100%).
    Линейная интерполяция.
    """
    t = rarity / 100.0
    r = int(255 * t)
    g = int(140 * t)
    b = 0
    return f"{r:02X}{g:02X}{b:02X}"


def rarity_glow_sigma(rarity: int) -> float:
    """Сила свечения текста редкости. 0 при 0%, до 8 при 100%."""
    return (rarity / 100.0) * 8.0


def ff_escape_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\\'")
         .replace(":", "\\:")
         .replace("%", "%%")
    )


async def render_video(
    bg_video: Path,
    age_plashka: Path,
    name: str,
    rarity: int,
    output_path: Path,
) -> None:
    total_start = time.time()
    duration_str = f"{VIDEO_DURATION:.4f}"
    font_escaped = str(FONT_PATH).replace("\\", "/").replace(":", "\\:")
    text_name = ff_escape_text(name.upper())
    text_rarity = ff_escape_text(f"{rarity}%")

    rarity_color = rarity_to_color(rarity)
    glow_sigma = rarity_glow_sigma(rarity)

    log.info(f"{'='*55}")
    log.info(f"🎬 НАЧАЛО ГЕНЕРАЦИИ")
    log.info(f"   Имя: {name}")
    log.info(f"   Фон: {bg_video.name}")
    log.info(f"   Плашка: {age_plashka.name}")
    log.info(f"   Редкость: {rarity}% (цвет #{rarity_color}, свечение {glow_sigma:.1f})")
    log.info(f"   Длительность: {VIDEO_DURATION:.1f} сек ({VIDEO_FRAMES} кадров)")
    log.info(f"   Разрешение: {VIDEO_WIDTH}x{VIDEO_HEIGHT}")
    log.info(f"   Выход: {output_path.name}")
    log.info(f"{'='*55}")

    # Сборка filter_complex:
    # [0] = bg video (loop)
    # [1] = frame_template.png
    # [2] = plashka_age_X.png
    #
    # Порядок наложения:
    #   bg → overlay frame → overlay plashka_age → drawtext name → drawtext rarity (с свечением)

    # Свечение для текста редкости реализуем через два drawtext:
    # 1. Размытый текст (тень/glow) — если glow_sigma > 0
    # 2. Чёткий текст поверх

    # Для свечения в FFmpeg используем boxblur на отдельном текстовом слое — 
    # но проще сделать через shadowcolor + shadowx/shadowy = 0 с несколькими проходами.
    # Самый надёжный способ: просто drawtext с shadow.

    glow_alpha = min(1.0, rarity / 100.0 * 0.8)
    glow_hex = rarity_color

    filter_parts = []

    # Подготовка bg
    filter_parts.append(
        f"[0:v]trim=duration={duration_str},setpts=PTS-STARTPTS,"
        f"fps={VIDEO_FPS},scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg]"
    )

    # Масштабируем frame
    filter_parts.append(
        f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[frame]"
    )

    # Масштабируем plashka age (без изменения размера, как есть)
    filter_parts.append(
        f"[2:v]format=rgba[plashka]"
    )

    # Overlay frame на bg
    filter_parts.append(
        f"[bg][frame]overlay=0:0:format=auto[v1]"
    )

    # Overlay plashka на v1
    filter_parts.append(
        f"[v1][plashka]overlay=0:0:format=auto[v2]"
    )

    # Drawtext: имя
    filter_parts.append(
        f"[v2]drawtext=text='{text_name}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={NAME_FONT_SIZE}:"
        f"fontcolor=#{NAME_TEXT_COLOR}:"
        f"x={NAME_TEXT_X}:y={NAME_TEXT_Y}[v3]"
    )

    # Drawtext: редкость (с свечением через shadow)
    # shadowcolor = цвет редкости с альфой, shadowx=0, shadowy=0 — даёт glow эффект
    # Для усиления glow добавляем несколько теневых проходов
    shadow_parts = ""
    if glow_sigma > 1:
        # FFmpeg drawtext поддерживает только один shadow,
        # но мы можем сделать 2 прохода drawtext для glow
        filter_parts.append(
            f"[v3]drawtext=text='{text_rarity}':"
            f"fontfile='{font_escaped}':"
            f"fontsize={RARITY_FONT_SIZE}:"
            f"fontcolor=#{rarity_color}@0.3:"
            f"x={RARITY_TEXT_X}:y={RARITY_TEXT_Y}:"
            f"shadowcolor=#{rarity_color}@{glow_alpha:.2f}:"
            f"shadowx=0:shadowy=0[v4]"
        )
        # Чёткий текст поверх
        filter_parts.append(
            f"[v4]drawtext=text='{text_rarity}':"
            f"fontfile='{font_escaped}':"
            f"fontsize={RARITY_FONT_SIZE}:"
            f"fontcolor=#{rarity_color}:"
            f"x={RARITY_TEXT_X}:y={RARITY_TEXT_Y}:"
            f"shadowcolor=#{rarity_color}@{glow_alpha:.2f}:"
            f"shadowx=2:shadowy=2[out]"
        )
    else:
        # Низкая редкость — просто текст без особого свечения
        filter_parts.append(
            f"[v3]drawtext=text='{text_rarity}':"
            f"fontfile='{font_escaped}':"
            f"fontsize={RARITY_FONT_SIZE}:"
            f"fontcolor=#{rarity_color}:"
            f"x={RARITY_TEXT_X}:y={RARITY_TEXT_Y}[out]"
        )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(bg_video),
        "-i", str(FRAME_TEMPLATE),
        "-i", str(age_plashka),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-t", duration_str,
        "-r", str(VIDEO_FPS),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-tune", "fastdecode",
        "-threads", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        "-loglevel", "info",
        "-progress", "pipe:1",
        str(output_path),
    ]

    log.info(f"⚙️  FFmpeg команда запущена...")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_stderr():
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            decoded = line.decode(errors='replace').strip()
            if decoded:
                if any(k in decoded.lower() for k in ['frame=', 'fps=', 'speed=', 'time=', 'error', 'warning']):
                    log.info(f"   📊 {decoded}")

    async def read_stdout():
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors='replace').strip()
            if decoded.startswith('frame='):
                try:
                    frame_num = int(decoded.split('=')[1])
                    if frame_num % 50 == 0:
                        elapsed = time.time() - total_start
                        pct = min(100, int(frame_num / VIDEO_FRAMES * 100))
                        log.info(f"   🔄 Прогресс: {pct}% ({frame_num}/{VIDEO_FRAMES} кадров) [{elapsed:.1f}s]")
                except:
                    pass

    await asyncio.gather(read_stderr(), read_stdout(), process.wait())

    elapsed = time.time() - total_start

    if process.returncode != 0:
        log.error(f"❌ FFmpeg завершился с ошибкой (код {process.returncode})")
        log.error(f"   Время: {elapsed:.1f} сек")
        raise RuntimeError(f"FFmpeg error (код {process.returncode})")

    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

    log.info(f"{'='*55}")
    log.info(f"✅ ГЕНЕРАЦИЯ ЗАВЕРШЕНА!")
    log.info(f"   Время: {elapsed:.1f} сек")
    log.info(f"   Размер: {size_mb:.1f} МБ")
    log.info(f"   Файл: {output_path.name}")
    log.info(f"{'='*55}")


def cleanup_old_files(max_age_seconds: int = 3600) -> None:
    now = time.time()
    count = 0
    for f in GENERATED_DIR.iterdir():
        if f.is_file() and (now - f.stat().st_mtime > max_age_seconds):
            f.unlink(missing_ok=True)
            count += 1
    if count:
        log.info(f"🗑️  Удалено {count} старых файлов")


# ── Роуты ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    log.info("📄 Главная страница")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/processing", response_class=HTMLResponse)
async def processing(request: Request, name: str = "", bg: str = "", age: str = ""):
    if not name.strip() or bg not in VALID_BG or age not in VALID_AGE:
        return RedirectResponse("/")
    log.info(f"📄 Страница генерации: name={name}, bg={bg}, age={age}")
    return templates.TemplateResponse("processing.html", {"request": request})


@app.post("/generate")
async def generate(
    name: str = Form(...),
    bg: str = Form(...),
    age: str = Form(...),
):
    name = name.strip()
    log.info(f"📩 Запрос генерации: name='{name}', bg={bg}, age={age}")

    # Валидация
    if not name:
        return JSONResponse({"error": "Введите имя"}, status_code=400)
    if len(name) > MAX_NAME_LENGTH:
        return JSONResponse(
            {"error": f"Максимум {MAX_NAME_LENGTH} символов"}, status_code=400
        )
    if bg not in VALID_BG:
        return JSONResponse({"error": "Неверный выбор фона"}, status_code=400)
    if age not in VALID_AGE:
        return JSONResponse({"error": "Неверный выбор возраста"}, status_code=400)

    # Проверка файлов
    if not FRAME_TEMPLATE.exists():
        log.error("❌ frame_template.png не найден!")
        return JSONResponse({"error": "frame_template.png не найден"}, status_code=500)
    if not FONT_PATH.exists():
        log.error("❌ BoldPixels.ttf не найден!")
        return JSONResponse({"error": "BoldPixels.ttf не найден"}, status_code=500)

    try:
        bg_video = get_bg_video(bg)
        log.info(f"🎥 Фоновое видео: {bg_video.name} ({bg_video.stat().st_size / (1024*1024):.1f} МБ)")
    except FileNotFoundError as e:
        log.error(f"❌ {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        age_plashka = get_age_plashka(age)
        log.info(f"🏷️  Плашка возраста: {age_plashka.name}")
    except FileNotFoundError as e:
        log.error(f"❌ {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Генерация редкости
    rarity = generate_rarity()
    log.info(f"🎲 Редкость: {rarity}%")

    job_id = uuid.uuid4().hex[:8]
    video_path = GENERATED_DIR / f"video_{job_id}.mp4"

    try:
        await render_video(bg_video, age_plashka, name, rarity, video_path)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    cleanup_old_files()

    return JSONResponse({
        "status": "done",
        "download_url": f"/download/{job_id}",
        "rarity": rarity,
    })


@app.get("/download/{job_id}")
async def download(job_id: str):
    video_path = GENERATED_DIR / f"video_{job_id}.mp4"
    if not video_path.exists():
        return JSONResponse(
            {"error": "Видео не найдено или устарело"}, status_code=404
        )
    log.info(f"📥 Скачивание: {video_path.name}")
    return FileResponse(
        str(video_path),
        media_type="video/mp4",
        filename=f"personalized_{job_id}.mp4",
    )


@app.on_event("startup")
async def startup():
    log.info(f"🚀 Сервер запущен!")
    log.info(f"   Assets: {ASSETS_DIR}")
    log.info(f"   Templates: {TEMPLATES_DIR}")
    log.info(f"   Generated: {GENERATED_DIR}")

    # Проверка файлов
    for i in range(1, 5):
        bg_ok = (ASSETS_DIR / f"bg_{i}.mp4").exists()
        age_ok = (ASSETS_DIR / f"plashka_age_{i}.png").exists()
        log.info(f"   bg_{i}.mp4: {'✅' if bg_ok else '❌'}  |  plashka_age_{i}.png: {'✅' if age_ok else '❌'}")

    log.info(f"   frame_template.png: {'✅' if FRAME_TEMPLATE.exists() else '❌'}")
    log.info(f"   BoldPixels.ttf: {'✅' if FONT_PATH.exists() else '❌'}")
    log.info(f"   monkey.png: {'✅' if (ASSETS_DIR / 'monkey.png').exists() else '❌'}")


if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception as e:
        print(f"\nОШИБКА: {e}")
        input("\nНажмите Enter чтобы закрыть...")