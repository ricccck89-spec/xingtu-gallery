# -*- coding: utf-8 -*-
"""缩略图生成、视频截帧、实况照片转换与回收站清理。"""

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from douk_lite import config, state

NAME_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[.:\-](\d{2})[.:\-](\d{2})")


def source_time(path):
    m = NAME_TIME_RE.search(path.name)
    if m:
        try:
            return datetime(*[int(x) for x in m.groups()])
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.now()


def thumb_allowed(path):
    """只允许给保存目录里的文件做缩略图或预览，别让它变成任意文件读取。"""
    try:
        p = Path(path).resolve()
    except OSError:
        return None
    if not p.is_file():
        return None
    if p.suffix.lower() not in config.IMAGE_EXT | config.VIDEO_EXT:
        return None
    cfg = config.load_config()
    roots = {Path(cfg.get("root") or config.VOLUME_DIR)}
    if config.VOLUME_DIR.exists():
        roots.add(config.VOLUME_DIR)
    roots.add(config.live_out_dir(cfg.get("root") or config.VOLUME_DIR))
    for root in roots:
        try:
            if config.is_under(p, root.resolve()):
                return p
        except OSError:
            continue
    return None


def _get_thumb_lock(key):
    with state._thumb_key_guard:
        lock = state._thumb_key_locks.get(key)
        if lock is None:
            if len(state._thumb_key_locks) > 512:
                state._thumb_key_locks.clear()
            lock = state._thumb_key_locks[key] = threading.Lock()
        return lock


def _prune_thumb_cache(max_bytes=300 * 1024 * 1024, max_files=5000):
    now = time.time()
    if now - state._last_thumb_prune < 600:
        return
    state._last_thumb_prune = now
    try:
        entries = []
        total = 0
        for p in config.THUMB_DIR.iterdir():
            if not p.is_file() or p.suffix != ".jpg":
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            entries.append((st.st_mtime, st.st_size, p))
        if len(entries) <= max_files and total <= max_bytes:
            return
        entries.sort(key=lambda x: x[0])
        remaining = len(entries)
        for _mt, sz, p in entries:
            if remaining <= max_files and total <= max_bytes:
                break
            try:
                p.unlink()
                total -= sz
                remaining -= 1
            except OSError:
                pass
    except OSError:
        pass


def make_thumb(src, width=160):
    """生成（或取缓存的）缩略图，返回 (路径, 是否命中缓存)。"""
    width = max(48, min(int(width or 160), 720))
    st = src.stat()
    key = hashlib.sha1(
        f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{width}".encode("utf-8")
    ).hexdigest()
    dest = config.THUMB_DIR / f"{key}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return dest, True

    with _get_thumb_lock(key):
        if dest.exists() and dest.stat().st_size > 0:
            return dest, True
        config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.THUMB_DIR / f"{key}.{threading.get_ident()}.part.jpg"
        try:
            if src.suffix.lower() in config.IMAGE_EXT:
                ok = False
                try:
                    from PIL import Image, ImageOps
                    try:
                        import pillow_heif
                        pillow_heif.register_heif_opener()
                    except Exception:
                        pass
                    img = Image.open(src)
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail((width, width * 4))
                    if img.mode not in ("RGB", "L"):
                        img = img.convert("RGB")
                    img.save(tmp, format="JPEG", quality=82)
                    ok = True
                except Exception:
                    ok = False
                if not ok:
                    ffmpeg = config.tool_path("ffmpeg", "ffmpeg.exe")
                    if not ffmpeg:
                        raise RuntimeError("没有可用的图片解码器")
                    with state._thumb_sem:
                        config.run_tool(
                            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                             "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)],
                            timeout=60,
                        )
            else:
                ffmpeg = config.tool_path("ffmpeg", "ffmpeg.exe")
                if not ffmpeg:
                    raise RuntimeError("找不到 ffmpeg")
                with state._thumb_sem:
                    try:
                        config.run_tool(
                            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                             "-ss", "0.3", "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)],
                            timeout=90,
                        )
                    except Exception:
                        config.run_tool(
                            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                             "-i", str(src), "-frames:v", "1",
                             "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)],
                            timeout=90,
                        )
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError("缩略图生成失败")
            tmp.replace(dest)
            threading.Thread(target=_prune_thumb_cache, daemon=True).start()
            return dest, False
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def scan_frame_videos(root, refresh=False, limit=0):
    """扫保存目录里的视频（跳过 LivePhotos 和 截帧），结果缓存 20 秒。"""
    root = Path(root)
    key = str(root.resolve())
    now = time.time()
    with state._frame_lock:
        hit = state._frame_cache.get(key)
        items = hit[1] if hit and not refresh and now - hit[0] < 20 else None
    if items is None:
        items = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                if here == root:
                    dirnames[:] = [
                        d for d in dirnames
                        if d not in config.SKIP_DIRS and not d.startswith(".")
                    ]
                for name in filenames:
                    p = here / name
                    if p.suffix.lower() not in config.VIDEO_EXT:
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    try:
                        rel = p.relative_to(root)
                    except ValueError:
                        continue
                    parts = rel.parts
                    if not parts or parts[0] in config.SKIP_DIRS or parts[0].startswith("."):
                        continue
                    items.append({
                        "path": str(p),
                        "rel": str(rel),
                        "name": p.name,
                        "creator": parts[0] if len(parts) > 1 else "",
                        "live": ("-实况-" in p.name) or (p.parent.name == "实况"),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "time": source_time(p).strftime("%Y-%m-%d %H:%M"),
                        "thumb": str(p),
                    })
        except OSError as exc:
            config.get_logger().warning("扫描视频失败 %s: %s", root, exc)
        items.sort(key=lambda x: x["mtime"], reverse=True)
        with state._frame_lock:
            state._frame_cache[key] = (now, items)
    return items[:limit] if limit and limit > 0 else list(items)


def frame_output_dir(root, src):
    r"""截帧成品目录：只保留博主这一层，统一收在 截帧\ 下。"""
    root = Path(root)
    try:
        rel_parent = Path(src).parent.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return root / config.FRAME_DIR / "_外部"
    parts = [p for p in rel_parent.parts if p not in ("", ".")]
    return (root / config.FRAME_DIR / parts[0]) if parts else (root / config.FRAME_DIR)


def extract_frame(src, root, seconds=None):
    """用 ffmpeg 抽一帧。seconds 为空表示自动挑最清晰的一帧。"""
    src = Path(src)
    if not src.is_file():
        raise RuntimeError("视频文件不在了")
    ffmpeg = config.tool_path("ffmpeg", "ffmpeg.exe")
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")

    outdir = frame_output_dir(root, src)
    outdir.mkdir(parents=True, exist_ok=True)

    base = config.safe_name(src.stem, 80)
    if seconds is None:
        tag = "最佳帧"
    else:
        try:
            t = max(0.0, float(seconds))
        except (TypeError, ValueError):
            t = 0.0
        tag = f"{int(t // 3600):02d}-{int(t % 3600 // 60):02d}-{int(t % 60):02d}"
    name = f"{base}_帧_{tag}"
    ext = ".jpg"
    dest = outdir / f"{name}{ext}"
    n = 2
    while dest.exists():
        dest = outdir / f"{name}_{n}{ext}"
        n += 1

    scratch = Path(tempfile.mkdtemp(prefix="douk_frame_"))
    tmp = scratch / "frame.jpg"
    try:
        try:
            if seconds is None:
                config.run_tool(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(src), "-vf", "thumbnail=100", "-frames:v", "1",
                     "-q:v", "2", str(tmp)],
                    timeout=180,
                )
            else:
                config.run_tool(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-ss", f"{max(0.0, float(seconds)):.3f}", "-i", str(src),
                     "-frames:v", "1", "-q:v", "2", str(tmp)],
                    timeout=180,
                )
        except Exception:
            config.run_tool(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(src), "-vf", "thumbnail=100", "-frames:v", "1",
                 "-q:v", "2", str(tmp)],
                timeout=180,
            )
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("没有抽到画面")
        shutil.move(str(tmp), str(dest))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    try:
        rel = str(dest.relative_to(Path(root)))
    except ValueError:
        rel = dest.name
    return {"ok": True, "path": str(dest), "rel": rel, "name": dest.name}


def frame_time_tag(seconds):
    """秒数 → 文件名里的时间标签，精确到毫秒，逐帧截取时不会撞名。"""
    try:
        t = max(0.0, float(seconds))
    except (TypeError, ValueError):
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}-{m:02d}-{s:02d}-{ms:03d}"


def save_frame_png(src, root, data, seconds=None):
    """浏览器截帧页 canvas 导出的 PNG 直接落盘，路径规则和 ffmpeg 抽帧一致。"""
    src = Path(src)
    if not src.is_file():
        raise RuntimeError("视频文件不在了")
    if not data:
        raise RuntimeError("没有收到图片数据")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("收到的不是 PNG 图片")

    outdir = frame_output_dir(root, src)
    outdir.mkdir(parents=True, exist_ok=True)

    base = config.safe_name(src.stem, 80)
    name = f"{base}_帧_{frame_time_tag(seconds)}"
    dest = outdir / f"{name}.png"
    n = 2
    while dest.exists():
        dest = outdir / f"{name}_{n}.png"
        n += 1

    tmp = dest.with_name(dest.name + ".part")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    try:
        rel = str(dest.relative_to(Path(root)))
    except ValueError:
        rel = dest.name
    return {"ok": True, "path": str(dest), "rel": rel, "name": dest.name, "bytes": len(data)}


def build_makernote(identifier):
    """Apple MakerNote，只放一条 0x0011 ContentIdentifier。"""
    data = identifier.encode("ascii") + b"\x00"
    header = b"Apple iOS\x00" + b"\x00\x01" + b"MM"
    ifd = struct.pack(">H", 1)
    ifd += struct.pack(">HHI", 0x0011, 2, len(data))
    ifd += struct.pack(">I", 32)
    ifd += b"\x00\x00\x00\x00"
    return header + ifd + data


def load_live_index(out):
    try:
        data = json.loads((out / config.LIVE_INDEX).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_live_index(out, data):
    out.mkdir(parents=True, exist_ok=True)
    tmp = (out / config.LIVE_INDEX).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out / config.LIVE_INDEX)


def unique_base(out, base):
    base = base or "实况"

    def taken(name):
        return any((out / f"{name}{ext}").exists() for ext in config.NAME_EXTS)

    if not taken(base):
        return base
    n = 2
    while taken(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def scan_live(root, limit=0):
    """找保存目录里所有文件名带「实况」的视频，跳过已经配对好的（跳过 截帧/Cache 等无关目录）。"""
    root = Path(root)
    out = config.live_out_dir(root)
    index = load_live_index(out)
    items = []
    if root.exists():
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                here = Path(dirpath)
                if here == root:
                    dirnames[:] = [
                        d for d in dirnames
                        if d not in config.SKIP_DIRS and not d.startswith(".")
                    ]
                else:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name_f in filenames:
                    p = here / name_f
                    if p.suffix.lower() not in (".mp4", ".mov", ".m4v"):
                        continue
                    if "实况" not in p.name:
                        continue
                    rec = index.get(str(p.resolve())) or {}
                    name = rec.get("name") or ""
                    still = None
                    if name:
                        for ext in (".HEIC", ".JPG"):
                            cand = out / f"{name}{ext}"
                            if cand.exists():
                                still = cand
                                break
                    if not still:
                        guess = config.safe_name(p.stem, 90)
                        for ext in (".HEIC", ".JPG"):
                            cand = out / f"{guess}{ext}"
                            if cand.exists() and (out / f"{guess}.MOV").exists():
                                name, still = guess, cand
                                break
                    done = bool(still) and (out / f"{name}.MOV").exists()
                    try:
                        st = p.stat()
                        size, mtime = st.st_size, st.st_mtime
                    except OSError:
                        size, mtime = 0, 0
                    items.append({
                        "path": str(p),
                        "rel": str(p.relative_to(root)) if root in p.parents else p.name,
                        "name": name,
                        "done": done,
                        "size": size,
                        "time": source_time(p).strftime("%Y-%m-%d %H:%M"),
                        "mtime": mtime,
                        "thumb": str(still) if done else str(p),
                        "still": str(still) if still else "",
                    })
        except OSError as exc:
            config.get_logger().warning("扫描实况失败 %s: %s", root, exc)
    items.sort(key=lambda x: x["mtime"], reverse=True)
    if limit and limit > 0:
        items = items[:limit]
    return items, out


def save_still(cover_jpg, dest_dir, stem):
    """封面存成 HEIC（iOS 原生格式）；HEIC 组件不在时退回 JPEG，同样能和 MOV 配对。"""
    dest_dir = Path(dest_dir)
    try:
        from PIL import Image
        import pillow_heif
        pillow_heif.register_heif_opener()
        img = Image.open(cover_jpg)
        if img.mode != "RGB":
            img = img.convert("RGB")
        dest = dest_dir / f"{stem}.HEIC"
        img.save(dest, format="HEIF", quality=90)
        return dest, "heic"
    except Exception:
        dest = dest_dir / f"{stem}.JPG"
        shutil.copyfile(cover_jpg, dest)
        return dest, "jpeg"


def _probe_video_stream(ffmpeg, src_local):
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(src_local)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, **kwargs,
        )
        info = r.stderr or ""
    except Exception:
        return 0.0, "", ""
    dur = 0.0
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if dm:
        dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
    vm = re.search(r"Stream #[^\n]*?Video:\s*([A-Za-z0-9_]+)", info)
    am = re.search(r"Stream #[^\n]*?Audio:\s*([A-Za-z0-9_]+)", info)
    return dur, (vm.group(1).lower() if vm else ""), (am.group(1).lower() if am else "")


def convert_live_one(src, out_dir, base, ffmpeg, exiftool):
    uuid_str = str(uuid.uuid4()).upper()
    when = source_time(src)
    exif_date = when.strftime("%Y:%m:%d %H:%M:%S")
    scratch = Path(tempfile.mkdtemp(prefix="douk_live_"))
    cover = scratch / "cover.jpg"
    try:
        src_local = scratch / ("src" + (src.suffix.lower() or ".mp4"))
        try:
            shutil.copyfile(src, src_local)
        except OSError as exc:
            raise RuntimeError(f"读不了源文件：{exc}")
        try:
            config.run_tool(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(src_local), "-vf", "thumbnail=100", "-frames:v", "1",
                 "-q:v", "2", str(cover)],
                timeout=180,
            )
        except Exception:
            try:
                config.run_tool(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-ss", "0.4", "-i", str(src_local), "-frames:v", "1",
                     "-q:v", "2", str(cover)],
                    timeout=180,
                )
            except Exception as exc:
                raise RuntimeError(f"抽帧失败：{str(exc)[:200]}")
        if not cover.exists() or cover.stat().st_size == 0:
            raise RuntimeError("抽帧失败：视频里没有可用画面")

        still_tmp, kind = save_still(cover, scratch, "still")
        if not still_tmp.exists() or still_tmp.stat().st_size == 0:
            raise RuntimeError("封面编码失败")

        blob = scratch / "mn.bin"
        blob.write_bytes(build_makernote(uuid_str))
        try:
            config.run_tool(
                [exiftool, "-m", "-overwrite_original",
                 f"-MakerNoteApple<={blob}",
                 f"-EXIF:DateTimeOriginal={exif_date}",
                 f"-EXIF:CreateDate={exif_date}",
                 f"-EXIF:ModifyDate={exif_date}",
                 str(still_tmp)],
                timeout=120,
            )
        except Exception:
            try:
                config.run_tool(
                    [exiftool, "-m", "-overwrite_original",
                     f"-MakerNoteApple<={blob}", str(still_tmp)],
                    timeout=120,
                )
            except Exception as exc:
                raise RuntimeError(f"封面写配对信息失败：{str(exc)[:200]}")

        mov_tmp = scratch / "clip.MOV"
        dur, vcodec, acodec = _probe_video_stream(ffmpeg, src_local)
        copied = False
        if 0 < dur <= 3.02 and vcodec == "h264" and acodec in ("", "aac"):
            try:
                config.run_tool(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(src_local), "-map", "0:v:0", "-map", "0:a:0?",
                     "-c", "copy", "-movflags", "+faststart", str(mov_tmp)],
                    timeout=120,
                )
                copied = mov_tmp.exists() and mov_tmp.stat().st_size > 0
            except Exception:
                copied = False
        if not copied:
            try:
                config.run_tool(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(src_local), "-t", "3",
                     "-map", "0:v:0", "-map", "0:a:0?",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                     "-movflags", "+faststart", str(mov_tmp)],
                    timeout=300,
                )
            except Exception as exc:
                raise RuntimeError(f"封装视频失败：{str(exc)[:200]}")

        try:
            config.run_tool(
                [exiftool, "-m", "-overwrite_original",
                 f"-QuickTime:ContentIdentifier={uuid_str}",
                 f"-QuickTime:CreateDate={exif_date}",
                 f"-QuickTime:CreationDate={exif_date}",
                 str(mov_tmp)],
                timeout=120,
            )
        except Exception:
            try:
                config.run_tool(
                    [exiftool, "-m", "-overwrite_original",
                     f"-QuickTime:ContentIdentifier={uuid_str}", str(mov_tmp)],
                    timeout=120,
                )
            except Exception as exc:
                raise RuntimeError(f"视频写配对信息失败：{str(exc)[:200]}")

        still = out_dir / f"{base}{still_tmp.suffix}"
        mov = out_dir / f"{base}.MOV"
        shutil.move(str(still_tmp), str(still))
        shutil.move(str(mov_tmp), str(mov))

        return {
            "name": base, "still": str(still), "mov": str(mov),
            "kind": kind, "uuid": uuid_str, "when": exif_date,
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_live_job(job_id, files, root):
    job = state.get_job(job_id) or state._jobs[job_id]
    out = config.live_out_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    index = load_live_index(out)
    tools = config.tool_status()
    log = config.get_logger()
    ffmpeg, exiftool = tools["ffmpeg"], tools["exiftool"]
    if not ffmpeg or not exiftool:
        for p in files:
            item = job["items"].get(p)
            if item:
                item["status"] = "error"
                item["message"] = "缺少 ffmpeg 或 exiftool"
        job["running"] = False
        job["done_at"] = time.time()
        state.prune_jobs()
        return

    index_lock = threading.Lock()
    reserved = set()
    flush_state = {"dirty": 0, "last": time.time()}

    def _reserve_base(stem):
        with index_lock:
            cand = unique_base(out, config.safe_name(stem, 90))
            if cand in reserved:
                n = 2
                root_name = config.safe_name(stem, 90) or "实况"
                while (f"{root_name}_{n}" in reserved
                       or any((out / f"{root_name}_{n}{ext}").exists() for ext in config.NAME_EXTS)):
                    n += 1
                cand = f"{root_name}_{n}"
            reserved.add(cand)
            return cand

    def one(path_str):
        item = job["items"].get(path_str)
        if item is None:
            return
        if job["cancelled"]:
            item["status"] = "error"
            item["message"] = "已停止"
            return
        item["status"] = "converting"
        item["message"] = "抽帧 / 封装中"
        src = Path(path_str)
        if not src.exists():
            item["status"] = "error"
            item["message"] = "源文件不在了"
            return
        base = _reserve_base(src.stem)
        try:
            rec = convert_live_one(src, out, base, ffmpeg, exiftool)
            with index_lock:
                index[str(src.resolve())] = {
                    "name": rec["name"], "uuid": rec["uuid"],
                    "src": src.name, "when": rec["when"],
                }
                flush_state["dirty"] += 1
                now = time.time()
                if flush_state["dirty"] >= 5 or now - flush_state["last"] >= 2.0:
                    save_live_index(out, index)
                    flush_state["dirty"] = 0
                    flush_state["last"] = now
            item["status"] = "done"
            item["message"] = ""
            item["file"] = rec["still"]
        except Exception as exc:
            log.warning("实况转换失败 %s: %s", src.name, exc)
            for ext in config.NAME_EXTS:
                try:
                    (out / f"{base}{ext}").unlink()
                except OSError:
                    pass
            item["status"] = "error"
            item["message"] = str(exc)[:200]

    workers = min(3, max(1, len(files)))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, p) for p in files]
            for _ in as_completed(futures):
                job["finished"] += 1
    finally:
        with index_lock:
            if flush_state["dirty"] > 0:
                try:
                    save_live_index(out, index)
                except Exception as exc:
                    log.warning("保存实况索引失败: %s", exc)

    job["running"] = False
    job["done_at"] = time.time()
    state.prune_jobs()


def _move_to_mac_trash(src: Path) -> bool:
    trash = Path.home() / ".Trash"
    if not trash.is_dir():
        return False
    dest = trash / src.name
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        idx = 1
        while (trash / f"{stem}_{idx}{suffix}").exists():
            idx += 1
        dest = trash / f"{stem}_{idx}{suffix}"
    shutil.move(str(src), str(dest))
    return True


def recycle_files(paths):
    """把文件移到系统回收站/废纸篓（Windows 用 SHFileOperationW，macOS 移至 ~/.Trash）。"""
    if not paths:
        return 0
    if os.name != "nt":
        import sys
        cnt = 0
        for p in paths:
            fp = Path(p)
            try:
                if sys.platform == "darwin" and fp.exists() and _move_to_mac_trash(fp):
                    cnt += 1
                else:
                    fp.unlink()
                    cnt += 1
            except OSError:
                pass
        return cnt
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    joined = "\0".join(str(p) for p in paths) + "\0\0"
    buf = ctypes.create_unicode_buffer(joined)
    op = SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=ctypes.cast(buf, wintypes.LPCWSTR),
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0:
        raise RuntimeError(f"移入回收站失败（错误码 {res}）")
    return len(paths)
