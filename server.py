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
RARITY_TEXT_COLOR = "000000"

MAX_NAME_LENGTH = 21

# Допустимые значения
VALID_BG = {"1", "2", "3", "4"}
VALID_AGE = {"1", "2", "3", "4"}


def get_bg_video(bg_num: str) -> Path:
    path = ASSETS_DIR / f"bg_{bg_num}.mp4"
    if not path.exists():
        raise FileNotFoundError(f"Фоновое видео bg_{bg_num}.mp4 не найдено")
    return path


def get_age_plashka(age_num: str) -> Path:
    path = ASSETS_DIR / f"plashka_age_{age_num}.png"
    if not path.exists():
        raise FileNotFoundError(f"Плашка plashka_age_{age_num}.png не найдена")
    return path


def generate_rarity() -> int:
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


def ff_escape_text(s: str) -> str:
    """Экранирование текста для FFmpeg drawtext."""
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\\'")
         .replace(":", "\\:")
         .replace(";", "\\;")
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

    # Текст имени — экранируем
    text_name = ff_escape_text(name.upper())

    # Текст редкости — число + знак процента
    # В FFmpeg drawtext знак % экранируется как %%
    rarity_str = f"{rarity}%%"
    text_rarity = ff_escape_text(rarity_str)

    log.info(f"{'='*55}")
    log.info(f"🎬 НАЧАЛО ГЕНЕРАЦИИ")
    log.info(f"   Имя: {name} -> drawtext: '{text_name}'")
    log.info(f"   Редкость: {rarity}% -> drawtext: '{text_rarity}'")
    log.info(f"   Фон: {bg_video.name}")
    log.info(f"   Плашка: {age_plashka.name}")
    log.info(f"   Длительность: {VIDEO_DURATION:.1f} сек ({VIDEO_FRAMES} кадров)")
    log.info(f"   Выход: {output_path.name}")
    log.info(f"{'='*55}")

    # filter_complex собираем по частям и соединяем через ;
    filters = [
        # [0] bg video: trim, fps, scale
        f"[0:v]trim=duration={duration_str},setpts=PTS-STARTPTS,"
        f"fps={VIDEO_FPS},scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg]",

        # [1] frame template: scale
        f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[frame]",

        # [2] age plashka: as-is with alpha
        f"[2:v]format=rgba[plashka]",

        # overlay frame on bg
        f"[bg][frame]overlay=0:0:format=auto[v1]",

        # overlay plashka on v1
        f"[v1][plashka]overlay=0:0:format=auto[v2]",

        # drawtext: name
        f"[v2]drawtext="
        f"text='{text_name}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={NAME_FONT_SIZE}:"
        f"fontcolor=#{NAME_TEXT_COLOR}:"
        f"x={NAME_TEXT_X}:y={NAME_TEXT_Y}"
        f"[v3]",

        # drawtext: rarity
        f"[v3]drawtext="
        f"text='{text_rarity}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={RARITY_FONT_SIZE}:"
        f"fontcolor=#{RARITY_TEXT_COLOR}:"
        f"x={RARITY_TEXT_X}:y={RARITY_TEXT_Y}"
        f"[out]",
    ]

    filter_complex = ";".join(filters)

    log.info(f"📝 filter_complex:")
    for i, f in enumerate(filters):
        log.info(f"   [{i}] {f}")

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

    log.info(f"⚙️  FFmpeg запущен...")

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
                if any(k in decoded.lower() for k in ['error', 'warning', 'invalid', 'failed']):
                    log.error(f"   ❗ {decoded}")
                elif any(k in decoded.lower() for k in ['frame=', 'fps=', 'speed=', 'time=']):
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
                        log.info(f"   🔄 {pct}% ({frame_num}/{VIDEO_FRAMES}) [{elapsed:.1f}s]")
                except:
                    pass

    await asyncio.gather(read_stderr(), read_stdout(), process.wait())

    elapsed = time.time() - total_start

    if process.returncode != 0:
        # Читаем полный stderr для диагностики
        log.error(f"❌ FFmpeg ошибка (код {process.returncode}), время: {elapsed:.1f}s")
        raise RuntimeError(f"FFmpeg error (код {process.returncode})")

    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

    log.info(f"{'='*55}")
    log.info(f"✅ ГОТОВО! Время: {elapsed:.1f}s, Размер: {size_mb:.1f} МБ")
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
    log.info(f"📄 Генерация: name={name}, bg={bg}, age={age}")
    return templates.TemplateResponse("processing.html", {"request": request})


@app.post("/generate")
async def generate(
    name: str = Form(...),
    bg: str = Form(...),
    age: str = Form(...),
):
    name = name.strip()
    log.info(f"📩 Запрос: name='{name}', bg={bg}, age={age}")

    if not name:
        return JSONResponse({"error": "Введите имя"}, status_code=400)
    if len(name) > MAX_NAME_LENGTH:
        return JSONResponse({"error": f"Максимум {MAX_NAME_LENGTH} символов"}, status_code=400)
    if bg not in VALID_BG:
        return JSONResponse({"error": "Неверный выбор фона"}, status_code=400)
    if age not in VALID_AGE:
        return JSONResponse({"error": "Неверный выбор возраста"}, status_code=400)

    if not FRAME_TEMPLATE.exists():
        return JSONResponse({"error": "frame_template.png не найден"}, status_code=500)
    if not FONT_PATH.exists():
        return JSONResponse({"error": "BoldPixels.ttf не найден"}, status_code=500)

    try:
        bg_video = get_bg_video(bg)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    try:
        age_plashka = get_age_plashka(age)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

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
        return JSONResponse({"error": "Видео не найдено"}, status_code=404)
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