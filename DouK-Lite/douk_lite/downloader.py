# -*- coding: utf-8 -*-
"""文件下载、文件头类型嗅探、图文高清源探测与旧版静态图整理。"""

import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from douk_lite import config, state


def guess_ext(url, fallback):
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in {".mp4", ".mov", ".m4v", ".jpeg", ".jpg", ".png", ".webp", ".heic", ".gif"}:
        return ext
    return fallback


def sniff_ext(path, default=".mp4"):
    """按文件头判断真实类型。抖音的视频地址没有扩展名，只看 URL 会误判成 jpeg。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return default
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if head[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
            return ".heic"
        if head[8:12] in (b"qt  ",):
            return ".mov"
        return ".mp4"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:4] == b"GIF8":
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return default


def find_existing(folder, stem):
    for ext in (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".m4v"):
        p = folder / (stem + ext)
        try:
            if p.exists() and p.stat().st_size > 0:
                return p
        except OSError:
            pass
    return None


def default_ext(item_type):
    return ".jpeg" if item_type == "图集" else ".mp4"


def item_base_stem(item):
    """生成作品文件名前缀，供下载落盘与本地查重共用。"""
    return config.safe_name(
        "-".join(
            x for x in (
                item.get("create_time"), item.get("type"),
                item.get("nickname"), item.get("desc"),
            ) if x
        )
    )


_TS_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2})[._](\d{2})[._](\d{2})-")


def _ts_key(create_time):
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2})[:._](\d{2})[:._](\d{2})", str(create_time or "").strip())
    if not m:
        return ""
    return f"{m.group(1)}_{m.group(2)}_{m.group(3)}"


def _candidate_folders(root, item, template):
    folders = []
    primary = config.item_folder(root, item, template)
    folders.append(primary)
    sec = str(item.get("sec_uid") or "").strip()
    if sec:
        try:
            creators = config._read_json_cached(config.CREATORS_FILE, encoding="utf-8") or []
            for c in creators:
                if isinstance(c, dict) and c.get("sec_uid") == sec and c.get("folder"):
                    alt = Path(root) / Path(c["folder"]).name
                    if alt not in folders:
                        folders.append(alt)
                    break
        except Exception:
            pass
    return folders


def _iter_by_ts_prefix(folder, ts_key, live_folder, video_folder):
    """遍历按精确到秒的发布时间前缀匹配的已下载文件。"""
    if not ts_key or not folder.exists():
        return
    dirs = (
        folder,
        config.sub_folder(folder, live_folder, "实况"),
        config.sub_folder(folder, video_folder, "视频"),
    )
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            with os.scandir(d) as it:
                for entry in it:
                    name = entry.name
                    if name.endswith((".part", ".tmp")):
                        continue
                    m = _TS_PREFIX_RE.match(name)
                    if not m:
                        continue
                    if f"{m.group(1)}_{m.group(2)}_{m.group(3)}" != ts_key:
                        continue
                    try:
                        if entry.is_file() and entry.stat().st_size > 0:
                            yield Path(entry.path)
                    except OSError:
                        pass
        except OSError:
            pass


def _find_by_ts_prefix(folder, ts_key, live_folder, video_folder):
    """按精确到秒的发布时间前缀匹配已下载文件，兼容博主改名、文案变化或实况文件数差异。"""
    return next(_iter_by_ts_prefix(folder, ts_key, live_folder, video_folder), None)


def _count_by_ts_prefix(folder, ts_key, live_folder, video_folder):
    """统计按精确到秒的发布时间前缀匹配到的已下载文件数。"""
    return sum(1 for _ in _iter_by_ts_prefix(folder, ts_key, live_folder, video_folder))


def item_download_status(root, item, cfg=None):
    """判断一条已解析作品在当前保存目录中的下载状态：'all' | 'partial' | 'none'。"""
    cfg = cfg or config.load_config()
    urls = item.get("downloads") or []
    if not urls:
        return "none"
    live_folder = cfg.get("live_folder") or "实况"
    video_folder = cfg.get("video_folder") or "视频"
    template = cfg.get("folder_template") or ""
    folders = _candidate_folders(root, item, template)
    base = item_base_stem(item)
    total = len(urls)
    multi = total > 1
    best_hits = 0
    for folder in folders:
        if not folder.exists():
            continue
        hits = 0
        for idx in range(1, total + 1):
            stem = f"{base}_{idx}" if multi else base
            if find_existing_any(folder, stem, live_folder, video_folder):
                hits += 1
        if hits >= total:
            return "all"
        if hits > best_hits:
            best_hits = hits
    if best_hits > 0:
        return "partial"

    ts_key = _ts_key(item.get("create_time"))
    if ts_key:
        for folder in folders:
            matched_count = _count_by_ts_prefix(folder, ts_key, live_folder, video_folder)
            if matched_count > 0:
                if total > 1 and item.get("type") != "实况" and matched_count < total:
                    return "partial"
                return "all"
    return "none"


def item_exists(root, item, cfg=None):
    """判断一条已解析作品是否已完整存在于当前保存目录中。"""
    return item_download_status(root, item, cfg) == "all"


def _stem_variants(stem):
    yield stem
    # 兼容早期原程序直下文件使用点号分隔时分秒（HH.MM.SS）的命名
    alt = re.sub(r"^(\d{4}-\d{2}-\d{2} \d{2})_(\d{2})_(\d{2})(-)", r"\1.\2.\3\4", stem)
    if alt != stem:
        yield alt


def find_existing_any(base_folder, stem, live_folder, video_folder):
    """同一个作品可能在博主文件夹、实况、视频三处，都查一遍。"""
    dirs = (
        base_folder,
        config.sub_folder(base_folder, live_folder, "实况"),
        config.sub_folder(base_folder, video_folder, "视频"),
    )
    for cand_stem in _stem_variants(stem):
        for d in dirs:
            found = find_existing(d, cand_stem)
            if found:
                return found
    return None


def tidy_live_images(root, live_folder):
    """旧版本把实况帖里的静态图也塞进了「实况」目录，把它们挪回博主文件夹。"""
    root = Path(root)
    live_name = config.safe_name(live_folder or "实况", 40)
    moved = skipped = 0
    if not root.exists():
        return moved, skipped
    for author in root.iterdir():
        try:
            if not author.is_dir():
                continue
        except OSError:
            continue
        folder = author / live_name
        try:
            if not folder.is_dir():
                continue
        except OSError:
            continue
        for p in folder.iterdir():
            try:
                if not p.is_file() or p.suffix.lower() not in config.IMAGE_EXT:
                    continue
                dest = author / p.name
                if dest.exists():
                    skipped += 1
                    continue
                p.replace(dest)
                moved += 1
            except OSError:
                skipped += 1
    return moved, skipped


def _is_xhs_url(url):
    return "xhscdn.com" in (url or "") or "xiaohongshu.com" in (url or "")


def download_file(url, dest, cookie="", retries=3):
    is_xhs = _is_xhs_url(url)
    headers = {
        "User-Agent": config.UA,
        "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
        "Accept": "*/*",
        "Connection": "close",
    }
    if cookie and not is_xhs:
        headers["Cookie"] = cookie
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last_exc = None
    for attempt in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp, open(dest, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1024 * 256)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404, 410):
                raise
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        if attempt + 1 < retries:
            time.sleep(0.5 * (attempt + 1))
    raise last_exc


JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def is_image_head(head):
    if head[:3] == b"\xff\xd8\xff":
        return True
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return head[:4] == b"GIF8"


def image_dims(head):
    """从文件头里取 (宽, 高)，认不出来返回 (0, 0)。"""
    try:
        if head[:3] == b"\xff\xd8\xff":
            i, n = 2, len(head)
            while i + 9 < n:
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seglen = int.from_bytes(head[i + 2:i + 4], "big")
                if marker in JPEG_SOF:
                    h = int.from_bytes(head[i + 5:i + 7], "big")
                    w = int.from_bytes(head[i + 7:i + 9], "big")
                    return w, h
                i += 2 + max(seglen, 2)
            return 0, 0
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            kind = head[12:16]
            if kind == b"VP8 ":
                return (
                    int.from_bytes(head[26:28], "little") & 0x3FFF,
                    int.from_bytes(head[28:30], "little") & 0x3FFF,
                )
            if kind == b"VP8L":
                bits = int.from_bytes(head[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return (
                    int.from_bytes(head[24:27], "little") + 1,
                    int.from_bytes(head[27:30], "little") + 1,
                )
            return 0, 0
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except Exception:
        pass
    return 0, 0


def image_variants(url):
    """列出同一张图的不同取法：原地址、去模板、q100、jpeg 等。"""
    if not url or ("douyinpic" not in url and "aweme-images" not in url):
        return [url]
    head, sep, query = url.partition("?")
    path, tilde, tpl = head.partition("~")
    if not tilde:
        return [url]
    qs = ("?" + query) if sep else ""
    tpl_name = tpl.split(":")[0] or "tplv-dy-aweme-images"
    for tail in (".webp", ".jpeg", ".jpg", ".image"):
        if tpl_name.endswith(tail):
            tpl_name = tpl_name[: -len(tail)]
    cands = [
        url,
        path + qs,
        f"{path}~{tpl_name}:q100.webp{qs}",
        f"{path}~{tpl_name}:q100.jpeg{qs}",
        f"{path}~{tpl_name}:q75.jpeg{qs}",
    ]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def probe_image(url, cookie="", timeout=8):
    """只取前 32KB，读分辨率 + 总大小；不是图片就返回 None。"""
    is_xhs = _is_xhs_url(url)
    headers = {
        "User-Agent": config.UA,
        "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Range": "bytes=0-32767",
    }
    if cookie and not is_xhs:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        head = resp.read(32768)
        total = 0
        cr = resp.headers.get("Content-Range") or ""
        if "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[1])
            except ValueError:
                total = 0
        if not total:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
    if not head or not is_image_head(head):
        return None
    w, h = image_dims(head)
    return {"url": url, "w": w, "h": h, "size": total}


def _probe_safe(url, cookie):
    try:
        with state._probe_sem:
            return probe_image(url, cookie)
    except Exception:
        return None


def _variant_score(c):
    area = c["w"] * c["h"]
    m = re.search(r":q(\d+)", c["url"])
    if m:
        q = int(m.group(1))
    elif "~tplv" not in c["url"]:
        q = 100
    else:
        q = 0
    fmt = 1 if re.search(r"\.jpe?g", c["url"], re.I) else 0
    return (area, q, fmt, c["size"])


def best_image_url(url, cookie=""):
    """返回 (最清晰的地址, 探测结果)。探测不出来就原样返回，绝不让下载变差。"""
    cands = image_variants(url)
    if len(cands) <= 1:
        return url, None
    best = None
    try:
        with ThreadPoolExecutor(max_workers=min(3, len(cands))) as pool:
            for res in pool.map(lambda u: _probe_safe(u, cookie), cands):
                if not res:
                    continue
                if best is None or _variant_score(res) > _variant_score(best):
                    best = res
    except Exception:
        best = None
    if not best:
        return url, None
    return best["url"], best


def run_download(job_id, items, cfg):
    root = Path(cfg["root"])
    live_folder = cfg.get("live_folder") or "实况"
    video_folder = cfg.get("video_folder") or "视频"
    template = cfg.get("folder_template") or ""
    cookie = config.cookie_header()
    log = config.get_logger()
    job = state.get_job(job_id) or state._jobs[job_id]
    mkdir_lock = threading.Lock()

    def one(item):
        item_state = job["items"][item["id"]]
        if job["cancelled"]:
            item_state["status"] = "error"
            item_state["message"] = "已停止"
            return
        urls = item.get("downloads") or []
        if not urls:
            item_state["status"] = "error"
            item_state["message"] = "没有可下载的地址"
            return
        if item_exists(root, item, cfg):
            ts_key = _ts_key(item.get("create_time"))
            for cf in _candidate_folders(root, item, template):
                found = _find_by_ts_prefix(cf, ts_key, live_folder, video_folder)
                if found:
                    item_state["file"] = item_state["file"] or str(found)
                    break
            item_state["status"] = "skip"
            item_state["message"] = "已存在"
            return
        folder = config.item_folder(root, item, template)
        try:
            with mkdir_lock:
                folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning("创建目录失败 %s: %s", folder, exc)
            item_state["status"] = "error"
            item_state["message"] = f"建目录失败: {exc}"
            return

        base = item_base_stem(item)
        multi = len(urls) > 1
        done_n = skip_n = err_n = 0
        last_err = ""
        hi_msg = ""
        for idx, url in enumerate(urls, 1):
            if job["cancelled"]:
                if not (done_n or skip_n):
                    item_state["status"] = "error"
                    item_state["message"] = "已停止"
                break
            stem = f"{base}_{idx}" if multi else base
            old = find_existing_any(folder, stem, live_folder, video_folder)
            if old:
                skip_n += 1
                item_state["file"] = item_state["file"] or str(old)
                continue
            item_state["status"] = "downloading"
            tmp = folder / (stem + ".part")
            use_url, info = url, None
            if cfg.get("best_image", True):
                use_url, info = best_image_url(url, cookie)
            try:
                try:
                    download_file(use_url, tmp, cookie)
                except Exception:
                    if use_url == url:
                        raise
                    download_file(url, tmp, cookie)
                    use_url, info = url, None
                ext = sniff_ext(tmp, default_ext(item.get("type")))
                dest_dir = config.target_folder(folder, item, ext, live_folder, video_folder)
                if dest_dir != folder:
                    with mkdir_lock:
                        dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / (stem + ext)
                tmp.replace(dest)
                done_n += 1
                if info and use_url != url:
                    hi_msg = (
                        f"高清 {info['w']}×{info['h']}" if info["w"]
                        else "已换高清源"
                    )
                item_state["file"] = str(dest)
            except Exception as exc:
                err_n += 1
                last_err = str(exc)[:160]
                log.warning("下载失败 [%s] %s: %s", item.get("id"), stem, exc)
                try:
                    tmp.unlink()
                except OSError:
                    pass
        if err_n:
            item_state["status"] = "error"
            if multi:
                ok_n = done_n + skip_n
                item_state["message"] = f"部分完成 {ok_n}/{len(urls)}：{last_err}"
            else:
                item_state["message"] = last_err
        elif job["cancelled"] and (done_n + skip_n) < len(urls):
            item_state["status"] = "error"
            item_state["message"] = f"已停止 ({done_n + skip_n}/{len(urls)})" if multi else "已停止"
        elif done_n:
            item_state["status"] = "done"
            item_state["message"] = hi_msg
        elif skip_n:
            item_state["status"] = "skip"
            item_state["message"] = "已存在"

    workers = max(1, min(int(cfg.get("workers") or 3), 6))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, it) for it in items]
        for _ in as_completed(futures):
            job["finished"] += 1
    try:
        for it in items:
            state.invalidate_library(config.item_folder(root, it, template).name)
    except Exception:
        pass
    job["running"] = False
    job["done_at"] = time.time()
    state.prune_jobs()
