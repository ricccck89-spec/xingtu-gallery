# -*- coding: utf-8 -*-
"""路径常量、配置与路径安全校验工具。"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = APP_DIR.parent
VOLUME_DIR = BASE_DIR / "Volume"
SETTINGS_FILE = VOLUME_DIR / "settings.json"
CONFIG_FILE = APP_DIR / "config.json"
WEB_DIR = APP_DIR / "web"
LOGS_DIR = APP_DIR / "logs"
PID_FILE = LOGS_DIR / "server.pid"
BUILD_FILE = LOGS_DIR / "server.build"
ACCOUNT_CACHE = APP_DIR / "cache" / "accounts"
CREATORS_FILE = APP_DIR / "cache" / "creators.json"
THUMB_DIR = APP_DIR / "cache" / "thumbs"
TOOLS_DIR = APP_DIR / "tools"

HOST = "127.0.0.1"
PORT = int(os.environ.get("DOUK_LITE_PORT", "8765"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "root": str(VOLUME_DIR),
    "live_folder": "实况",
    "video_folder": "视频",
    "quality": "high",
    "workers": 3,
    "folder_template": "UID{uid}_{nickname}_发布作品",
    "best_image": True,
    "xhs_cookie": "",
}

STALE_MSG = "接口不存在：界面服务还是旧版本，先双击「星图画廊」重启一次（不行再运行 scripts\\停止.cmd）"

FRAME_DIR = "截帧"
LIVE_OUT = "LivePhotos"
LIVE_INDEX = "_index.json"
NAME_EXTS = (".HEIC", ".JPG", ".MOV")
SKIP_DIRS = {
    "LivePhotos", "Cache", "cache", "Data",
    "$RECYCLE.BIN", "System Volume Information", FRAME_DIR,
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
VIDEO_SUFFIX = {".mp4", ".mov", ".m4v"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


_json_cache = {}
_json_cache_lock = threading.Lock()
_logger = None
_logger_lock = threading.Lock()


def get_logger():
    """返回写往 logs/app.log 的 UTF-8 轮转日志器。"""
    global _logger
    with _logger_lock:
        if _logger is not None:
            return _logger
        log = logging.getLogger("douk_lite")
        log.setLevel(logging.INFO)
        log.propagate = False
        if not log.handlers:
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    LOGS_DIR / "app.log",
                    maxBytes=2 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                ))
                log.addHandler(handler)
            except OSError:
                log.addHandler(logging.NullHandler())
        _logger = log
        return _logger


def _read_json_cached(path, encoding="utf-8"):
    """按路径与 (mtime_ns, size) 缓存 JSON 读取，减少高频请求的重复读盘。"""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        with _json_cache_lock:
            _json_cache.pop(str(p), None)
        return None
    sig = (st.st_mtime_ns, st.st_size)
    key = str(p)
    with _json_cache_lock:
        hit = _json_cache.get(key)
        if hit and hit[0] == sig:
            return dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
    try:
        parsed = json.loads(p.read_text(encoding=encoding))
    except Exception:
        return None
    with _json_cache_lock:
        _json_cache[key] = (sig, parsed)
    return dict(parsed) if isinstance(parsed, dict) else parsed


def read_build():
    """读取 web/build.txt 作为当前界面与服务端版本号。"""
    try:
        return (WEB_DIR / "build.txt").read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        return "dev"


BUILD = read_build()


def load_config():
    data = dict(DEFAULT_CONFIG)
    loaded = _read_json_cached(CONFIG_FILE, encoding="utf-8")
    if isinstance(loaded, dict):
        data.update(loaded)
    return data


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    with _json_cache_lock:
        _json_cache.pop(str(CONFIG_FILE), None)


def read_app_settings():
    loaded = _read_json_cached(SETTINGS_FILE, encoding="utf-8-sig")
    return loaded if isinstance(loaded, dict) else {}


_UNSET = object()


def cookie_header(raw=_UNSET):
    """把上游 settings.json 里的 cookie（dict 或 str）统一转成 HTTP Cookie 头字符串。"""
    if raw is _UNSET:
        raw = read_app_settings().get("cookie")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return "; ".join(
            f"{str(k).strip()}={v}"
            for k, v in raw.items()
            if k and v is not None and str(v) != ""
        )
    return ""


def resolve_root(raw_root=None):
    """优先使用前端传入且通过安全校验的保存目录，否则回退到当前配置根目录。"""
    if raw_root:
        valid, _err = validate_root(raw_root)
        if valid:
            return Path(valid)
    return Path(load_config().get("root") or VOLUME_DIR)


def is_under(path, root):
    try:
        Path(path).relative_to(Path(root))
        return True
    except (ValueError, OSError):
        return False


def validate_root(raw_root):
    p_str = (raw_root or "").strip()
    if not p_str:
        return None, "保存路径不能为空"
    try:
        rp = Path(p_str).resolve()
    except OSError as exc:
        return None, f"无效路径：{exc}"
    app_res = APP_DIR.resolve()
    if rp == app_res or is_under(rp, app_res):
        return None, "不能把保存路径设到 DouK-Lite 程序目录里"
    return p_str, ""


def safe_name(text, limit=120):
    text = BAD_CHARS.sub("_", text or "").strip().strip(".")
    text = re.sub(r"\s+", " ", text)
    return text[:limit] or "未命名"


def render_folder_name(item, template=""):
    """按模板拼博主文件夹名。可用占位符：{uid} {sec_uid} {nickname} {date}"""
    tpl = (template or DEFAULT_CONFIG["folder_template"]).strip()
    uid = str(item.get("uid") or "")
    nickname = item.get("nickname") or "未知作者"
    values = {
        "{uid}": uid,
        "{sec_uid}": str(item.get("sec_uid") or ""),
        "{nickname}": nickname,
        "{date}": str(item.get("create_time") or "")[:10],
    }
    for k, v in values.items():
        tpl = tpl.replace(k, v)
    if not uid:
        # 没有 uid 时别在名字里留个空的 UID 前缀
        tpl = re.sub(r"(?i)uid(?=[_\-\s]|$)", "", tpl)
    tpl = tpl.strip(" _-")
    return safe_name(tpl, 100) if tpl else safe_name(nickname, 60)


def item_folder(root, item, template=""):
    """博主文件夹本身。实况/视频的子目录按实际文件类型再分，见 target_folder()。"""
    return Path(root) / render_folder_name(item, template)


def sub_folder(base_folder, name, default):
    name = safe_name(name or default, 40)
    return Path(base_folder) / name


def target_folder(base_folder, item, ext, live_folder, video_folder):
    """照片直接放博主文件夹；实况帖的视频进「实况」，普通视频进「视频」。"""
    if ext.lower() in VIDEO_SUFFIX:
        if item.get("type") == "实况":
            return sub_folder(base_folder, live_folder, "实况")
        return sub_folder(base_folder, video_folder, "视频")
    return Path(base_folder)


def live_out_dir(root):
    return Path(root) / LIVE_OUT


def tool_path(name, exe):
    candidates = [TOOLS_DIR / exe]
    if os.name != "nt":
        candidates.extend([
            TOOLS_DIR / name,
            Path("/opt/homebrew/bin") / name,
            Path("/usr/local/bin") / name,
            Path.home() / ".local" / "bin" / name,
        ])
    for p in candidates:
        if p.exists():
            return str(p)
    return shutil.which(name) or ""


def tool_status():
    heic = True
    try:
        import pillow_heif  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        heic = False
    return {
        "ffmpeg": tool_path("ffmpeg", "ffmpeg.exe"),
        "exiftool": tool_path("exiftool", "exiftool.exe"),
        "heic": heic,
    }


def run_tool(cmd, timeout=300):
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **kwargs,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(msg[-400:] or f"命令失败：{Path(cmd[0]).name}")
    return r.stdout
