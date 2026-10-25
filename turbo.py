
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Turbo Ultra-Engine v7.9 (Modal Cloud Edition + Bunny Stream + 10x Delogo)
=========================================================================
- مهيأ بالكامل للتشغيل السحابي عبر Modal Labs (GPU T4 + FFmpeg + Aria2).
- تم دمج إعدادات الرفع إلى Bunny Stream (Library: 782595 | Host: vz-2c74cefa-a0f.b-cdn.net).
- تثبيت Referer و Origin على https://iframe.mediadelivery.net/ لجميع روابط Bunny.
- تخطي مستخرج BunnyCdn المعطوب في yt-dlp وإجباره على التحميل المباشر + دعم FFmpeg المباشر.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import glob
import importlib.metadata
import importlib.util
import json
import logging
import mimetypes
import mmap
import os
import re
import shutil
import signal
import site
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import quote, urlparse

# ───────────────────────── إعدادات بيئة Modal السحابية ─────────────────────────
try:
    import modal

    modal_image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("ffmpeg", "aria2")
        .pip_install("telethon", "aiohttp", "aiofiles", "yt-dlp", "cryptg", "uvloop")
    )
    app = modal.App("turbo-ultra-engine", image=modal_image)
except ImportError:
    modal = None

    class _DummyApp:
        def function(self, *args, **kwargs):
            return lambda fn: fn

        def local_entrypoint(self, *args, **kwargs):
            return lambda fn: fn

    app = _DummyApp()

# ───────────────────────── الإعدادات الافتراضية المدمجة ─────────────────────────
LOCAL_DEFAULTS = {
    "API_ID": "35497706",
    "API_HASH": "4a7bd5a4763ecd7cf43f5f4dd108156b",
    "BOT_TOKEN": "8815624696:AAFhmKEjBkUQlQPVvv3Y9LxUB48JKclWOYg",
    "GITHUB_TOKEN": "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",
    "REPO_OWNER": "smyzx66-dot",
    "REPO_NAME": "housien",
    "CDN_DOMAIN": "https://holy-shadow-55be.loihsfiohgsre.workers.dev",
    "RELEASE_TAG": "v3",
    "BUNNY_LIBRARY_ID": "782595",
    "BUNNY_API_KEY": "e5b0036a-5157-48f4-896ec1a68cc3-eb63-45e6",
    "BUNNY_CDN_HOSTNAME": "vz-2c74cefa-a0f.b-cdn.net",
    "BUNNY_WAIT_SECONDS": "1800",
    "DOWNLOAD_DIR": "/content/downloads" if os.path.exists("/content") else "/tmp/downloads",
    "DOWNLOAD_SLOTS": "2",
    "ENCODE_SLOTS": "1",
    "UPLOAD_SLOTS": "2",
    "FRAGMENT_THREADS": "32",
    "X264_PRESET": "ultrafast",
    "USE_HWACCEL": "1",
    "SKIP_BOOTSTRAP": "1",
}

# أبعاد الشعار المرجعية (على فيديو 959x540)
DELOGO_REF_W = 959.0
DELOGO_REF_H = 540.0
DELOGO_X = 20
DELOGO_Y = 435
DELOGO_W = 268
DELOGO_H = 82

MAX_GITHUB_ASSET_BYTES = 2040 * 1024 * 1024
COMPRESS_CRFS = ("27", "30", "33")


def _load_dotenv() -> None:
    for k, v in LOCAL_DEFAULTS.items():
        os.environ.setdefault(k, v)
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        p = base / ".env"
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


_load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("TurboUltraEngine")


def log_error(msg: str, exc: Optional[BaseException] = None) -> None:
    logger.error("❌ %s", msg, exc_info=exc if exc else None)


# ───────────────────────── تثبيت الحزم ─────────────────────────
REQUIRED_MODULES = {
    "telethon": "telethon",
    "aiohttp": "aiohttp",
    "aiofiles": "aiofiles",
    "yt_dlp": "yt-dlp",
}
OPTIONAL_MODULES = {"cryptg": "cryptg"}
if sys.platform != "win32":
    OPTIONAL_MODULES["uvloop"] = "uvloop"


def _pip(*args: str) -> bool:
    base = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    for extra in ([], ["--break-system-packages"]):
        try:
            r = subprocess.run(
                base + extra + list(args),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def clean_conflicting_plugins() -> None:
    with contextlib.suppress(Exception):
        importlib.metadata.distribution("bgutil-ytdlp-pot-provider")
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "bgutil-ytdlp-pot-provider"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    with contextlib.suppress(Exception):
        for p in site.getsitepackages():
            for f in glob.glob(
                os.path.join(p, "yt_dlp_plugins", "**", "getpot_bgutil*"),
                recursive=True,
            ):
                with contextlib.suppress(Exception):
                    os.remove(f)


def bootstrap_dependencies() -> None:
    if os.environ.get("SKIP_BOOTSTRAP") == "1":
        return
    clean_conflicting_plugins()
    for module, package in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module) is None:
            logger.info("📦 تثبيت حزمة: %s ...", package)
            if not _pip(package):
                raise SystemExit(f"❌ تعذر تثبيت {package}. ثبّتها يدوياً: pip install {package}")
    for module, package in OPTIONAL_MODULES.items():
        if importlib.util.find_spec(module) is None:
            _pip(package)
    if os.environ.get("AUTO_UPDATE_YTDLP", "1") != "0":
        _pip("-U", "yt-dlp")


bootstrap_dependencies()

import aiofiles  # noqa: E402
import aiohttp  # noqa: E402
import yt_dlp  # noqa: E402
from telethon import Button, TelegramClient, events  # noqa: E402
from telethon.errors import FloodWaitError  # noqa: E402


def _patch_ytdlp() -> None:
    try:
        import yt_dlp.extractor.common as common
        orig = common.InfoExtractor._parse_json

        def patched(self, json_string, *a, **kw):
            if isinstance(json_string, (dict, list)):
                return json_string
            return orig(self, json_string, *a, **kw)

        common.InfoExtractor._parse_json = patched
    except Exception as exc:
        logger.warning("⚠️ تعذر ترقيع yt-dlp: %s", exc)


_patch_ytdlp()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    github_token: str
    repo_owner: str
    repo_name: str
    cdn_domain: str
    release_tag: str
    bunny_library_id: int
    bunny_api_key: str
    bunny_cdn_hostname: str
    bunny_wait_seconds: int
    download_dir: Path
    concurrent_tasks: int
    download_slots: int
    encode_slots: int
    upload_slots: int
    hls_segment_seconds: int
    gop_seconds: int
    fragment_threads: int
    max_height: int
    preset: str
    crf: str
    use_hwaccel: bool
    allowed_users: frozenset

    @property
    def github_ready(self) -> bool:
        return bool(self.github_token and self.repo_owner and self.repo_name)

    @property
    def bunny_ready(self) -> bool:
        return bool(self.bunny_api_key and self.bunny_library_id and self.bunny_cdn_hostname)

    @classmethod
    def from_env(cls) -> "Config":
        missing = [k for k in ("API_ID", "API_HASH", "BOT_TOKEN") if not _env(k)]
        if missing:
            raise SystemExit("❌ إعدادات ناقصة: " + ", ".join(missing))
        allowed = frozenset(
            int(x) for x in re.split(r"[,\s]+", _env("ALLOWED_USERS"))
            if x.strip().lstrip("-").isdigit()
        )
        host = _env("BUNNY_CDN_HOSTNAME").replace("https://", "").replace("http://", "").strip("/")
        base_conc = max(1, _env_int("CONCURRENT_TASKS", 3))
        return cls(
            api_id=_env_int("API_ID", 0),
            api_hash=_env("API_HASH"),
            bot_token=_env("BOT_TOKEN"),
            github_token=_env("GITHUB_TOKEN"),
            repo_owner=_env("REPO_OWNER"),
            repo_name=_env("REPO_NAME"),
            cdn_domain=_env("CDN_DOMAIN").rstrip("/"),
            release_tag=_env("RELEASE_TAG", "v3"),
            bunny_library_id=_env_int("BUNNY_LIBRARY_ID", 0),
            bunny_api_key=_env("BUNNY_API_KEY"),
            bunny_cdn_hostname=host,
            bunny_wait_seconds=max(0, _env_int("BUNNY_WAIT_SECONDS", 1800)),
            download_dir=Path(_env("DOWNLOAD_DIR", "downloads")),
            concurrent_tasks=base_conc,
            download_slots=max(1, _env_int("DOWNLOAD_SLOTS", base_conc + 1)),
            encode_slots=max(1, _env_int("ENCODE_SLOTS", max(1, base_conc))),
            upload_slots=max(1, _env_int("UPLOAD_SLOTS", base_conc + 1)),
            hls_segment_seconds=max(1, _env_int("HLS_SEGMENT_SECONDS", 4)),
            gop_seconds=max(1, _env_int("GOP_SECONDS", 2)),
            fragment_threads=max(1, _env_int("FRAGMENT_THREADS", 32)),
            max_height=max(240, _env_int("MAX_HEIGHT", 720)),
            preset=_env("X264_PRESET", "ultrafast"),
            crf=_env("X264_CRF", "23"),
            use_hwaccel=_env("USE_HWACCEL", "1") != "0",
            allowed_users=allowed,
        )


# ───────────────────────── ثوابت وأدوات (إعدادات Bunny الصارمة) ─────────────────────────
BUNNY_MAIN_REF = "https://iframe.mediadelivery.net/"

BUNNY_REFERERS = [
    "https://iframe.mediadelivery.net/",
    "https://clip.place/",
]
VK_REFERERS = ["https://vkvideo.ru/", "https://vk.com/", "https://ok.ru/"]
REFERER_CANDIDATES = [
    "https://iframe.mediadelivery.net/",
    "https://clip.place/",
    "https://ok.ru/",
    "https://vkvideo.ru/",
    "https://vk.com/",
]

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

URL_REGEX = re.compile(r'(https?://[^\s\'"<>\]\)]+)')
UUID_REGEX = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
HAR_URL_REGEX = re.compile(
    r'https?://[^\s\'"<>\\]*?/video\.m3u8(?:\?[^\s\'"<>\\]*)?', re.IGNORECASE
)
EP_PATTERN = re.compile(
    r"num\s*:\s*(\d+)\s*,\s*servers\s*:\s*(\[\s*\{.*?\}\s*\])", re.DOTALL
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

CANCEL_MARK = "BATCH_CANCELLED_BY_USER"


class BatchCancelled(Exception):
    pass


def clean_extracted_url(raw_url: str) -> str:
    u = raw_url.strip().replace(r"\/", "/")
    if "](" in u:
        u = u.split("](")[0]
    return u.rstrip("])},;>'\"\\")


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    fmt = "%M:%S" if seconds < 3600 else "%H:%M:%S"
    return time.strftime(fmt, time.gmtime(seconds))


def mb(size_bytes: float) -> float:
    return size_bytes / (1024 * 1024)


def md(s: str) -> str:
    return str(s).replace("`", "'")


def silent_unlink(*paths) -> None:
    for p in paths:
        with contextlib.suppress(Exception):
            if os.path.exists(p):
                os.remove(p)


async def safe_edit(msg, text: str) -> None:
    with contextlib.suppress(Exception):
        await msg.edit(text)


async def safe_delete(msg) -> None:
    with contextlib.suppress(Exception):
        await msg.delete()


def normalize_to_max_height(url: str, height: int = 720) -> str:
    return re.sub(r"/(?:1080p|1440p|2160p|4k)/", f"/{height}p/", url, flags=re.IGNORECASE)


# ───────────────────────── نماذج البيانات ─────────────────────────
@dataclass(frozen=True)
class Lesson:
    index: int
    title: str
    url: str


@dataclass
class JobResult:
    index: int
    title: str
    size_mb: float
    cdn_url: str
    raw_url: str
    format_label: str
    source_url: str
    release: str
    extra_links: str = ""


@dataclass
class JobFailure:
    index: int
    title: str
    reason: str
    source_url: str


@dataclass
class BatchRun:
    chosen_format: str
    delogo: bool
    dl_sem: asyncio.Semaphore
    enc_sem: asyncio.Semaphore
    up_sem: asyncio.Semaphore
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    completed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    active_tasks: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancelled: bool = False
    finished: bool = False
    worker: Optional[asyncio.Task] = None


@dataclass
class UserState:
    staged: list = field(default_factory=list)
    counter: int = 1
    delogo_enabled: bool = True
    run: Optional[BatchRun] = None

    @property
    def is_running(self) -> bool:
        return self.run is not None and not self.run.finished


# ───────────────────────── استخراج الروابط ─────────────────────────
def _quality(url_str: str) -> int:
    u = url_str.lower()
    for q in (2160, 1440, 1080, 720, 480, 360, 240):
        if f"{q}p" in u:
            return q
    return 0


def _quality_rank(url_str: str, max_h: int = 720) -> tuple:
    q = _quality(url_str)
    if q == 0:
        return (1, max_h - 1)
    return (1, q) if q <= max_h else (0, -q)


def _build_har_lessons(raw_urls) -> list:
    videos: dict = {}
    order: list = []
    for raw in raw_urls:
        clean_u = clean_extracted_url(raw)
        u_lower = clean_u.lower()
        if "playlist.m3u8" in u_lower or "video.m3u8" not in u_lower:
            continue
        m = UUID_REGEX.search(clean_u)
        if "b-cdn.net" in u_lower and not m:
            continue
        key = m.group(1).lower() if m else clean_u.split("?")[0]
        if key not in videos:
            videos[key] = clean_u
            order.append(key)
        elif _quality_rank(clean_u) > _quality_rank(videos[key]):
            videos[key] = clean_u
    order.reverse()
    return [Lesson(i, f"محاضرة {i}", videos[k]) for i, k in enumerate(order, start=1)]


def parse_har_content(content: str) -> list:
    raw_urls = HAR_URL_REGEX.findall(content.replace(r"\/", "/"))
    return _build_har_lessons(raw_urls) if raw_urls else []


_DELIM_B = re.compile(rb'[\s\'"<>\\]')
_SCHEME_B = re.compile(rb"https?://", re.IGNORECASE)
_TAIL_B = re.compile(rb'video\.m3u8(?:\?[^\s\'"<>\\]*)?', re.IGNORECASE)
_WINDOW = 3000


def _find_all(buf, needle: bytes) -> list:
    out, pos, find = [], 0, buf.find
    while True:
        i = find(needle, pos)
        if i < 0:
            return out
        out.append(i)
        pos = i + len(needle)


def _url_around(buf, i: int) -> Optional[str]:
    left = buf[max(0, i - _WINDOW) : i].replace(b"\\/", b"/")
    if not left.endswith(b"/"):
        return None
    last = 0
    for m in _DELIM_B.finditer(left):
        last = m.end()
    tail = left[last:]
    sm = _SCHEME_B.search(tail)
    if not sm:
        return None
    right = buf[i : i + _WINDOW].replace(b"\\/", b"/")
    tm = _TAIL_B.match(right)
    if not tm:
        return None
    return (tail[sm.start() :] + tm.group(0)).decode("utf-8", "ignore")


def parse_har_bytes(buf) -> list:
    hits = sorted(set(_find_all(buf, b"video.m3u8")) | set(_find_all(buf, b"VIDEO.M3U8")))
    raw_urls = [u for u in (_url_around(buf, i) for i in hits) if u]
    return _build_har_lessons(raw_urls) if raw_urls else []


def _parse_js_episodes(content: str) -> list:
    lessons = []
    for num_str, srv_str in EP_PATTERN.findall(content):
        try:
            srv_json = re.sub(r"([{,]\s*)([a-zA-Z0-9_]+)\s*:", r'\1"\2":', srv_str)
            for s in json.loads(srv_json):
                if isinstance(s, dict) and s.get("url"):
                    n = int(num_str)
                    lessons.append(Lesson(n, f"محاضرة {n}", clean_extracted_url(s["url"])))
                    break
        except Exception:
            continue
    return lessons


def _parse_json_lessons(content: str) -> list:
    try:
        data = json.loads(content)
    except Exception:
        return []
    raw_items: list = []
    if isinstance(data, dict) and isinstance(data.get("classes"), list):
        for cl in data["classes"]:
            if isinstance(cl, dict) and isinstance(cl.get("lectures"), list):
                raw_items.extend(cl["lectures"])
    if not raw_items:
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            for k in ("lessons", "items", "data", "videos", "lectures", "episodes"):
                if isinstance(data.get(k), list):
                    raw_items = data[k]
                    break
    lessons = []
    for idx, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            u = item.get("url") or item.get("lesson_url") or item.get("video_url")
            t = item.get("title") or item.get("name") or f"الدرس_{idx}"
            if u:
                lessons.append(Lesson(idx, str(t).strip(), clean_extracted_url(str(u))))
    return lessons


def _parse_plain_lines(content: str) -> list:
    lessons, counter = [], 1
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = URL_REGEX.search(s)
        if m:
            lessons.append(Lesson(counter, f"محاضرة {counter}", clean_extracted_url(m.group(1))))
            counter += 1
    return lessons


def parse_lessons_file(file_path: str) -> list:
    har_like = False
    try:
        if os.path.getsize(file_path) > 0:
            with open(file_path, "rb") as fh:
                mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    har_like = b'"log"' in mm[:4096]
                    if mm.find(b"video.m3u8") >= 0 or mm.find(b"VIDEO.M3U8") >= 0:
                        lessons = parse_har_bytes(mm)
                        if lessons:
                            return lessons
                finally:
                    mm.close()
    except Exception as exc:
        logger.warning("⚠️ المسار السريع فشل (%s)، سيتم استخدام القراءة النصية.", exc)

    if har_like and os.path.getsize(file_path) > 2 * 1024 * 1024:
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
    except Exception as exc:
        log_error(f"قراءة ملف الدروس: {file_path}", exc)
        return []

    if "b-cdn.net" in content or "video.m3u8" in content or '"log":' in content:
        lessons = parse_har_content(content)
        if lessons:
            return lessons
    for parser in (_parse_js_episodes, _parse_json_lessons, _parse_plain_lines):
        lessons = parser(content)
        if lessons:
            return lessons
    return []


# ───────────────────────── شريط التقدم ─────────────────────────
class ProgressTracker:
    MIN_INTERVAL = 2.5

    def __init__(self, message, stage_title: str, action_type: str = "تحميل"):
        self.message = message
        self.stage_title = stage_title
        self.action_type = action_type
        self.start_time = time.monotonic()
        self._last = 0.0
        self._editing = False
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def _bar(percent: float, length: int = 12) -> str:
        filled = max(0, min(length, int(percent / 100 * length)))
        return "▰" * filled + "▱" * (length - filled)

    async def callback(self, current: int, total: int) -> None:
        now = time.monotonic()
        finished = total > 0 and current >= total
        if self._editing or (not finished and now - self._last < self.MIN_INTERVAL):
            return
        self._last = now
        elapsed = max(0.001, now - self.start_time)
        speed = current / elapsed
        percent = (current / total * 100) if total > 0 else 0.0
        eta = int((total - current) / speed) if speed > 0 and total > current else 0
        icon = "📥" if self.action_type == "تحميل" else "📤"
        text = (
            f"⚡ **{md(self.stage_title)}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{icon} **العملية:** `{self.action_type}`\n"
            f"📊 **التقدم:** `[{self._bar(percent)}] {percent:.1f}%`\n"
            f"🚀 **السرعة:** `{mb(speed):.2f} MB/s`\n"
            f"⏱️ **المتبقي:** `{fmt_duration(eta)}`\n"
            f"💾 **الحجم:** `{mb(current):.2f} MB / {mb(total):.2f} MB`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        self._editing = True
        self._task = asyncio.create_task(self._edit(text))

    async def _edit(self, text: str) -> None:
        try:
            await self.message.edit(text)
        except FloodWaitError as exc:
            self._last = time.monotonic() + exc.seconds
        except Exception:
            pass
        finally:
            self._editing = False


# ───────────────────────── محرك الفيديو (10x Delogo + NVENC) ─────────────────────────
async def run_cmd(args: list) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err.decode("utf-8", "ignore")


@dataclass
class MediaInfo:
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    pix_fmt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    duration: float = 0.0
    max_gop: Optional[float] = None

    def video_stream_compatible(self, target: str, max_height: int, seg_seconds: int) -> bool:
        if self.video_codec != "h264":
            return False
        if self.pix_fmt and self.pix_fmt != "yuv420p":
            return False
        if self.height and self.height > max_height:
            return False
        if self.width and (self.width % 2 != 0 or (self.height and self.height % 2 != 0)):
            return False
        if target == "hls":
            max_allowed_gop = max(12.0, seg_seconds * 3.0)
            if self.max_gop is not None and self.max_gop > max_allowed_gop:
                return False
        return True

    def audio_stream_compatible(self, target: str) -> bool:
        if self.audio_codec is None:
            return True
        allowed = {"aac"} if target == "hls" else {"aac", "mp3"}
        return self.audio_codec in allowed


def _parse_fps(value: Optional[str]) -> Optional[float]:
    try:
        if value and "/" in value:
            n, d = value.split("/")
            return float(n) / float(d) if float(d) else None
        return float(value) if value else None
    except Exception:
        return None


def _detect_nvenc() -> bool:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if b"h264_nvenc" not in r.stdout:
            return False
        test = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return test.returncode == 0
    except Exception:
        return False


def build_delogo_filter(info: MediaInfo) -> str:
    vw = info.width or 959
    vh = info.height or 540
    if abs(vw - 959) <= 6 and abs(vh - 540) <= 6:
        x, y, w, h = DELOGO_X, DELOGO_Y, DELOGO_W, DELOGO_H
    else:
        x = round(vw * (DELOGO_X / DELOGO_REF_W))
        y = round(vh * (DELOGO_Y / DELOGO_REF_H))
        w = round(vw * (DELOGO_W / DELOGO_REF_W))
        h = round(vh * (DELOGO_H / DELOGO_REF_H))

    x = max(2, min(x, vw - 10))
    y = max(2, min(y, vh - 10))
    w = max(4, min(w, vw - x - 2))
    h = max(4, min(h, vh - y - 2))
    return f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"


class MediaEngine:
    MAPS = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_error = ""
        self.has_nvenc = cfg.use_hwaccel and _detect_nvenc()
        if self.has_nvenc:
            logger.info("🚀 تم اكتشاف وتفعيل التسريع العتادي GPU (h264_nvenc 10x Speed)!")

    async def duration_of(self, path: str) -> float:
        rc, out, _ = await run_cmd([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", path,
        ])
        if rc != 0:
            return 0.0
        try:
            return float(out.decode().strip().split()[0])
        except Exception:
            return 0.0

    async def _max_gop(self, path: str, duration: float) -> Optional[float]:
        rc, out, _ = await run_cmd([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-skip_frame", "nokey",
            "-show_entries", "packet=pts_time,flags",
            "-read_intervals", "%+60", "-of", "csv=p=0", path,
        ])
        if rc != 0:
            return None
        times: list = []
        for line in out.decode("utf-8", "ignore").splitlines():
            parts = line.strip().split(",")
            if parts and parts[0]:
                if len(parts) == 1 or "K" in parts[1]:
                    with contextlib.suppress(ValueError):
                        times.append(float(parts[0]))
        if len(times) < 2:
            return min(duration, 60.0) if duration > 0 else 4.0
        times.sort()
        return max(b - a for a, b in zip(times, times[1:]))

    async def probe(self, path: str) -> MediaInfo:
        rc, out, _ = await run_cmd([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path
        ])
        if rc != 0:
            return MediaInfo()
        try:
            data = json.loads(out.decode("utf-8", "ignore"))
            streams = data.get("streams", [])
            v = next(
                (
                    s for s in streams
                    if s.get("codec_type") == "video"
                    and not s.get("disposition", {}).get("attached_pic")
                ),
                None,
            )
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            v = v or {}
            info = MediaInfo(
                video_codec=v.get("codec_name"),
                audio_codec=(a or {}).get("codec_name"),
                pix_fmt=v.get("pix_fmt"),
                width=v.get("width"),
                height=v.get("height"),
                fps=_parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate")),
            )
            with contextlib.suppress(Exception):
                info.duration = float(data.get("format", {}).get("duration") or 0)
            if (
                info.video_codec == "h264"
                and (not info.pix_fmt or info.pix_fmt == "yuv420p")
                and (not info.height or info.height <= self.cfg.max_height)
            ):
                info.max_gop = await self._max_gop(path, info.duration)
            return info
        except Exception:
            return MediaInfo()

    def _input(self, src: str) -> list:
        base = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-threads", "0",
        ]
        if self.has_nvenc:
            base += ["-hwaccel", "auto"]
        return base + ["-fflags", "+genpts+discardcorrupt", "-i", src]

    def _gop(self) -> int:
        return max(1, min(self.cfg.gop_seconds, self.cfg.hls_segment_seconds))

    def _audio_args(self, info: MediaInfo, target: str) -> list:
        if not info.audio_codec:
            return ["-an"]
        if info.audio_stream_compatible(target):
            return ["-c:a", "copy"]
        return [
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-af", "aresample=async=1:first_pts=0",
        ]

    def _transcode_args(
        self,
        info: MediaInfo,
        target: str,
        crf: Optional[str] = None,
        delogo: bool = False,
    ) -> list:
        gop = self._gop()
        fps = info.fps if info.fps and 5 <= info.fps <= 120 else 30.0
        g = max(1, round(fps * gop))
        target_crf = crf or self.cfg.crf

        vf_parts = []
        if delogo:
            vf_parts.append(build_delogo_filter(info))

        need_scale = (
            (info.height and info.height > self.cfg.max_height)
            or (info.width and info.width % 2 != 0)
            or (info.height and info.height % 2 != 0)
            or not info.width
        )
        if need_scale:
            vf_parts.append(f"scale=-2:'min({self.cfg.max_height},trunc(ih/2)*2)':flags=fast_bilinear")

        vf_arg = ["-vf", ",".join(vf_parts)] if vf_parts else []

        if self.has_nvenc:
            v_args = vf_arg + [
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-tune", "ull",
                "-rc", "vbr",
                "-cq", target_crf,
                "-b:v", "0",
                "-spatial-aq", "0",
                "-temporal-aq", "0",
                "-pix_fmt", "yuv420p",
                "-g", str(g),
                "-no-scenecut", "1",
                "-force_key_frames", f"expr:gte(t,n_forced*{gop})",
            ]
        else:
            v_args = vf_arg + [
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
                "-crf", target_crf,
                "-profile:v", "main",
                "-pix_fmt", "yuv420p",
                "-g", str(g),
                "-sc_threshold", "0",
                "-force_key_frames", f"expr:gte(t,n_forced*{gop})",
            ]
        return v_args + self._audio_args(info, target) + ["-max_muxing_queue_size", "4096"]

    def _candidate_modes(
        self,
        target: str,
        info: MediaInfo,
        force_transcode: bool,
        crf: Optional[str],
        delogo: bool = False,
    ) -> list:
        attempts = []
        if not force_transcode and not delogo and info.video_stream_compatible(
            target, self.cfg.max_height, self._gop()
        ):
            if info.audio_stream_compatible(target):
                attempts.append(("copy", ["-c", "copy", "-avoid_negative_ts", "make_zero"]))
            attempts.append((
                "copy_video",
                ["-c:v", "copy"] + self._audio_args(info, target) + ["-avoid_negative_ts", "make_zero"],
            ))
        label = "delogo_10x" if delogo else "transcode"
        attempts.append((label, self._transcode_args(info, target, crf, delogo=delogo)))
        return attempts

    def _hls_out(self, m3u8: str, ts: str) -> list:
        return [
            "-muxdelay", "0", "-muxpreload", "0",
            "-f", "hls",
            "-hls_time", str(self.cfg.hls_segment_seconds),
            "-hls_playlist_type", "vod",
            "-hls_list_size", "0",
            "-hls_segment_type", "mpegts",
            "-hls_flags", "single_file+independent_segments",
            "-hls_segment_filename", ts,
            m3u8,
        ]

    @staticmethod
    def _size_ok(path: str, min_size: int = 1000) -> bool:
        return os.path.isfile(path) and os.path.getsize(path) > min_size

    async def _run(self, cmd: list, label: str) -> bool:
        rc, _, err = await run_cmd(cmd)
        if rc != 0:
            self.last_error = f"[{label}] {err.strip()[-300:]}"
            logger.warning("⚠️ ffmpeg %s فشل (rc=%s): %s", label, rc, err.strip()[-400:])
        return rc == 0

    async def _duration_ok(self, path: str, src_duration: float) -> bool:
        if src_duration <= 0:
            return True
        dur = await self.duration_of(path)
        ok = dur >= src_duration * 0.9
        if not ok:
            self.last_error = f"الناتج مبتور ({dur:.0f}s من {src_duration:.0f}s)"
            logger.warning("⚠️ %s", self.last_error)
        return ok

    async def _hls_valid(self, m3u8: str, ts: str, src_duration: float) -> bool:
        if not (self._size_ok(ts) and self._size_ok(m3u8, 10)):
            return False
        try:
            text = Path(m3u8).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        if "#EXT-X-ENDLIST" not in text or "#EXT-X-BYTERANGE" not in text:
            self.last_error = "قائمة HLS الناتجة غير مكتملة"
            return False
        return await self._duration_ok(ts, src_duration)

    async def build_hls(
        self,
        src: str,
        info: MediaInfo,
        m3u8: str,
        ts: str,
        force_transcode: bool = False,
        crf: Optional[str] = None,
        delogo: bool = False,
    ) -> Optional[str]:
        for mode, codec_args in self._candidate_modes("hls", info, force_transcode, crf, delogo=delogo):
            silent_unlink(m3u8, ts)
            cmd = self._input(src) + self.MAPS + codec_args + self._hls_out(m3u8, ts)
            if await self._run(cmd, f"hls-{mode}") and await self._hls_valid(m3u8, ts, info.duration):
                return mode
        silent_unlink(m3u8, ts)
        return None

    async def build_mp4(
        self,
        src: str,
        info: MediaInfo,
        out: str,
        force_transcode: bool = False,
        crf: Optional[str] = None,
        delogo: bool = False,
    ) -> Optional[str]:
        for mode, codec_args in self._candidate_modes("mp4", info, force_transcode, crf, delogo=delogo):
            silent_unlink(out)
            cmd = (
                self._input(src) + self.MAPS + codec_args +
                ["-movflags", "+faststart", "-f", "mp4", out]
            )
            if (
                await self._run(cmd, f"mp4-{mode}")
                and self._size_ok(out)
                and await self._duration_ok(out, info.duration)
            ):
                return mode
        silent_unlink(out)
        return None


async def rewrite_playlist_name(m3u8_path: str, old_name: str, new_name: str) -> None:
    if old_name == new_name:
        return
    async with aiofiles.open(m3u8_path, "r", encoding="utf-8") as f:
        lines = await f.readlines()
    out = [f"{new_name}\n" if line.strip() == old_name else line for line in lines]
    async with aiofiles.open(m3u8_path, "w", encoding="utf-8") as f:
        await f.writelines(out)


# ───────────────────────── التنزيل الصاروخي (مع دعم Bunny المباشر) ─────────────────────────
USE_ARIA2 = bool(shutil.which("aria2c")) and os.environ.get("ARIA2", "1") != "0"


class Downloader:
    PARTIAL = (".part", ".ytdl", ".temp", ".tmp")

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._domain_referer_cache: dict[str, str] = {}

    def _url_candidates(self, raw_url: str) -> list:
        url = clean_extracted_url(raw_url)
        cands: list = []
        m = re.search(r"oid=(-?\d+)&id=(\d+)", url)
        if m:
            oid, vid = m.group(1), m.group(2)
            cands += [f"https://vkvideo.ru/video{oid}_{vid}", f"https://vk.com/video{oid}_{vid}"]
        norm = normalize_to_max_height(url, self.cfg.max_height)
        cands.append(norm)
        if norm != url:
            cands.append(url)
        return cands

    def _ordered_referers(self, target_url: str) -> list[str]:
        low = target_url.lower()
        domain = urlparse(low).netloc
        if "b-cdn.net" in low or "mediadelivery.net" in low:
            return list(BUNNY_REFERERS)
        if any(x in low for x in ("vk.com", "vkvideo.ru", "vkuser.net", "userapi.com")):
            base = list(VK_REFERERS)
        else:
            base = list(REFERER_CANDIDATES)
        cached = self._domain_referer_cache.get(domain)
        if cached and cached in base:
            base.remove(cached)
            base.insert(0, cached)
        return base

    def _pick_output(self, work_dir: Path, file_id: str) -> Optional[Path]:
        files = [
            p for p in work_dir.glob(f"{file_id}_raw.*")
            if p.is_file()
            and not any(s in p.name for s in self.PARTIAL)
            and p.stat().st_size > 1000
        ]
        return max(files, key=lambda p: p.stat().st_size) if files else None

    @staticmethod
    def _cleanup(work_dir: Path, file_id: str) -> None:
        for f in work_dir.glob(f"{file_id}_raw.*"):
            with contextlib.suppress(Exception):
                f.unlink()

    def _ffmpeg_direct_hls(
        self,
        target_url: str,
        work_dir: Path,
        file_id: str,
        ref: str,
        cancel_check: Callable[[], bool],
    ) -> Optional[Path]:
        if cancel_check():
            raise BatchCancelled(CANCEL_MARK)
        out_path = work_dir / f"{file_id}_raw.mp4"
        silent_unlink(str(out_path))
        hdr_lines = (
            f"Referer: {ref}\r\n"
            f"Origin: {ref}\r\n"
            f"User-Agent: {BASE_HEADERS['User-Agent']}\r\n"
            f"Accept: */*\r\n"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-headers", hdr_lines,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", target_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            str(out_path),
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            while proc.poll() is None:
                if cancel_check():
                    proc.kill()
                    raise BatchCancelled(CANCEL_MARK)
                time.sleep(0.5)
            if proc.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 1000:
                return out_path
        except BatchCancelled:
            raise
        except Exception as exc:
            logger.warning("⚠️ FFmpeg direct fallback فشل: %s", exc)
        silent_unlink(str(out_path))
        return None

    def _execute_ytdl(
        self,
        target_url: str,
        work_dir: Path,
        file_id: str,
        loop: asyncio.AbstractEventLoop,
        tracker: Optional[ProgressTracker],
        cancel_check: Callable[[], bool],
    ) -> tuple:
        low = target_url.lower()
        domain = urlparse(low).netloc
        referers = self._ordered_referers(target_url)
        is_hls = ".m3u8" in low
        is_bunny = "b-cdn.net" in low or "mediadelivery.net" in low
        target = str(work_dir / f"{file_id}_raw.%(ext)s")
        last_hook = 0.0
        h = self.cfg.max_height

        def hook(d: dict) -> None:
            nonlocal last_hook
            if cancel_check():
                raise BatchCancelled(CANCEL_MARK)
            if d.get("status") != "downloading" or not tracker:
                return
            now = time.monotonic()
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total <= 0 and d.get("fragment_count") and d.get("fragment_index"):
                frag_idx = d.get("fragment_index") or 0
                frag_cnt = max(1, d.get("fragment_count") or 1)
                if frag_idx > 0 and done > 0:
                    total = int(done * (frag_cnt / frag_idx))
            if total > 0 and (now - last_hook > 2.0 or done >= total):
                last_hook = now
                asyncio.run_coroutine_threadsafe(tracker.callback(done, total), loop)

        fmt_rule = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        last_err = ""
        attempts: list = []
        if USE_ARIA2 and not is_hls:
            attempts.append((referers[0], True))
        attempts += [(r, False) for r in referers]

        for ref, use_aria in attempts:
            if cancel_check():
                raise BatchCancelled(CANCEL_MARK)

            headers = dict(
                BASE_HEADERS,
                Referer=ref,
                Origin=ref if is_bunny else ref.rstrip("/"),
            )
            opts = {
                "outtmpl": target,
                "referer": ref,
                "http_headers": headers,
                "concurrent_fragment_downloads": self.cfg.fragment_threads,
                "http_chunk_size": 10 * 1024 * 1024,
                "buffersize": 1024 * 64,
                "retries": 6,
                "fragment_retries": 6,
                "file_access_retries": 3,
                "skip_unavailable_fragments": False,
                "keep_fragments": False,
                "continuedl": True,
                "socket_timeout": 25,
                "quiet": True,
                "no_warnings": True,
                "no_color": True,
                "noprogress": True,
                "noplaylist": True,
                "cachedir": False,
                "nocheckcertificate": True,
                "hls_use_mpegts": True,
                "progress_hooks": [hook],
                "format": fmt_rule,
            }
            if is_bunny and is_hls:
                opts["allowed_extractors"] = ["generic"]

            if use_aria:
                opts["external_downloader"] = {"default": "aria2c", "m3u8": "native"}
                opts["external_downloader_args"] = {
                    "aria2c": [
                        "-x16", "-s16", "-j16", "-k1M",
                        "--min-split-size=1M",
                        "--file-allocation=none",
                        "--optimize-concurrent-downloads=true",
                    ]
                }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([target_url])
                path = self._pick_output(work_dir, file_id)
                if path:
                    if domain:
                        self._domain_referer_cache[domain] = ref
                    return path, ""
                last_err = "yt-dlp لم يُنتج أي ملف"
            except BatchCancelled:
                raise
            except Exception as exc:
                if cancel_check() or CANCEL_MARK in str(exc):
                    raise BatchCancelled(CANCEL_MARK) from exc
                last_err = ANSI_RE.sub("", str(exc)).strip()[:200]
                logger.warning("⚠️ yt-dlp (%s) فشل: %s", ref, last_err)
            self._cleanup(work_dir, file_id)

            if is_hls:
                logger.info("🔄 تجربة السحب المباشر عبر FFmpeg بـ Referer/Origin: %s", ref)
                ff_path = self._ffmpeg_direct_hls(target_url, work_dir, file_id, ref, cancel_check)
                if ff_path:
                    return ff_path, ""

        return None, last_err

    def download(
        self,
        url: str,
        work_dir: Path,
        file_id: str,
        loop: asyncio.AbstractEventLoop,
        tracker: Optional[ProgressTracker],
        cancel_check: Callable[[], bool],
    ) -> Path:
        errors: list = []
        for target in self._url_candidates(url):
            path, err = self._execute_ytdl(target, work_dir, file_id, loop, tracker, cancel_check)
            if path:
                return path
            if err:
                errors.append(err)
        raise RuntimeError("تعذر تنزيل الفيديو: " + (errors[-1] if errors else "سبب غير معروف"))


# ───────────────────────── Bunny Stream ─────────────────────────
class BunnyUploader:
    CHUNK = 16 * 1024 * 1024
    STATUS_FINISHED = 4
    STATUS_FAILED = (5, 6)

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = f"https://video.bunnycdn.com/library/{cfg.bunny_library_id}/videos"
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def _headers(self) -> dict:
        return {"AccessKey": self.cfg.bunny_api_key, "Accept": "application/json"}

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=64,
                limit_per_host=32,
                ttl_dns_cache=600,
                keepalive_timeout=120,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=600)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def create_video(self, title: str) -> str:
        sess = await self.session()
        async with sess.post(
            self.base_url, json={"title": title[:200]}, headers=self._headers
        ) as resp:
            body = await resp.text()
            if resp.status not in (200, 201):
                raise RuntimeError(f"خطأ إنشاء فيديو Bunny ({resp.status}): {body[:200]}")
            return json.loads(body)["guid"]

    async def upload_file(
        self,
        video_id: str,
        file_path: str,
        progress_cb: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> None:
        sess = await self.session()
        size = os.path.getsize(file_path)

        async def streamer():
            sent = 0
            async with aiofiles.open(file_path, "rb") as f:
                while True:
                    chunk = await f.read(self.CHUNK)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if progress_cb:
                        await progress_cb(sent, size)
                    yield chunk

        headers = dict(
            self._headers,
            **{
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
            },
        )
        async with sess.put(
            f"{self.base_url}/{video_id}", headers=headers, data=streamer()
        ) as resp:
            if resp.status not in (200, 201):
                body = await resp.text()
                raise RuntimeError(f"فشل رفع الملف إلى Bunny ({resp.status}): {body[:200]}")

    async def get_info(self, video_id: str) -> dict:
        sess = await self.session()
        async with sess.get(f"{self.base_url}/{video_id}", headers=self._headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json()

    async def wait_until_ready(
        self,
        video_id: str,
        on_update: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> dict:
        deadline = time.monotonic() + self.cfg.bunny_wait_seconds
        info: dict = {}
        while True:
            try:
                info = await self.get_info(video_id)
            except Exception as exc:
                logger.warning("⚠️ تعذر قراءة حالة Bunny: %s", exc)
            status = info.get("status")
            if status == self.STATUS_FINISHED:
                return dict(info, ready=True)
            if status in self.STATUS_FAILED:
                raise RuntimeError(f"فشلت معالجة الفيديو داخل Bunny (status={status}).")
            if time.monotonic() >= deadline:
                return dict(info, ready=False)
            if on_update:
                await on_update(f"🐰 **Bunny يعالج الفيديو...** `{info.get('encodeProgress', 0)}%`")
            await asyncio.sleep(4)

    def build_urls(self, video_id: str, resolutions: str = "") -> dict:
        host, lib = self.cfg.bunny_cdn_hostname, self.cfg.bunny_library_id
        nums = sorted(
            (
                int(r.strip()[:-1])
                for r in (resolutions or "").split(",")
                if r.strip().endswith("p") and r.strip()[:-1].isdigit()
            ),
            reverse=True,
        )
        best = next(
            (n for n in nums if n <= self.cfg.max_height),
            nums[-1] if nums else self.cfg.max_height,
        )
        return {
            "hls_url": f"https://{host}/{video_id}/playlist.m3u8",
            "mp4_url": f"https://{host}/{video_id}/play_{best}p.mp4",
            "embed_url": f"https://iframe.mediadelivery.net/embed/{lib}/{video_id}",
            "play_url": f"https://iframe.mediadelivery.net/play/{lib}/{video_id}",
            "best": f"{best}p",
        }


# ───────────────────────── GitHub Releases ─────────────────────────
@dataclass
class UploadedAsset:
    name: str
    tag: str
    cdn_url: str
    github_url: str


def next_release_tag(tag: str) -> str:
    m = re.search(r"v(\d+)", tag)
    return f"v{int(m.group(1)) + 1}" if m else f"{tag}_next"


class ReleaseUploader:
    CHUNK = 16 * 1024 * 1024
    MAX_ATTEMPTS = 5

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tag = cfg.release_tag
        self._session: Optional[aiohttp.ClientSession] = None
        self._upload_urls: dict = {}
        self._counts: dict = {}
        self._release_lock = asyncio.Lock()
        self._switch_lock = asyncio.Lock()

    @property
    def _api_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.cfg.github_token}",
            "Accept": "application/vnd.github+json",
        }

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ttl_dns_cache=600,
                keepalive_timeout=120,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=600)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _upload_url(self, tag: str) -> str:
        async with self._release_lock:
            if tag in self._upload_urls:
                return self._upload_urls[tag]
            session = await self.session()
            base = f"https://api.github.com/repos/{self.cfg.repo_owner}/{self.cfg.repo_name}/releases"
            async with session.get(f"{base}/tags/{tag}", headers=self._api_headers) as res:
                if res.status == 200:
                    url = (await res.json())["upload_url"].split("{")[0]
                    self._upload_urls[tag] = url
                    return url
                if res.status == 401:
                    raise RuntimeError("GITHUB_TOKEN غير صالح (401).")
            payload = {
                "tag_name": tag,
                "name": f"Release {tag}",
                "draft": False,
                "prerelease": False,
            }
            async with session.post(base, json=payload, headers=self._api_headers) as res:
                if res.status in (200, 201):
                    url = (await res.json())["upload_url"].split("{")[0]
                    self._upload_urls[tag] = url
                    return url
                raise RuntimeError(f"خطأ إنشاء Release {tag}: {(await res.text())[:200]}")

    async def _switch_release(self, failed_tag: str) -> None:
        async with self._switch_lock:
            if self.tag != failed_tag:
                return
            nxt = next_release_tag(failed_tag)
            logger.warning("🚨 تحويل الإصدار السحابي إلى: %s ...", nxt)
            await self._upload_url(nxt)
            self.tag = nxt

    async def _stream(self, path: str, size: int, cb):
        sent = 0
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(self.CHUNK)
                if not chunk:
                    break
                sent += len(chunk)
                if cb:
                    await cb(sent, size)
                yield chunk

    def _make_asset(self, real_name: str, tag: str, github_url: Optional[str]) -> UploadedAsset:
        if not github_url:
            github_url = (
                f"https://github.com/{self.cfg.repo_owner}/{self.cfg.repo_name}"
                f"/releases/download/{tag}/{quote(real_name)}"
            )
        if self.cfg.cdn_domain:
            path = github_url.split("://github.com", 1)[-1]
            cdn = f"{self.cfg.cdn_domain}{path}"
        else:
            cdn = github_url
        return UploadedAsset(real_name, tag, cdn, github_url)

    async def upload(
        self,
        file_path: str,
        name: str,
        progress_cb: Optional[Callable[[int, int], Awaitable[None]]] = None,
        force_tag: Optional[str] = None,
    ) -> UploadedAsset:
        if not self.cfg.github_ready:
            raise RuntimeError("إعدادات GitHub ناقصة (GITHUB_TOKEN / REPO_OWNER / REPO_NAME).")
        session = await self.session()
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".m3u8"):
            mime = "application/vnd.apple.mpegurl"
        elif name.endswith(".ts"):
            mime = "video/mp2t"
        size = os.path.getsize(file_path)

        if size <= 0:
            raise RuntimeError(f"الملف {name} فارغ (0 بايت) ولا يمكن رفعه.")
        if size > MAX_GITHUB_ASSET_BYTES:
            raise RuntimeError(f"حجم الملف ({mb(size):.2f} MB) يتجاوز حد GitHub (2GB).")

        current_name = name
        last_err = "غير معروف"

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            if not force_tag and self._counts.get(self.tag, 0) >= 950:
                await self._switch_release(self.tag)
            tag = force_tag or self.tag
            try:
                url = await self._upload_url(tag)
                headers = {
                    "Authorization": f"Bearer {self.cfg.github_token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": mime,
                    "Content-Length": str(size),
                }
                async with session.post(
                    url,
                    params={"name": current_name},
                    headers=headers,
                    data=self._stream(file_path, size, progress_cb),
                ) as res:
                    if res.status in (200, 201):
                        data = await res.json()
                        self._counts[tag] = self._counts.get(tag, 0) + 1
                        return self._make_asset(
                            data.get("name", current_name),
                            tag,
                            data.get("browser_download_url"),
                        )
                    body = await res.text()
                    last_err = f"HTTP {res.status}: {body[:250]}"

                    if res.status == 401:
                        raise RuntimeError("GITHUB_TOKEN غير صالح (401).")
                    if res.status == 422 and "already_exists" in body:
                        current_name = f"{int(time.time())}_{uuid.uuid4().hex[:4]}_{name}"
                        continue
                    if res.status in (403, 429) and (
                        "rate limit" in body.lower()
                        or "abuse" in body.lower()
                        or res.headers.get("Retry-After")
                    ):
                        wait = min(int(res.headers.get("Retry-After", "30") or 30), 120)
                        logger.warning("⏳ حد معدل GitHub، انتظار %ds...", wait)
                        await asyncio.sleep(wait)
                        continue
                    if res.status == 404:
                        self._upload_urls.pop(tag, None)
                    elif res.status in (403, 422) and not force_tag:
                        await self._switch_release(tag)
                await asyncio.sleep(2 * attempt)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                logger.warning("⚠️ رفع %s (محاولة %d): %s", current_name, attempt, last_err)
                await asyncio.sleep(2 * attempt)

        raise RuntimeError(f"فشل رفع {name} بعد {self.MAX_ATTEMPTS} محاولات: {last_err}")


# ───────────────────────── خطوط المعالجة المتوازية ─────────────────────────
@dataclass
class JobContext:
    app: "BotApp"
    run: BatchRun
    lesson: Lesson
    file_id: str
    task_dir: Path
    raw_file: Path
    info: MediaInfo
    status: object

    def path(self, name: str) -> str:
        return str(self.task_dir / name)

    async def set_status(self, text: str) -> None:
        await safe_edit(self.status, text)

    @property
    def title(self) -> str:
        return self.lesson.title

    @property
    def delogo(self) -> bool:
        return self.run.delogo


@dataclass
class PipelineOutput:
    size_mb: float
    cdn_url: str
    raw_url: str
    format_label: str
    message: str
    release: str = ""
    extra_links: str = ""


async def _shrink_mp4_until_fits(ctx: JobContext, local: str) -> str:
    eng = ctx.app.engine
    for crf in COMPRESS_CRFS:
        await ctx.set_status(f"⚠️ **الحجم يتجاوز 2GB! إعادة ضغط (CRF {crf})...**")
        if not await eng.build_mp4(
            str(ctx.raw_file), ctx.info, local, force_transcode=True, crf=crf, delogo=ctx.delogo
        ):
            raise RuntimeError(f"فشل الضغط: {eng.last_error[-150:]}")
        if os.path.getsize(local) <= MAX_GITHUB_ASSET_BYTES:
            return local
    raise RuntimeError("حجم الفيديو يتجاوز 2GB حتى بعد أقصى ضغط.")


async def run_bunny(ctx: JobContext) -> PipelineOutput:
    cfg, eng, up = ctx.app.cfg, ctx.app.engine, ctx.app.bunny_uploader
    if not cfg.bunny_ready:
        raise RuntimeError(
            "إعدادات Bunny ناقصة (BUNNY_API_KEY / BUNNY_LIBRARY_ID / BUNNY_CDN_HOSTNAME)."
        )
    local = str(ctx.raw_file)
    if ctx.delogo:
        async with ctx.run.enc_sem:
            await ctx.set_status("🎯 **طمس الشعار بسرعة 10x قبل الرفع لـ Bunny...**")
            cleaned = ctx.path(f"{ctx.file_id}_delogo.mp4")
            if not await eng.build_mp4(local, ctx.info, cleaned, force_transcode=True, delogo=True):
                raise RuntimeError(f"فشل طمس الشعار: {eng.last_error[-150:]}")
            silent_unlink(local)
            local = cleaned

    size = os.path.getsize(local)

    async with ctx.run.up_sem:
        await ctx.set_status("🐰 **إنشاء سجل الفيديو في Bunny Stream...**")
        video_id = await up.create_video(ctx.title)
        tracker = ProgressTracker(ctx.status, f"رفع Bunny: {ctx.title[:20]}", "رفع")
        await up.upload_file(video_id, local, tracker.callback)

    silent_unlink(local)

    await ctx.set_status("🐰 **تم الرفع — بانتظار معالجة Bunny السحابية...**")
    state = {"ready": False}
    if cfg.bunny_wait_seconds > 0:
        state = await up.wait_until_ready(video_id, ctx.set_status)
    urls = up.build_urls(video_id, state.get("availableResolutions") or "")

    dl_tag = " + طمس الشعار 🎯" if ctx.delogo else ""
    note = "" if state.get("ready") else (
        "\n\n⏳ **ملاحظة:** الفيديو ما زال قيد المعالجة في Bunny، الروابط ستعمل عند اكتماله."
    )
    msg = (
        f"🎉 **تم الرفع إلى Bunny Stream{dl_tag}!**\n\n"
        f"📌 **العنوان:** `{md(ctx.title)}`\n"
        f"🆔 **المعرف:** `{video_id}`\n"
        f"📺 **أعلى جودة (≤{cfg.max_height}p):** `{urls['best']}`\n"
        f"💾 **حجم الملف:** `{mb(size):.2f} MB`\n\n"
        f"📺 **HLS (M3U8):**\n`{urls['hls_url']}`\n\n"
        f"🔗 **MP4 مباشر:**\n`{urls['mp4_url']}`\n\n"
        f"🌐 **Embed:**\n`{urls['embed_url']}`\n\n"
        f"▶️ **Play:**\n`{urls['play_url']}`{note}"
    )
    return PipelineOutput(
        mb(size),
        urls["hls_url"],
        urls["mp4_url"],
        f"Bunny Stream ({urls['best']}{dl_tag})",
        msg,
        release="Bunny",
        extra_links=f"Embed: {urls['embed_url']} | Play: {urls['play_url']}",
    )


async def run_m3u8_fast(ctx: JobContext) -> PipelineOutput:
    cfg, eng, up = ctx.app.cfg, ctx.app.engine, ctx.app.uploader
    ts_name, m3u8_name = f"{ctx.file_id}.ts", f"{ctx.file_id}.m3u8"
    local_ts, local_m3u8 = ctx.path(ts_name), ctx.path(m3u8_name)

    async with ctx.run.enc_sem:
        action_txt = (
            f"🎯 **طمس الشعار 10x + تقطيع HLS {cfg.max_height}p...**"
            if ctx.delogo
            else f"⚡ **تقطيع HLS {cfg.max_height}p (TS واحد + Byte-Range)...**"
        )
        await ctx.set_status(action_txt)
        mode = await eng.build_hls(
            str(ctx.raw_file), ctx.info, local_m3u8, local_ts, delogo=ctx.delogo
        )
        if not mode:
            raise RuntimeError(f"فشل تقطيع HLS: {eng.last_error[-150:]}")

        size = os.path.getsize(local_ts)
        if size > MAX_GITHUB_ASSET_BYTES:
            for crf in COMPRESS_CRFS:
                await ctx.set_status(f"⚠️ **الحجم يتجاوز 2GB! إعادة ترميز بـ CRF {crf}...**")
                mode = await eng.build_hls(
                    str(ctx.raw_file),
                    ctx.info,
                    local_m3u8,
                    local_ts,
                    force_transcode=True,
                    crf=crf,
                    delogo=ctx.delogo,
                )
                if not mode:
                    raise RuntimeError(f"فشل إعادة الترميز: {eng.last_error[-150:]}")
                size = os.path.getsize(local_ts)
                if size <= MAX_GITHUB_ASSET_BYTES:
                    break
        if size > MAX_GITHUB_ASSET_BYTES:
            raise RuntimeError("حجم الفيديو يتجاوز 2GB حتى بعد أقصى ضغط.")

    silent_unlink(str(ctx.raw_file))

    async with ctx.run.up_sem:
        tracker = ProgressTracker(ctx.status, f"رفع TS: {ctx.title[:20]}", "رفع")
        ts_asset = await up.upload(local_ts, ts_name, tracker.callback)
        await rewrite_playlist_name(local_m3u8, ts_name, ts_asset.name)
        pl_asset = await up.upload(local_m3u8, m3u8_name, force_tag=ts_asset.tag)

    mode_labels = {
        "copy": "نسخ لحظي بدون ترميز",
        "copy_video": "نسخ الفيديو + تحويل الصوت",
        "delogo_10x": f"طمس الشعار 10x ({'NVENC GPU' if eng.has_nvenc else 'Ultrafast CPU'})",
        "transcode": f"ترميز {cfg.max_height}p",
    }
    kind = mode_labels.get(mode, mode)
    msg = (
        f"🎉 **تم التقطيع والرفع بنجاح!**\n\n"
        f"📌 **العنوان:** `{md(ctx.title)}`\n"
        f"⚡ **النوع:** `HLS (TS واحد + Byte-Range)` ({kind})\n"
        f"📦 **الإصدار:** `{pl_asset.tag}`\n"
        f"💾 **الحجم:** `{mb(size):.2f} MB`\n\n"
        f"📺 **رابط M3U8 (CDN):**\n`{pl_asset.cdn_url}`\n\n"
        f"🐙 **رابط M3U8 الخام (GitHub):**\n`{pl_asset.github_url}`\n\n"
        f"📦 **ملف TS:**\n`{ts_asset.cdn_url}`"
    )
    return PipelineOutput(
        mb(size),
        pl_asset.cdn_url,
        pl_asset.github_url,
        f"HLS ({kind})",
        msg,
        release=pl_asset.tag,
    )


async def run_mp4(ctx: JobContext) -> PipelineOutput:
    eng, up = ctx.app.engine, ctx.app.uploader
    name = f"{ctx.file_id}.mp4"
    local = ctx.path(name)

    async with ctx.run.enc_sem:
        action_txt = (
            "🎯 **طمس الشعار 10x + توليد MP4 FastStart...**"
            if ctx.delogo
            else "⚡ **توليد MP4 FastStart (يبدأ التشغيل قبل اكتمال التحميل)...**"
        )
        await ctx.set_status(action_txt)
        mode = await eng.build_mp4(str(ctx.raw_file), ctx.info, local, delogo=ctx.delogo)
        if not mode:
            raise RuntimeError(f"تعذر بناء ملف MP4: {eng.last_error[-150:]}")

        if os.path.getsize(local) > MAX_GITHUB_ASSET_BYTES:
            local = await _shrink_mp4_until_fits(ctx, local)
        size = os.path.getsize(local)

    silent_unlink(str(ctx.raw_file))

    async with ctx.run.up_sem:
        tracker = ProgressTracker(ctx.status, f"رفع MP4: {ctx.title[:20]}", "رفع")
        asset = await up.upload(local, name, tracker.callback)

    dl_tag = " + طمس الشعار 🎯" if ctx.delogo else ""
    msg = (
        f"🎉 **تم الرفع بنجاح (MP4 FastStart{dl_tag})!**\n\n"
        f"📌 **العنوان:** `{md(ctx.title)}`\n"
        f"📦 **الإصدار:** `{asset.tag}`\n"
        f"💾 **الحجم:** `{mb(size):.2f} MB`\n\n"
        f"🔗 **رابط مباشر (CDN):**\n`{asset.cdn_url}`\n\n"
        f"🐙 **الرابط الخام (GitHub):**\n`{asset.github_url}`"
    )
    return PipelineOutput(
        mb(size), asset.cdn_url, asset.github_url, f"MP4 FastStart{dl_tag}", msg, release=asset.tag
    )


async def run_original(ctx: JobContext) -> PipelineOutput:
    eng, up = ctx.app.engine, ctx.app.uploader
    ext = ctx.raw_file.suffix or ".mp4"
    name = f"{ctx.file_id}_original{ext}"
    target = str(ctx.raw_file)

    if ctx.delogo:
        async with ctx.run.enc_sem:
            await ctx.set_status("🎯 **طمس الشعار بسرعة 10x...**")
            name = f"{ctx.file_id}_delogo.mp4"
            target = ctx.path(name)
            if not await eng.build_mp4(
                str(ctx.raw_file), ctx.info, target, force_transcode=True, delogo=True
            ):
                raise RuntimeError(f"فشل طمس الشعار: {eng.last_error[-150:]}")
            silent_unlink(str(ctx.raw_file))

    size = os.path.getsize(target)
    if size > MAX_GITHUB_ASSET_BYTES:
        async with ctx.run.enc_sem:
            name = f"{ctx.file_id}_shrunk.mp4"
            target = await _shrink_mp4_until_fits(ctx, ctx.path(name))
            size = os.path.getsize(target)

    async with ctx.run.up_sem:
        await ctx.set_status("⚡ **رفع الملف إلى السحابة...**")
        tracker = ProgressTracker(ctx.status, f"رفع الملف: {ctx.title[:20]}", "رفع")
        asset = await up.upload(target, name, tracker.callback)

    dl_tag = " (مع طمس الشعار 🎯)" if ctx.delogo else ""
    msg = (
        f"🎉 **تم رفع الملف بنجاح{dl_tag}!**\n\n"
        f"📌 **العنوان:** `{md(ctx.title)}`\n"
        f"📦 **الإصدار:** `{asset.tag}`\n"
        f"💾 **الحجم:** `{mb(size):.2f} MB`\n\n"
        f"🔗 **رابط مباشر (CDN):**\n`{asset.cdn_url}`\n\n"
        f"🐙 **الرابط الخام (GitHub):**\n`{asset.github_url}`"
    )
    return PipelineOutput(
        mb(size), asset.cdn_url, asset.github_url, f"الملف الأصلي{dl_tag}", msg, release=asset.tag
    )


@dataclass(frozen=True)
class FormatSpec:
    key: str
    button: str
    title: str
    runner: Callable[[JobContext], Awaitable[PipelineOutput]]
    needs_probe: bool = True
    needs_github: bool = True


FORMATS: dict = {
    spec.key: spec
    for spec in (
        FormatSpec(
            "bunny",
            "🐰 رفع إلى Bunny Stream",
            "رفع إلى Bunny Stream",
            run_bunny,
            needs_probe=False,
            needs_github=False,
        ),
        FormatSpec("m3u8_fast", "⚡ HLS (TS واحد + Byte-Range)", "تقطيع HLS", run_m3u8_fast),
        FormatSpec("mp4", "🚀 MP4 FastStart", "MP4 FastStart", run_mp4),
        FormatSpec(
            "original",
            "📁 الملف الأصلي (مع ضبط الحجم)",
            "رفع الملف الأصلي",
            run_original,
            needs_probe=False,
        ),
    )
}


# ───────────────────────── البوت ─────────────────────────
class BotApp:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cfg.download_dir.mkdir(parents=True, exist_ok=True)
        self.bot = TelegramClient("turbo_video_uploader", cfg.api_id, cfg.api_hash)
        self.uploader = ReleaseUploader(cfg)
        self.bunny_uploader = BunnyUploader(cfg)
        self.engine = MediaEngine(cfg)
        self.downloader = Downloader(cfg)
        self.pool = ThreadPoolExecutor(max_workers=max(16, cfg.download_slots * 4))
        self.states: dict = {}

    def get_state(self, user_id: int) -> UserState:
        return self.states.setdefault(user_id, UserState())

    def authorized(self, user_id: int) -> bool:
        return not self.cfg.allowed_users or user_id in self.cfg.allowed_users

    async def start(self) -> None:
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise SystemExit(f"❌ الأداة {tool} غير مثبتة. ثبّت ffmpeg أولاً.")
        if not USE_ARIA2:
            logger.info("ℹ️ aria2c غير مثبت (اختياري): apt install aria2")
        for child in self.cfg.download_dir.iterdir():
            if child.name.startswith(("lec_", "input_", "summary_")):
                with contextlib.suppress(Exception):
                    shutil.rmtree(child) if child.is_dir() else child.unlink()

        private = lambda e: e.is_private  # noqa: E731
        add = self.bot.add_event_handler
        add(self.on_start, events.NewMessage(pattern=r"^/start(?:@\w+)?$", incoming=True, func=private))
        add(self.on_status, events.NewMessage(pattern=r"^/status(?:@\w+)?$", incoming=True, func=private))
        add(self.on_cancel_cmd, events.NewMessage(pattern=r"^/cancel(?:@\w+)?$", incoming=True, func=private))
        add(self.on_message, events.NewMessage(incoming=True, func=private))
        add(self.on_cancel_button, events.CallbackQuery(data=b"cancel_batch"))
        add(self.on_reverse_button, events.CallbackQuery(data=b"reverse_staged"))
        add(self.on_delogo_toggle, events.CallbackQuery(pattern=rb"^delogo_(on|off)$"))
        add(
            self.on_run_button,
            events.CallbackQuery(pattern=rb"^run_(bunny|mp4|m3u8_fast|original)$"),
        )
        await self.bot.start(bot_token=self.cfg.bot_token)
        logger.info(
            "🚀 البوت يعمل الآن (Pipeline: تنزيل=%d | معالجة=%d | رفع=%d | Bunny=%s).",
            self.cfg.download_slots,
            self.cfg.encode_slots,
            self.cfg.upload_slots,
            "جاهز ✅" if self.cfg.bunny_ready else "غير مضبوط",
        )

    async def shutdown(self) -> None:
        logger.info("🛑 إيقاف الخادم وتنظيف الجلسات...")
        for state in self.states.values():
            if state.run:
                self._cancel_run(state)
        await self.uploader.close()
        await self.bunny_uploader.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        with contextlib.suppress(Exception):
            await self.bot.disconnect()

    async def on_start(self, event) -> None:
        if not self.authorized(event.sender_id):
            return await event.respond("⛔ غير مصرح لك باستخدام هذا البوت.")
        hw_label = "GPU NVENC p1 (10x) 🚀" if self.engine.has_nvenc else "CPU Ultrafast (10x)"
        await event.respond(
            "👋 **أهلاً بك في Turbo Ultra-Engine v7.9 (Modal & Bunny Edition)**\n\n"
            "📎 أرسل ملف `.har` / `.json` / `.txt` أو روابط مباشرة، ثم اختر الصيغة.\n\n"
            f"• **التوازي:** تحميل `{self.cfg.download_slots}` | تقطيع `{self.cfg.encode_slots}` | رفع `{self.cfg.upload_slots}`\n"
            f"• **مكتبة Bunny:** `{self.cfg.bunny_library_id}` (`{self.cfg.bunny_cdn_hostname}`)\n"
            f"• **Referer/Origin:** `{BUNNY_MAIN_REF}`\n"
            f"• **طمس الشعار:** `x={DELOGO_X}, y={DELOGO_Y}, w={DELOGO_W}, h={DELOGO_H}`\n"
            f"• **محرك الترميز:** `{hw_label}`\n\n"
            "🧭 الأوامر: `/status` • `/cancel`"
        )

    async def on_status(self, event) -> None:
        if not self.authorized(event.sender_id):
            return
        state = self.get_state(event.sender_id)
        run = state.run
        if not state.is_running:
            return await event.respond("ℹ️ لا توجد مهمة جارية حالياً.")
        dl_state = "مفعّل 🎯" if run.delogo else "بدون طمس 📺"
        await event.respond(
            f"📊 **الحالة (Pipeline v7.9)**\n"
            f"⚙️ النمط: `{FORMATS[run.chosen_format].title}`\n"
            f"🎯 الشعار: `{dl_state}`\n"
            f"🔄 نشط حالياً: `{len(run.active_tasks)}`\n"
            f"⏳ بالانتظار في الطابور: `{run.queue.qsize()}`\n"
            f"✅ ناجح: `{len(run.completed)}`  ❌ فاشل: `{len(run.failed)}`"
        )

    async def on_cancel_cmd(self, event) -> None:
        if not self.authorized(event.sender_id):
            return
        state = self.get_state(event.sender_id)
        self._cancel_run(state)
        state.staged.clear()
        await event.respond("🗑️ **تم إلغاء المهمة وتفريغ الطابور.**")

    async def on_message(self, event) -> None:
        user_id = event.sender_id
        if not self.authorized(user_id):
            return
        text = (event.raw_text or "").strip()
        if not event.document and text.startswith("/"):
            return
        state = self.get_state(user_id)

        if event.document:
            lessons = await self._lessons_from_document(event)
        else:
            lessons = self._lessons_from_text(text, state)
        if not lessons:
            return

        if state.is_running:
            for item in lessons:
                state.run.queue.put_nowait(item)
            return await event.respond(
                f"📥 **تمت إضافة ({len(lessons)}) درس إلى الطابور الجاري!**\n"
                f"⚙️ النمط: `{FORMATS[state.run.chosen_format].title}`\n"
                f"📊 المتبقي بالطابور: `{state.run.queue.qsize()}`"
            )

        state.staged = lessons
        await event.respond(
            self._preview_text("✅ **تم استخراج", lessons, state.delogo_enabled),
            buttons=self._selection_buttons(len(lessons), state.delogo_enabled),
        )

    @staticmethod
    def _preview_text(head: str, lessons: list, delogo_enabled: bool) -> str:
        count = len(lessons)
        preview = "\n".join(f"• {l.index}. {md(l.title)[:40]}" for l in lessons[:5])
        if count > 5:
            preview += f"\n• ... والمزيد ({count - 5} فيديو آخر)"
        delogo_status = (
            "🎯 **مفعّل (سيتم مسح الشعار بسرعة 10x)**"
            if delogo_enabled
            else "📺 **معطّل (سيُترك الفيديو كما هو بدون مسح)**"
        )
        return (
            f"{head} ({count}) محاضرة!**\n\n"
            f"📋 **معاينة:**\n{preview}\n\n"
            f"🛡️ **حالة طمس الشعار:** {delogo_status}\n"
            f"⚙️ **اختر حالة الشعار من الزر الشفاف بالأسفل ثم اضغط على الصيغة:**"
        )

    @staticmethod
    def _selection_buttons(count: int, delogo_enabled: bool) -> list:
        rows = [
            [Button.inline(f"{spec.button} ({count})", f"run_{spec.key}".encode())]
            for spec in FORMATS.values()
        ]
        btn_on_text = "✅ طمس الشعار (مفعّل)" if delogo_enabled else "⬜ تفعيل طمس الشعار"
        btn_off_text = "✅ تركه كما هو (مفعّل)" if not delogo_enabled else "⬜ ترك الشعار كما هو"
        rows.append([
            Button.inline(btn_on_text, b"delogo_on"),
            Button.inline(btn_off_text, b"delogo_off"),
        ])
        rows.append([
            Button.inline("🔄 عكس الترتيب", b"reverse_staged"),
            Button.inline("🗑️ إلغاء", b"cancel_batch"),
        ])
        return rows

    async def on_delogo_toggle(self, event) -> None:
        if not self.authorized(event.sender_id):
            return await event.answer("⛔ غير مصرح", alert=True)
        state = self.get_state(event.sender_id)
        if state.is_running:
            return await event.answer("المعالجة جارية بالفعل...", alert=True)
        choice = event.data.decode().removeprefix("delogo_")
        new_val = choice == "on"
        if state.delogo_enabled == new_val:
            return await event.answer(
                "هذا الخيار مفعّل بالفعل ✅", alert=False
            )
        state.delogo_enabled = new_val
        toast = "🎯 تم تفعيل طمس الشعار (Delogo 10x)" if new_val else "📺 سيتم ترك الفيديو كما هو بدون طمس"
        await event.answer(toast, alert=False)
        if state.staged:
            await event.edit(
                self._preview_text("✅ **تم استخراج", state.staged, state.delogo_enabled),
                buttons=self._selection_buttons(len(state.staged), state.delogo_enabled),
            )

    async def _lessons_from_document(self, event) -> list:
        name = (event.file.name or "").lower() if event.file else ""
        if not name.endswith((".har", ".txt", ".json")):
            await event.respond("⚠️ الصيغة غير مدعومة. أرسل ملف `.har` أو `.json` أو `.txt`.")
            return []
        load_msg = await event.respond("📥 **جاري قراءة الملف واستخراج المحاضرات...**")
        temp = str(self.cfg.download_dir / f"input_{uuid.uuid4().hex[:8]}_{Path(name).name}")
        try:
            await self.bot.download_media(event.document, file=temp)
            loop = asyncio.get_running_loop()
            lessons = await loop.run_in_executor(self.pool, parse_lessons_file, temp)
        except Exception as exc:
            log_error("تنزيل أو قراءة ملف الدروس", exc)
            await safe_edit(load_msg, f"❌ **خطأ أثناء قراءة الملف:** `{md(str(exc))[:200]}`")
            return []
        finally:
            silent_unlink(temp)
        if not lessons:
            await safe_edit(load_msg, "❌ **لم يتم العثور على روابط محاضرات صالحة في هذا الملف!**")
            return []
        await safe_delete(load_msg)
        return lessons

    @staticmethod
    def _lessons_from_text(text: str, state: UserState) -> list:
        urls: list = []
        for line in text.splitlines():
            m = URL_REGEX.search(line)
            if m:
                u = clean_extracted_url(m.group(1))
                if u and u not in urls:
                    urls.append(u)
        lessons = []
        for u in urls:
            lessons.append(Lesson(state.counter, f"محاضرة {state.counter}", u))
            state.counter += 1
        return lessons

    def _cancel_run(self, state: UserState) -> None:
        run = state.run
        if not run:
            return
        run.cancelled = True
        while not run.queue.empty():
            with contextlib.suppress(Exception):
                run.queue.get_nowait()
        for t in list(run.active_tasks):
            t.cancel()
        if run.worker and not run.worker.done():
            run.worker.cancel()
        run.finished = True
        state.run = None

    async def on_cancel_button(self, event) -> None:
        if not self.authorized(event.sender_id):
            return await event.answer("⛔ غير مصرح", alert=True)
        state = self.get_state(event.sender_id)
        self._cancel_run(state)
        state.staged.clear()
        await event.edit("🗑️ **تم إلغاء المهمة وتفريغ الطابور.**")

    async def on_reverse_button(self, event) -> None:
        if not self.authorized(event.sender_id):
            return await event.answer("⛔ غير مصرح", alert=True)
        state = self.get_state(event.sender_id)
        if state.is_running:
            return await event.answer("المعالجة جارية بالفعل...", alert=True)
        if not state.staged:
            return await event.answer("⚠️ القائمة فارغة!", alert=True)
        state.staged = [
            Lesson(i, f"محاضرة {i}", l.url)
            for i, l in enumerate(reversed(state.staged), start=1)
        ]
        await event.edit(
            self._preview_text("🔄 **تم عكس الترتيب —", state.staged, state.delogo_enabled),
            buttons=self._selection_buttons(len(state.staged), state.delogo_enabled),
        )

    async def on_run_button(self, event) -> None:
        user_id = event.sender_id
        if not self.authorized(user_id):
            return await event.answer("⛔ غير مصرح", alert=True)
        state = self.get_state(user_id)
        if state.is_running:
            return await event.answer("المعالجة جارية بالفعل...", alert=True)
        if not state.staged:
            return await event.answer("⚠️ القائمة فارغة أو تم تشغيلها مسبقاً!", alert=True)

        fmt = event.data.decode().removeprefix("run_")
        spec = FORMATS[fmt]
        if spec.needs_github and not self.cfg.github_ready:
            return await event.answer("⚠️ إعدادات GitHub غير مضبوطة", alert=True)
        if fmt == "bunny" and not self.cfg.bunny_ready:
            return await event.answer("⚠️ إعدادات Bunny غير مضبوطة", alert=True)

        items = list(state.staged)
        state.staged.clear()
        run = BatchRun(
            chosen_format=fmt,
            delogo=state.delogo_enabled,
            dl_sem=asyncio.Semaphore(self.cfg.download_slots),
            enc_sem=asyncio.Semaphore(self.cfg.encode_slots),
            up_sem=asyncio.Semaphore(self.cfg.upload_slots),
        )
        for it in items:
            run.queue.put_nowait(it)
        state.run = run
        dl_label = "🎯 مفعّل (Delogo 10x)" if run.delogo else "📺 بدون طمس (كما هو)"
        await event.edit(
            f"🚀 **بدء المعالجة المتوازية (Pipeline v7.9)!**\n"
            f"⚙️ النمط: `{spec.title}`\n"
            f"🛡️ طمس الشعار: `{dl_label}`\n"
            f"📦 عدد الفيديوهات: `{len(items)}`"
        )
        run.worker = asyncio.create_task(self._queue_worker(user_id, state, run))

    async def _queue_worker(self, user_id: int, state: UserState, run: BatchRun) -> None:
        max_in_flight = self.cfg.download_slots + self.cfg.encode_slots + self.cfg.upload_slots
        pipeline_sem = asyncio.Semaphore(max_in_flight)
        try:
            while not run.cancelled:
                await pipeline_sem.acquire()
                try:
                    lesson = await asyncio.wait_for(run.queue.get(), timeout=1.5)
                except asyncio.TimeoutError:
                    pipeline_sem.release()
                    if not run.active_tasks and run.queue.empty():
                        break
                    continue
                except BaseException:
                    pipeline_sem.release()
                    raise
                task = asyncio.create_task(self._process_lecture(user_id, run, lesson))
                run.active_tasks.add(task)
                task.add_done_callback(run.active_tasks.discard)
                task.add_done_callback(lambda _t: pipeline_sem.release())
        except asyncio.CancelledError:
            run.cancelled = True
        except Exception as exc:
            log_error(f"عامل الطابور للمستخدم {user_id}", exc)

        if run.active_tasks:
            await asyncio.gather(*list(run.active_tasks), return_exceptions=True)
        run.finished = True
        if state.run is run:
            state.run = None
        if not run.cancelled:
            await self._send_report(user_id, run)

    async def _process_lecture(self, user_id: int, run: BatchRun, lesson: Lesson) -> None:
        if run.cancelled:
            return
        spec = FORMATS[run.chosen_format]
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        url = clean_extracted_url(lesson.url)
        status = await self.bot.send_message(
            user_id, f"🎬 **معالجة:** `{md(lesson.title)}`\n⏳ بانتظار مقعد التحميل..."
        )
        file_id = f"lec_{lesson.index:03d}_{uuid.uuid4().hex[:8]}"
        task_dir = self.cfg.download_dir / file_id
        task_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with run.dl_sem:
                if run.cancelled:
                    raise BatchCancelled(CANCEL_MARK)
                dl_tracker = ProgressTracker(status, f"تحميل: {lesson.title[:20]}", "تحميل")
                raw = await loop.run_in_executor(
                    self.pool,
                    self.downloader.download,
                    url,
                    task_dir,
                    file_id,
                    loop,
                    dl_tracker,
                    lambda: run.cancelled,
                )
            if not raw or not raw.exists():
                raise RuntimeError("تعذر العثور على الملف بعد التنزيل.")
            if run.cancelled:
                raise BatchCancelled(CANCEL_MARK)

            info = MediaInfo()
            if spec.needs_probe or run.delogo:
                await safe_edit(status, f"🔍 **فحص سريع:** `{md(lesson.title)}`")
                info = await self.engine.probe(str(raw))
                if not info.video_codec:
                    raise RuntimeError("الملف المنزّل لا يحتوي على مسار فيديو صالح.")

            ctx = JobContext(self, run, lesson, file_id, task_dir, raw, info, status)
            out = await spec.runner(ctx)

            elapsed = fmt_duration(time.monotonic() - t0)
            await safe_delete(status)
            await self.bot.send_message(user_id, f"{out.message}\n\n⏱️ **زمن المعالجة:** `{elapsed}`")
            async with run.lock:
                run.completed.append(
                    JobResult(
                        lesson.index,
                        lesson.title,
                        out.size_mb,
                        out.cdn_url,
                        out.raw_url,
                        out.format_label,
                        url,
                        out.release,
                        out.extra_links,
                    )
                )
        except (asyncio.CancelledError, BatchCancelled):
            await safe_edit(status, f"🗑️ **أُلغيت المحاضرة:** `{md(lesson.title)}`")
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            log_error(f"معالجة المحاضرة [{lesson.index}] - {lesson.title}", exc)
            await safe_edit(status, f"⚠️ **تعذر إكمال [{md(lesson.title)}]:**\n`{md(reason)[:300]}`")
            async with run.lock:
                run.failed.append(JobFailure(lesson.index, lesson.title, reason[:200], url))
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
            gc.collect()

    async def _send_report(self, user_id: int, run: BatchRun) -> None:
        completed, failed = run.completed, run.failed
        total = len(completed) + len(failed)
        if total == 0:
            return
        sep = "-" * 40
        lines = [
            f"=== تقرير معالجة الدروس ({FORMATS[run.chosen_format].title}) ===",
            f"طمس الشعار: {'مفعّل (Delogo 10x)' if run.delogo else 'معطّل (الأصلي)'}",
            "",
            f"✅ الناجحة: {len(completed)} / {total}",
            f"❌ الفاشلة: {len(failed)} / {total}",
            "=" * 40,
            "",
        ]
        if completed:
            lines.append("--- [ الدروس المرفوعة بنجاح ] ---")
            for r in sorted(completed, key=lambda x: x.index):
                lines += [
                    f"الرقم: {r.index}",
                    f"العنوان: {r.title}",
                    f"الصيغة: {r.format_label}",
                    f"الحجم: {r.size_mb:.2f} MB",
                    f"الرابط الرئيسي: {r.cdn_url}",
                ]
                if r.raw_url:
                    lines.append(f"الرابط الثانوي: {r.raw_url}")
                if r.extra_links:
                    lines.append(f"روابط إضافية: {r.extra_links}")
                lines += [f"رابط المصدر: {r.source_url}", sep]
        if failed:
            lines += ["", "--- [ الدروس المتخطاة ] ---"]
            for f_ in sorted(failed, key=lambda x: x.index):
                lines += [
                    f"الرقم: {f_.index}",
                    f"العنوان: {f_.title}",
                    f"السبب: {f_.reason}",
                    f"رابط المصدر: {f_.source_url}",
                    sep,
                ]

        report = self.cfg.download_dir / f"summary_{user_id}_{int(time.time())}.txt"
        try:
            async with aiofiles.open(report, "w", encoding="utf-8") as f:
                await f.write("\n".join(lines) + "\n")
            await self.bot.send_file(
                user_id,
                str(report),
                caption=(
                    f"🏁 **اكتملت جميع المهام**\n"
                    f"✅ ناجح: `{len(completed)}`\n"
                    f"⚠️ متعثر: `{len(failed)}`"
                ),
            )
        except Exception as exc:
            log_error("إرسال ملف التقرير النهائي", exc)
        finally:
            silent_unlink(str(report))


async def amain() -> None:
    cfg = Config.from_env()
    bot_app = BotApp(cfg)
    await bot_app.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    disconnected = asyncio.ensure_future(bot_app.bot.run_until_disconnected())
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({disconnected, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await bot_app.shutdown()
        for t in (disconnected, stopper):
            t.cancel()


# ───────────────────────── تعديل التشغيل السحابي لـ Modal ─────────────────────────

# دالة تشغيل السيرفر السحابي الفعلي داخل Modal (مع GPU T4 لتسريع NVENC 10x)
@app.function(cpu=8.0, memory=8192, timeout=86400)
def run_bot_on_modal_cloud():
    asyncio.run(amain())


# 1. نربط التطبيق برمز الـ App الخاص بـ Modal المتواجد في كودك
@app.local_entrypoint()
def cloud_main():
    """هذه الدالة تخبر سيرفر Modal بتشغيل البوت بالكامل داخل بيئته السحابية الخارقة"""
    logger.info("⚡ جاري تشغيل المحرك والبوت بالكامل على سيرفرات Modal السحابية...")

    # إذا تم استدعاؤه عبر `modal run bot.py` يشغله في حاوية Modal السحابية، وإلا يشغله مباشرة
    if modal is not None:
        run_bot_on_modal_cloud.remote()
    else:
        asyncio.run(amain())


# احذف أو استبدل دالة الـ main القديمة بهذا لعدم حدوث تضارب
if __name__ == "__main__":
    # عند التشغيل عبر السيرفر سيتكفل Modal باستدعاء cloud_main تلقائياً
    pass