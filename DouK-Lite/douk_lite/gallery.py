# -*- coding: utf-8 -*-
"""本地作品库扫描、作品归组与画廊数据聚合。"""

import re
from datetime import datetime
from pathlib import Path

from douk_lite import config, douk_api, state

POST_NAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ _]?(\d{2}[.:_]\d{2}[.:_]\d{2})-(视频|图集|实况)-(.*)$"
)
DIR_UID_RE = re.compile(r"^UID(\d+)_(.+)$")
FOLDER_SUFFIX = "_发布作品"


def _post_group_key(stem):
    """只在符合 DouK 命名规则且属于多文件分片时才剥掉末尾 _序号。"""
    m = POST_NAME_RE.match(stem)
    if not m:
        return stem
    ptype = m.group(3)
    stripped = re.sub(r"_\d{1,3}$", "", stem)
    if stripped != stem and ptype in ("图集", "实况"):
        return stripped
    return stem


def scan_creator_posts(folder, nickname=""):
    """把一个博主文件夹里的媒体文件归组成作品。返回 (posts, 文件数, 总字节)。"""
    groups = {}
    total_files = 0
    total_bytes = 0
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in config.IMAGE_EXT | config.VIDEO_EXT:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        total_files += 1
        total_bytes += st.st_size
        base = _post_group_key(p.stem)
        g = groups.get(base)
        if g is None:
            g = groups[base] = {"key": base, "files": []}
        g["files"].append((p, st.st_size, st.st_mtime))

    posts = []
    for base, g in groups.items():
        files = sorted(g["files"], key=lambda x: x[0].name)
        has_img = any(f[0].suffix.lower() in config.IMAGE_EXT for f in files)
        has_vid = any(f[0].suffix.lower() in config.VIDEO_EXT for f in files)

        m = POST_NAME_RE.match(base)
        if m:
            date, clock, ptype, rest = m.groups()
            desc = rest
            if nickname and rest.startswith(nickname):
                desc = rest[len(nickname):].lstrip("-_ ")
            elif "-" in rest:
                desc = rest.split("-", 1)[1]
            when = f"{date} {clock.replace('.', ':').replace('_', ':')}"
        else:
            date, ptype, desc = "", "", base
            when = datetime.fromtimestamp(max(f[2] for f in files)).strftime("%Y-%m-%d %H:%M")
            date = when[:10]

        if not ptype:
            ptype = "实况" if has_img and has_vid else ("视频" if has_vid else "图集")

        cover = next((f[0] for f in files if f[0].suffix.lower() in config.IMAGE_EXT), files[0][0])
        posts.append({
            "key": base,
            "title": (desc if desc else ("无标题" if m else base)),
            "type": ptype,
            "date": date,
            "time": when,
            "cover": str(cover),
            "count": len(files),
            "bytes": sum(f[1] for f in files),
            "files": [{
                "name": f[0].name,
                "path": str(f[0]),
                "kind": "video" if f[0].suffix.lower() in config.VIDEO_EXT else "image",
                "size": f[1],
            } for f in files],
            "frames": [],
        })

    # 把「截帧/<博主目录>/」里对应视频的截图挂到所属作品下
    frame_dir = folder.parent / config.FRAME_DIR / folder.name
    if frame_dir.is_dir() and posts:
        by_stem = {}
        for p_item in posts:
            for f_item in p_item["files"]:
                if f_item["kind"] == "video":
                    stem80 = config.safe_name(Path(f_item["path"]).stem, 80)
                    by_stem.setdefault(stem80, p_item)
        if by_stem:
            try:
                frame_files = sorted(frame_dir.iterdir(), key=lambda x: x.name)
            except OSError:
                frame_files = []
            for img in frame_files:
                if not img.is_file() or img.suffix.lower() not in config.IMAGE_EXT:
                    continue
                if "_帧_" not in img.stem:
                    continue
                prefix = img.stem.split("_帧_", 1)[0]
                target_post = by_stem.get(prefix)
                if not target_post:
                    continue
                try:
                    st = img.stat()
                except OSError:
                    continue
                target_post["frames"].append({
                    "name": img.name,
                    "path": str(img),
                    "kind": "image",
                    "size": st.st_size,
                    "is_frame": True,
                })

    posts.sort(key=lambda x: (x["time"], x["key"]), reverse=True)
    return posts, total_files, total_bytes


def creator_dir_info(root, entry=None, folder_name="", refresh=False):
    """定位某个博主的文件夹并统计作品；定位不到返回 None。"""
    root = Path(root)
    target = None
    if folder_name:
        name = Path(folder_name).name
        if name == folder_name:
            cand = root / name
            if cand.is_dir():
                target = cand
    if target is None and entry:
        stored = (entry.get("folder") or "").strip()
        if stored:
            cand = root / Path(stored).name
            if cand.is_dir():
                target = cand
        if target is None:
            uid = str(entry.get("uid") or "")
            nick = entry.get("nickname") or ""
            try:
                dirs = [d for d in root.iterdir() if d.is_dir()]
            except OSError:
                dirs = []
            if uid:
                target = next((d for d in dirs if uid in d.name), None)
            if target is None and nick:
                target = next((d for d in dirs if nick in d.name and d.name not in config.SKIP_DIRS), None)
    if target is None:
        return None

    try:
        name = target.name
        uid = ""
        nickname = name
        m = DIR_UID_RE.match(name)
        if m:
            uid, nickname = m.group(1), m.group(2)
        if nickname.endswith(FOLDER_SUFFIX):
            nickname = nickname[:-len(FOLDER_SUFFIX)]

        key = str(target.resolve())
        with state._lib_lock:
            cached = state._lib_cache.get(key)
        if cached and not refresh:
            posts, files, total = cached["posts"], cached["files"], cached["bytes"]
        else:
            posts, files, total = scan_creator_posts(target, nickname)
            with state._lib_lock:
                state._lib_cache[key] = {"posts": posts, "files": files, "bytes": total}
        return {
            "folder": name, "path": str(target), "uid": uid,
            "nickname": nickname, "posts": posts, "files": files,
            "bytes": total, "mtime": target.stat().st_mtime,
        }
    except OSError:
        return None


def discover_creator_dirs(root, refresh=False):
    root = Path(root)
    out = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for d in dirs:
        if d.name in config.SKIP_DIRS or d.name.startswith("."):
            continue
        info = creator_dir_info(root, folder_name=d.name, refresh=refresh)
        if info and info["files"]:
            out.append(info)
    return out


def creators_payload(dirs=None, entries=None):
    """名单 + 磁盘上的博主文件夹，合成界面要的一份列表。"""
    cfg = config.load_config()
    root = Path(cfg.get("root") or config.VOLUME_DIR)
    if entries is None:
        entries = douk_api.load_creators()
    if dirs is None:
        dirs = discover_creator_dirs(root)
    by_folder = {d["folder"]: d for d in dirs}
    used = set()
    out = []

    for e in entries:
        info = None
        stored = (e.get("folder") or "").strip()
        if stored and Path(stored).name in by_folder:
            info = by_folder[Path(stored).name]
        if info is None:
            uid = str(e.get("uid") or "")
            nick = e.get("nickname") or ""
            for d in dirs:
                if d["folder"] in used:
                    continue
                if (uid and uid == d.get("uid")) or (nick and nick == d["nickname"]):
                    info = d
                    break
        item = dict(e)
        item["nickname"] = e.get("nickname") or (info or {}).get("nickname") or ""
        item["uid"] = e.get("uid") or (info or {}).get("uid") or ""
        item["remark"] = (e.get("remark") or "").strip()
        if info:
            used.add(info["folder"])
            item.update({
                "folder": info["folder"],
                "path": info["path"],
                "posts": len(info["posts"]),
                "files": info["files"],
                "bytes": info["bytes"],
                "cover": info["posts"][0]["cover"] if info["posts"] else (e.get("cover") or ""),
                "local": True,
            })
        else:
            item.update({
                "folder": "", "path": "", "posts": 0, "files": 0,
                "bytes": 0, "local": False,
            })
        out.append(item)

    for d in dirs:
        if d["folder"] in used:
            continue
        out.append({
            "sec_uid": "", "uid": d["uid"], "nickname": d["nickname"],
            "url": "", "folder": d["folder"], "path": d["path"],
            "cover": d["posts"][0]["cover"] if d["posts"] else "",
            "posts": len(d["posts"]), "files": d["files"], "bytes": d["bytes"],
            "last_active": datetime.fromtimestamp(d["mtime"]).strftime("%Y-%m-%d %H:%M:%S"),
            "local": True, "local_only": True,
        })

    out.sort(key=lambda c: (c.get("last_active") or "", c.get("posts") or 0), reverse=True)
    return out


def _gallery_snapshot(root, refresh=False):
    root_key = str(Path(root).resolve()) if Path(root).exists() else str(root)
    if not refresh:
        with state._lib_lock:
            snap = state._gallery_cache.get(root_key)
        if snap is not None:
            return snap

    entries = douk_api.load_creators()
    dirs = discover_creator_dirs(root, refresh=refresh)
    creators = creators_payload(dirs=dirs, entries=entries)
    by_folder = {c.get("folder"): c for c in creators if c.get("folder")}

    posts = []
    type_counts = {"视频": 0, "图集": 0, "实况": 0}
    total_files = 0
    total_bytes = 0
    for d in dirs:
        c = by_folder.get(d["folder"]) or {}
        total_files += d["files"]
        total_bytes += d["bytes"]
        meta = {
            "nickname": c.get("nickname") or d["nickname"],
            "remark": (c.get("remark") or "").strip(),
            "folder": d["folder"],
            "sec_uid": c.get("sec_uid") or "",
            "url": c.get("url") or "",
            "uid": c.get("uid") or d.get("uid") or "",
            "cover": c.get("cover") or (d["posts"][0]["cover"] if d["posts"] else ""),
        }
        for p in d["posts"]:
            item = dict(p)
            item["creator"] = meta
            posts.append(item)
            type_counts[p["type"]] = type_counts.get(p["type"], 0) + 1

    posts.sort(key=lambda x: (x.get("time") or "", x.get("key") or ""), reverse=True)
    local = [c for c in creators if c.get("local")]
    snap = {
        "root": str(root),
        "creators": creators,
        "posts": posts,
        "stats": {
            "creators": len(creators),
            "local_creators": len(local),
            "posts": len(posts),
            "files": total_files,
            "bytes": total_bytes,
            "types": type_counts,
        },
    }
    with state._lib_lock:
        state._gallery_cache[root_key] = snap
    return snap


def gallery_payload(folder="", post_type="", q="", sort="newest", offset=0, limit=60, refresh=False):
    """一次调用给画廊首页备齐数据：博主卡片、统计、全部作品（可筛选排序分页）。"""
    cfg = config.load_config()
    root = Path(cfg.get("root") or config.VOLUME_DIR)
    snap = _gallery_snapshot(root, refresh=refresh)

    keyword = (q or "").strip().casefold()
    picked = []
    context_counts = {"视频": 0, "图集": 0, "实况": 0}
    for p in snap["posts"]:
        if folder and (p["creator"].get("folder") or "") != folder:
            continue
        if keyword:
            hay = f"{p.get('title') or ''} {p['creator'].get('nickname') or ''} {p['creator'].get('remark') or ''}".casefold()
            if keyword not in hay:
                continue
        context_counts[p["type"]] = context_counts.get(p["type"], 0) + 1
        if post_type and p["type"] != post_type:
            continue
        picked.append(p)

    if sort == "oldest":
        picked.sort(key=lambda x: (x.get("time") or "", x.get("key") or ""))
    elif sort == "largest":
        picked.sort(key=lambda x: (x.get("bytes") or 0, x.get("time") or ""), reverse=True)
    elif sort == "most":
        picked.sort(key=lambda x: (x.get("count") or 0, x.get("time") or ""), reverse=True)

    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 60
    page = picked[offset:offset + limit]

    return {
        "ok": True,
        "root": snap["root"],
        "creators": snap["creators"],
        "stats": snap["stats"],
        "counts": {
            "types": context_counts,
            "filtered": len(picked),
        },
        "posts": page,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(picked),
    }
