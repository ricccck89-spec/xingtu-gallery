# -*- coding: utf-8 -*-
"""ComfyUI 批量图像处理后台任务。"""

from __future__ import annotations

import copy
import json
import mimetypes
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from douk_lite import config, media, state

DEFAULT_WORKFLOW = r"E:\Download\▶▷Qwen2.1-Turbo-9步-编辑&扩图&移除&重绘&生图（整合）.json"
DEFAULT_SERVER = "http://127.0.0.1:8188"
DEFAULT_PREFIX = "QW21_batch"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_COMFY_SUFFIX_RE = re.compile(r"_comfy(?:_.*)?$", re.IGNORECASE)
_ILLEGAL_NAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]+')
PRESETS_FILE = Path(__file__).resolve().parent.parent / "cache" / "comfy_presets.json"

LOAD_IMAGE_NODE_ID = "425"
PROMPT_NODE_ID = "427"
SEED_NODE_ID = "454"
SAVE_IMAGE_NODE_ID = "422"

_lock = threading.Lock()
_stop_event = threading.Event()
_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_file": "",
    "workflow": DEFAULT_WORKFLOW,
    "server": DEFAULT_SERVER,
    "prefix": DEFAULT_PREFIX,
    "prompt": "",
    "error": "",
    "logs": [],
}


def _append_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _lock:
        _state["logs"].append(line)
        if len(_state["logs"]) > 80:
            _state["logs"] = _state["logs"][-80:]


def read_default_prompt(workflow_path: str = DEFAULT_WORKFLOW) -> str:
    p = Path(workflow_path)
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return str(data.get(PROMPT_NODE_ID, {}).get("inputs", {}).get("value", ""))
    except Exception:
        return ""


def _write_presets(presets: list[dict]) -> None:
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")


def list_presets() -> list[dict]:
    with _lock:
        if not PRESETS_FILE.exists():
            default_p = read_default_prompt()
            initial = [{"name": "默认提示词", "prompt": default_p}] if default_p else []
            _write_presets(initial)
            return initial
        try:
            data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            return [x for x in data if isinstance(x, dict) and x.get("name")]
        except Exception:
            return []


def save_preset(name: str, prompt: str) -> dict:
    clean_name = (name or "").strip()
    clean_prompt = (prompt or "").strip()
    if not clean_name:
        return {"ok": False, "message": "预设名称不能为空"}
    if not clean_prompt:
        return {"ok": False, "message": "提示词内容不能为空"}
    with _lock:
        presets = []
        if PRESETS_FILE.exists():
            try:
                presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            except Exception:
                presets = []
        updated = False
        for item in presets:
            if item.get("name") == clean_name:
                item["prompt"] = clean_prompt
                updated = True
                break
        if not updated:
            presets.append({"name": clean_name, "prompt": clean_prompt})
        _write_presets(presets)
    return {"ok": True, "presets": presets}


def delete_preset(name: str) -> dict:
    clean_name = (name or "").strip()
    if not clean_name:
        return {"ok": False, "message": "未指定要删除的预设"}
    with _lock:
        presets = []
        if PRESETS_FILE.exists():
            try:
                presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
            except Exception:
                presets = []
        presets = [x for x in presets if x.get("name") != clean_name]
        _write_presets(presets)
    return {"ok": True, "presets": presets}


def _normalize_server(server: str) -> str:
    s = (server or DEFAULT_SERVER).strip().rstrip("/")
    parsed = urllib.parse.urlparse(s)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("ComfyUI 地址必须以 http:// 或 https:// 开头")
    return s


def _base_stem(stem: str) -> str:
    stripped = _COMFY_SUFFIX_RE.sub("", stem or "")
    return stripped or (stem or "")


def _safe_preset_tag(preset_name: str) -> str:
    cleaned = _ILLEGAL_NAME_CHARS_RE.sub("_", (preset_name or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._- ")
    return cleaned[:40]


def _detect_image_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return ""


def get_queue_status(server: str = DEFAULT_SERVER) -> tuple[int, int] | None:
    try:
        srv = _normalize_server(server)
        with urllib.request.urlopen(f"{srv}/queue", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return len(data.get("queue_running", [])), len(data.get("queue_pending", []))
    except Exception:
        return None


def _upload_image(server: str, image_path: Path, subfolder: str = "batch_input") -> str:
    boundary = f"----ComfyBatch{uuid.uuid4().hex}"
    safe_name = image_path.name.replace('"', "_").replace("\r", "").replace("\n", "")
    mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    file_bytes = image_path.read_bytes()
    parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="subfolder"\r\n\r\n{subfolder}\r\n'.encode("utf-8"),
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'.encode("utf-8"),
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    req = urllib.request.Request(
        f"{server.rstrip('/')}/upload/image",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    name = res["name"]
    sub = res.get("subfolder", "")
    return f"{sub}/{name}" if sub else name


def _queue_prompt(server: str, workflow: dict, client_id: str) -> str:
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{server.rstrip('/')}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return res["prompt_id"]
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"提交任务失败 HTTP {exc.code}: {err_body}") from exc


def _wait_for_completion(
    server: str,
    prompt_id: str,
    poll_interval: float = 1.5,
    timeout_sec: float = 1800.0,
) -> dict:
    url = f"{server.rstrip('/')}/history/{urllib.parse.quote(prompt_id)}"
    started = time.monotonic()
    consecutive_errors = 0
    empty_queue_misses = 0
    while True:
        if time.monotonic() - started > timeout_sec:
            raise RuntimeError(f"等待 ComfyUI 完成超时（超过 {int(timeout_sec)} 秒）")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                history = json.loads(resp.read().decode("utf-8"))
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise RuntimeError(f"查询 ComfyUI 进度失败：{exc}") from exc
            time.sleep(poll_interval)
            continue
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(status, ensure_ascii=False)}")
            if status.get("completed", True):
                return entry
            empty_queue_misses = 0
        else:
            q = get_queue_status(server)
            if q == (0, 0):
                empty_queue_misses += 1
                if empty_queue_misses >= 4:
                    raise RuntimeError("ComfyUI 队列已空但未找到该任务结果，可能已被手动取消或服务重启")
            else:
                empty_queue_misses = 0
        time.sleep(poll_interval)


def _extract_output_images(history_entry: dict) -> list[dict]:
    outputs = history_entry.get("outputs") or {}
    preferred = (outputs.get(SAVE_IMAGE_NODE_ID) or {}).get("images") or []
    if preferred:
        return [img for img in preferred if isinstance(img, dict) and img.get("filename")]
    fallback: list[dict] = []
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for img in node_out.get("images") or []:
            if isinstance(img, dict) and img.get("filename") and img.get("type", "output") == "output":
                fallback.append(img)
    return fallback


def _download_output_image(server: str, img_meta: dict) -> bytes:
    params = urllib.parse.urlencode({
        "filename": img_meta["filename"],
        "subfolder": img_meta.get("subfolder", ""),
        "type": img_meta.get("type", "output"),
    })
    url = f"{server.rstrip('/')}/view?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def _comfy_out_dir(src_path: Path) -> Path:
    root = config.resolve_root()
    try:
        rel = src_path.resolve().relative_to(root.resolve())
        parts = rel.parts
    except Exception:
        parts = ()
    if len(parts) >= 2 and parts[0] in (config.FRAME_DIR, config.COMFY_DIR):
        creator_folder = parts[1]
    elif parts:
        creator_folder = parts[0]
    else:
        creator_folder = "_外部"
    return root / config.COMFY_DIR / creator_folder


def import_to_post_comfy(target_path: Path, filename: str, data: bytes) -> dict:
    magic_ext = _detect_image_ext(data)
    if not magic_ext:
        return {"ok": False, "message": "仅支持导入有效图片文件（PNG/JPG/WEBP/BMP）"}
    dest_dir = _comfy_out_dir(target_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    orig_name = (filename or "image.png").strip()
    orig_path = Path(orig_name)
    ext = orig_path.suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = magic_ext

    target_stem = _base_stem(target_path.stem)
    orig_stem = orig_path.stem
    expected_prefix = f"{target_stem}_comfy"
    is_valid_name = orig_stem == expected_prefix or (
        orig_stem.startswith(f"{expected_prefix}_")
        and orig_stem[len(expected_prefix) + 1:].isdigit()
    )

    candidate = dest_dir / f"{orig_stem}{ext}" if is_valid_name else dest_dir / f"{expected_prefix}{ext}"
    renamed = not is_valid_name
    if candidate.exists():
        seq = 2
        while True:
            cand = dest_dir / f"{expected_prefix}_{seq}{ext}"
            if not cand.exists():
                candidate = cand
                break
            seq += 1
        renamed = True

    tmp = dest_dir / f".{candidate.name}.{uuid.uuid4().hex[:6]}.part"
    tmp.write_bytes(data)
    tmp.replace(candidate)
    state.invalidate_library()
    mtime_ns = candidate.stat().st_mtime_ns
    return {
        "ok": True,
        "renamed": renamed,
        "original_name": orig_name,
        "file": {
            "name": candidate.name,
            "path": str(candidate),
            "kind": "image",
            "size": len(data),
            "mtime": mtime_ns,
            "is_comfy": True,
        },
    }


def _save_output_to_post(
    src_path: Path,
    img_meta: dict,
    data: bytes,
    preset_name: str = "",
) -> Path:
    dest_dir = _comfy_out_dir(src_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(img_meta.get("filename") or ".png").suffix.lower()
    if ext not in IMAGE_EXTS:
        ext = ".png"
    base_stem = _base_stem(src_path.stem)
    tag = _safe_preset_tag(preset_name)
    stem_prefix = f"{base_stem}_comfy_{tag}" if tag else f"{base_stem}_comfy"
    candidate = dest_dir / f"{stem_prefix}{ext}"
    seq = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem_prefix}_{seq}{ext}"
        seq += 1
    tmp = dest_dir / f".{candidate.name}.{uuid.uuid4().hex[:6]}.part"
    tmp.write_bytes(data)
    tmp.replace(candidate)
    return candidate


def get_status(server: str = "") -> dict:
    with _lock:
        snap = {
            "ok": True,
            "running": _state["running"],
            "total": _state["total"],
            "done": _state["done"],
            "current_file": _state["current_file"],
            "workflow": _state["workflow"],
            "server": _state["server"],
            "prefix": _state["prefix"],
            "prompt": _state["prompt"],
            "error": _state["error"],
            "logs": list(_state["logs"]),
        }
    srv = (server or snap["server"] or DEFAULT_SERVER).strip()
    q = get_queue_status(srv)
    snap["comfy_online"] = q is not None
    snap["queue_running"] = q[0] if q else 0
    snap["queue_pending"] = q[1] if q else 0
    snap["default_prompt"] = read_default_prompt(snap["workflow"])
    return snap


def start_batch(
    paths: list[str],
    workflow: str = "",
    server: str = "",
    prefix: str = "",
    prompt: str = "",
    preset_name: str = "",
) -> dict:
    wf_path = Path((workflow or DEFAULT_WORKFLOW).strip())
    try:
        srv = _normalize_server((server or DEFAULT_SERVER).strip())
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    out_prefix = (prefix or DEFAULT_PREFIX).strip()
    prompt_str = (prompt or "").strip()

    if not wf_path.exists():
        return {"ok": False, "message": f"找不到工作流文件：{wf_path}"}

    valid_paths: list[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = media.thumb_allowed(str(raw))
        if p and p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                valid_paths.append(p)
    if not valid_paths:
        return {"ok": False, "message": "没有有效的图片文件可处理"}

    q = get_queue_status(srv)
    if q is None:
        return {"ok": False, "message": f"无法连接 ComfyUI（{srv}），请确认已启动"}
    if q[0] > 0 or q[1] > 0:
        return {"ok": False, "message": f"ComfyUI 正在执行任务（运行中 {q[0]} / 排队中 {q[1]}），请稍后再试"}

    with _lock:
        if _state["running"]:
            return {"ok": False, "message": "已有批处理任务正在运行中"}
        _stop_event.clear()
        _state.update({
            "running": True,
            "total": len(valid_paths),
            "done": 0,
            "current_file": "",
            "workflow": str(wf_path),
            "server": srv,
            "prefix": out_prefix,
            "prompt": prompt_str,
            "error": "",
            "logs": [],
        })
        state.set_comfy_running(True)

    try:
        threading.Thread(
            target=_worker,
            args=(wf_path, valid_paths, srv, out_prefix, prompt_str, (preset_name or "").strip()),
            daemon=True,
        ).start()
    except Exception:
        with _lock:
            _state["running"] = False
        state.set_comfy_running(False)
        raise
    return {"ok": True, "total": len(valid_paths)}


def stop_batch() -> dict:
    with _lock:
        running = _state["running"]
    if not running:
        return {"ok": False, "message": "当前没有运行中的批处理任务"}
    _stop_event.set()
    _append_log("已请求停止，将在当前图片处理完成后停止...")
    return {"ok": True}


def _worker(
    wf_path: Path,
    files: list[Path],
    server: str,
    prefix: str,
    prompt_str: str,
    preset_name: str = "",
) -> None:
    try:
        base_wf = json.loads(wf_path.read_text(encoding="utf-8"))
        if LOAD_IMAGE_NODE_ID not in base_wf:
            raise KeyError(f"工作流中未找到 LoadImage 节点 {LOAD_IMAGE_NODE_ID}")

        client_id = f"xingtu_{uuid.uuid4().hex[:8]}"
        total = len(files)
        _append_log(f"开始批量处理 {total} 张图片 -> {server}")

        for idx, img_path in enumerate(files, start=1):
            if _stop_event.is_set():
                _append_log("已手动停止剩余任务。")
                break

            with _lock:
                _state["current_file"] = img_path.name

            t0 = time.time()
            _append_log(f"[{idx}/{total}] 上传并提交: {img_path.name}")
            uploaded_ref = _upload_image(server, img_path)

            wf = copy.deepcopy(base_wf)
            wf[LOAD_IMAGE_NODE_ID]["inputs"]["image"] = uploaded_ref
            if PROMPT_NODE_ID in wf and prompt_str:
                wf[PROMPT_NODE_ID]["inputs"]["value"] = prompt_str
            if SEED_NODE_ID in wf:
                wf[SEED_NODE_ID]["inputs"]["seed"] = random.randint(1, 2**31 - 1)
            if SAVE_IMAGE_NODE_ID in wf and prefix:
                wf[SAVE_IMAGE_NODE_ID]["inputs"]["filename_prefix"] = f"{prefix}/{img_path.stem}"

            prompt_id = _queue_prompt(server, wf, client_id)
            history_entry = _wait_for_completion(server, prompt_id)
            out_images = _extract_output_images(history_entry)
            if not out_images:
                raise RuntimeError(f"ComfyUI 未返回输出图片（prompt_id={prompt_id}）")

            saved_names: list[str] = []
            for img_meta in out_images:
                img_bytes = _download_output_image(server, img_meta)
                saved_path = _save_output_to_post(img_path, img_meta, img_bytes, preset_name=preset_name)
                saved_names.append(saved_path.name)
            state.invalidate_library()

            elapsed = time.time() - t0
            with _lock:
                _state["done"] = idx
            _append_log(f"[{idx}/{total}] 完成并归入子画廊: {', '.join(saved_names)} (耗时 {elapsed:.1f}s)")

        if not _stop_event.is_set():
            _append_log(f"全部完成！结果已归入画廊作品子画廊（{config.COMFY_DIR}/）。")
    except Exception as exc:
        with _lock:
            _state["error"] = str(exc)
        _append_log(f"[错误] {exc}")
    finally:
        with _lock:
            _state["running"] = False
            _state["current_file"] = ""
        state.set_comfy_running(False)
