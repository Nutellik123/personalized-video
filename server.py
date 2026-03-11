import uuid
import time
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

FONT_SIZE = 80
TEXT_COLOR = "02FEF7"
TEXT_LEFT_X = 20
TEXT_TOP_Y = 1500
MAX_NAME_LENGTH = 21


def find_background_video() -> Path:
    mp4_files = sorted(ASSETS_DIR.glob("*.mp4"))
    if not mp4_files:
        raise FileNotFoundError("В папке assets не найден ни один .mp4 файл")
    if len(mp4_files) > 1:
        names = ", ".join([f.name for f in mp4_files])
        raise FileExistsError(
            f"В папке assets несколько .mp4: {names}. Оставьте один."
        )
    return mp4_files[0]


def ff_escape_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace("'", "\\'")
         .replace(":", "\\:")
         .replace(";", "\\;")
    )


async def render_video(background_video: Path, name: str, output_path: Path) -> None:
    total_start = time.time()
    duration_str = f"{VIDEO_DURATION:.4f}"
    font_escaped = str(FONT_PATH).replace("\\", "/").replace(":", "\\:")
    text = ff_escape_text(name.upper())

    log.info(f"{'='*50}")
    log.info(f"🎬 НАЧАЛО ГЕНЕРАЦИИ")
    log.info(f"   Имя: {name} -> '{text}'")
    log.info(f"   Фон: {background_video.name}")
    log.info(f"   Выход: {output_path.name}")
    log.info(f"{'='*50}")

    filters = [
        f"[0:v]trim=duration={duration_str},setpts=PTS-STARTPTS,"
        f"fps={VIDEO_FPS},scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg]",

        f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[frame]",

        f"[bg][frame]overlay=0:0:format=auto[v1]",

        f"[v1]drawtext="
        f"text='{text}':"
        f"fontfile='{font_escaped}':"
        f"fontsize={FONT_SIZE}:"
        f"fontcolor=#{TEXT_COLOR}:"
        f"x={TEXT_LEFT_X}:y={TEXT_TOP_Y}"
        f"[out]",
    ]

    filter_complex = ";".join(filters)

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
        log.error(f"❌ FFmpeg ошибка (код {process.returncode}), время: {elapsed:.1f}s")
        raise RuntimeError(f"FFmpeg error (код {process.returncode})")

    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

    log.info(f"{'='*50}")
    log.info(f"✅ ГОТОВО! Время: {elapsed:.1f}s, Размер: {size_mb:.1f} МБ")
    log.info(f"{'='*50}")


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
async def processing(request: Request, name: str = ""):
    if not name.strip():
        return RedirectResponse("/")
    log.info(f"📄 Генерация для: {name}")
    return templates.TemplateResponse("processing.html", {"request": request})


@app.post("/generate")
async def generate(name: str = Form(...)):
    name = name.strip()
    log.info(f"📩 Запрос: name='{name}'")

    if not name:
        return JSONResponse({"error": "Введите имя"}, status_code=400)
    if len(name) > MAX_NAME_LENGTH:
        return JSONResponse({"error": f"Максимум {MAX_NAME_LENGTH} символов"}, status_code=400)

    if not FRAME_TEMPLATE.exists():
        return JSONResponse({"error": "frame_template.png не найден"}, status_code=500)
    if not FONT_PATH.exists():
        return JSONResponse({"error": "BoldPixels.ttf не найден"}, status_code=500)

    try:
        background_video = find_background_video()
        log.info(f"🎥 Фон: {background_video.name}")
    except (FileNotFoundError, FileExistsError) as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    job_id = uuid.uuid4().hex[:8]
    video_path = GENERATED_DIR / f"video_{job_id}.mp4"

    try:
        await render_video(background_video, name, video_path)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    cleanup_old_files()

    return JSONResponse({
        "status": "done",
        "download_url": f"/download/{job_id}",
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
    mp4_files = list(ASSETS_DIR.glob("*.mp4"))
    log.info(f"   Видео: {[f.name for f in mp4_files]}")
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