import uuid
import time
import random
import asyncio
import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
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
BG_DIR = ASSETS_DIR / "backgrounds"
GENERATED_DIR = BASE_DIR / "generated"
TEMPLATES_DIR = BASE_DIR / "templates"

GENERATED_DIR.mkdir(parents=True, exist_ok=True)
BG_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── Настройки ────────────────────────────────────────────
FRAME_TEMPLATE = ASSETS_DIR / "frame_template.png"
FONT_PATH = ASSETS_DIR / "BoldPixels.ttf"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 25
VIDEO_FRAMES = 248
VIDEO_DURATION = VIDEO_FRAMES / VIDEO_FPS

FONT_SIZE = 80
TEXT_COLOR = "02FEF7"
TEXT_LEFT_X = 20
TEXT_TOP_Y = 1500
MAX_NAME_LENGTH = 21

RARE_BG_CHANCE = 0.001  # 0.1%
RARE_BG_PREFIX = "bg_rare"


# ── Выбор фона ──────────────────────────────────────────

def get_all_backgrounds() -> tuple[list[Path], list[Path]]:
    """Возвращает (обычные фоны, редкие фоны)."""
    all_mp4 = sorted(BG_DIR.glob("*.mp4"))

    if not all_mp4:
        all_mp4 = sorted(ASSETS_DIR.glob("*.mp4"))

    normal = []
    rare = []
    for f in all_mp4:
        if f.stem.lower().startswith(RARE_BG_PREFIX):
            rare.append(f)
        else:
            normal.append(f)
    return normal, rare


def pick_background() -> tuple[Path, bool, float]:
    """
    Выбирает случайный фон.
    Возвращает (путь, is_rare, rarity_percent).
    """
    normal, rare = get_all_backgrounds()

    if not normal and not rare:
        raise FileNotFoundError(
            "Не найдено ни одного .mp4 файла в assets/backgrounds/"
        )

    is_rare = False
    rarity_percent = 0.0

    # Шанс редкого фона
    if rare and random.random() < RARE_BG_CHANCE:
        chosen = random.choice(rare)
        is_rare = True
        rarity_percent = RARE_BG_CHANCE * 100  # 0.1
        log.info(f"🌟 ВЫПАЛ РЕДКИЙ ФОН! {chosen.name} (шанс {rarity_percent}%)")
    elif normal:
        chosen = random.choice(normal)
        # Шанс обычного фона
        normal_total_chance = 1.0 - RARE_BG_CHANCE if rare else 1.0
        rarity_percent = round((normal_total_chance / len(normal)) * 100, 1)
        log.info(f"🎲 Выбран фон: {chosen.name} (шанс ~{rarity_percent}%)")
    elif rare:
        chosen = random.choice(rare)
        is_rare = True
        rarity_percent = RARE_BG_CHANCE * 100
        log.info(f"🎲 Единственный фон (редкий): {chosen.name}")

    return chosen, is_rare, rarity_percent


# ── FFmpeg ───────────────────────────────────────────────

def ff_escape_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\\'")
         .replace(":", "\\:")
         .replace("%", "%%")
    )


async def render_video(background_video: Path, name: str, output_path: Path) -> None:
    total_start = time.time()
    duration_str = f"{VIDEO_DURATION:.4f}"
    font_escaped = str(FONT_PATH).replace("\\", "/").replace(":", "\\:")
    text = ff_escape_text(name.upper())

    log.info(f"{'='*50}")
    log.info(f"🎬 НАЧАЛО ГЕНЕРАЦИИ")
    log.info(f"   Имя: {name}")
    log.info(f"   Фон: {background_video.name}")
    log.info(f"   Длительность: {VIDEO_DURATION:.1f} сек ({VIDEO_FRAMES} кадров)")
    log.info(f"   Выход: {output_path.name}")
    log.info(f"{'='*50}")

    filter_complex = (
        f"[0:v]trim=duration={duration_str},setpts=PTS-STARTPTS,"
        f"fps={VIDEO_FPS},scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg];"
        f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[ovr];"
        f"[bg][ovr]overlay=0:0:format=auto,"
        f"drawtext=text='{text}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={FONT_SIZE}:"
        f"fontcolor=#{TEXT_COLOR}:"
        f"x={TEXT_LEFT_X}:y={TEXT_TOP_Y}"
        f"[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(background_video),
        "-i", str(FRAME_TEMPLATE),
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
        log.error(f"❌ FFmpeg ошибка (код {process.returncode})")
        raise RuntimeError(f"FFmpeg error (код {process.returncode})")

    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

    log.info(f"{'='*50}")
    log.info(f"✅ ГЕНЕРАЦИЯ ЗАВЕРШЕНА!")
    log.info(f"   Время: {elapsed:.1f} сек")
    log.info(f"   Размер: {size_mb:.1f} МБ")
    log.info(f"   Файл: {output_path.name}")
    log.info(f"{'='*50}")


# ── Роуты ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    log.info("📄 Главная страница")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/processing", response_class=HTMLResponse)
async def processing(request: Request, name: str = ""):
    if not name.strip():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/")
    log.info(f"📄 Страница генерации: {name}")
    return templates.TemplateResponse("processing.html", {"request": request})


@app.post("/generate")
async def generate(name: str = Form(...)):
    name = name.strip()
    log.info(f"📩 Запрос: '{name}'")

    if not name:
        return JSONResponse({"error": "Введите имя"}, status_code=400)
    if len(name) > MAX_NAME_LENGTH:
        return JSONResponse(
            {"error": f"Максимум {MAX_NAME_LENGTH} символов"}, status_code=400
        )

    if not FRAME_TEMPLATE.exists():
        log.error("❌ frame_template.png не найден!")
        return JSONResponse({"error": "frame_template.png не найден"}, status_code=500)
    if not FONT_PATH.exists():
        log.error("❌ BoldPixels.ttf не найден!")
        return JSONResponse({"error": "BoldPixels.ttf не найден"}, status_code=500)

    # Выбираем фон
    try:
        background_video, is_rare, rarity_percent = pick_background()
    except FileNotFoundError as e:
        log.error(f"❌ {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Каждый раз генерируем новое видео (без кэша)
    file_id = f"{uuid.uuid4().hex[:8]}"
    filename = f"{file_id}.mp4"
    video_path = GENERATED_DIR / filename

    try:
        await render_video(background_video, name, video_path)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({
        "status": "done",
        "is_rare": is_rare,
        "rarity_percent": rarity_percent,
        "bg_name": background_video.stem,
        "download_url": f"/download/{file_id}",
        "watch_url": f"/watch/{file_id}?rare={'1' if is_rare else '0'}&rp={rarity_percent}",
    })


@app.get("/download/{file_id}")
async def download(file_id: str):
    video_path = GENERATED_DIR / f"{file_id}.mp4"
    if not video_path.exists():
        return JSONResponse({"error": "Видео не найдено"}, status_code=404)
    log.info(f"📥 Скачивание: {video_path.name}")
    return FileResponse(
        str(video_path),
        media_type="video/mp4",
        filename=f"{file_id}.mp4",
    )


@app.get("/watch/{file_id}")
async def watch(request: Request, file_id: str, rare: str = "0", rp: str = "0"):
    video_path = GENERATED_DIR / f"{file_id}.mp4"
    if not video_path.exists():
        return JSONResponse({"error": "Видео не найдено"}, status_code=404)
    log.info(f"👁️  Просмотр: {video_path.name}")
    return templates.TemplateResponse("watch.html", {
        "request": request,
        "job_id": file_id,
        "video_url": f"/download/{file_id}",
        "is_rare": rare == "1",
        "rarity_percent": rp,
    })


@app.get("/stats")
async def stats():
    """Статистика сгенерированных видео."""
    files = list(GENERATED_DIR.glob("*.mp4"))
    total_size = sum(f.stat().st_size for f in files) / (1024 * 1024)

    normal, rare = get_all_backgrounds()

    return JSONResponse({
        "total_videos": len(files),
        "total_size_mb": round(total_size, 1),
        "available_normal_bgs": len(normal),
        "available_rare_bgs": len(rare),
    })


@app.on_event("startup")
async def startup():
    normal, rare = get_all_backgrounds()
    cached = list(GENERATED_DIR.glob("*.mp4"))

    log.info(f"🚀 Сервер запущен!")
    log.info(f"   Assets: {ASSETS_DIR}")
    log.info(f"   Backgrounds: {BG_DIR}")
    log.info(f"   Generated: {GENERATED_DIR}")
    log.info(f"   Обычных фонов: {len(normal)} шт — {[f.name for f in normal]}")
    log.info(f"   Редких фонов: {len(rare)} шт — {[f.name for f in rare]}")
    log.info(f"   Сгенерированных видео: {len(cached)} шт")
    log.info(f"   frame_template.png: {'✅' if FRAME_TEMPLATE.exists() else '❌'}")
    log.info(f"   BoldPixels.ttf: {'✅' if FONT_PATH.exists() else '❌'}")
    log.info(f"   monkey.png: {'✅' if (ASSETS_DIR / 'monkey.png').exists() else '❌'}")
    log.info(f"   Шанс редкого фона: {RARE_BG_CHANCE*100}%")


if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception as e:
        print(f"\nОШИБКА: {e}")
        input("\nНажмите Enter чтобы закрыть...")