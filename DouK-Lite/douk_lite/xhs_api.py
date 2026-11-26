# -*- coding: utf-8 -*-
"""小红书链接识别、纯算签名（基于 Cloxl/xhshow）、主页/单篇解析与媒体提取。"""

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from xhshow import Xhshow

from douk_lite import config, douk_api, state

XHS_WEB = "https://www.xiaohongshu.com"
XHS_API = "https://edith.xiaohongshu.com"

URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
XHS_HOST_RE = re.compile(r"(?:xiaohongshu\.com|rednote\.com|xhslink\.com)", re.I)
XHS_PROFILE_NOTE_RE = re.compile(
    r"(?:xiaohongshu|rednote)\.com/user/profile/([A-Za-z0-9]+)/([A-Za-z0-9]+)"
)
XHS_NOTE_RE = re.compile(
    r"(?:xiaohongshu|rednote)\.com/(?:explore|discovery/item)/([A-Za-z0-9]+)"
)
XHS_USER_RE = re.compile(
    r"(?:xiaohongshu|rednote)\.com/user/profile/([A-Za-z0-9]+)"
)

_SIGNER = Xhshow()


_SHORT_LIVED_COOKIES = {"acw_tc", "sec_poison_id", "loadts", "unread"}


def parse_cookie_dict(raw):
    """把 Cookie 字符串、字典、Cookie-Editor JSON 或 DevTools 表格粘贴文本解析成 {name: value}。"""
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if k and v is not None}
    text = str(raw or "").strip()
    if not text:
        return {}
    out = {}
    if text.startswith("["):
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                        k = str(item["name"]).strip()
                        if k not in _SHORT_LIVED_COOKIES:
                            out[k] = str(item["value"]).strip()
                if out:
                    return out
        except Exception:
            pass
    if "\t" in text:
        for line in text.splitlines():
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) >= 2 and cols[0] and cols[0] not in _SHORT_LIVED_COOKIES:
                if re.fullmatch(r"[A-Za-z0-9_\-]+", cols[0]):
                    out[cols[0]] = cols[1]
        if out:
            return out
    for part in text.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _ensure_cookies(raw_cookie):
    cookies = parse_cookie_dict(raw_cookie)
    has_session = bool(cookies.get("web_session"))
    if not cookies.get("a1"):
        cookies["a1"] = Xhshow.generate_a1()
    if not cookies.get("webId"):
        cookies["webId"] = Xhshow.generate_web_id(cookies["a1"])
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return cookies, cookie_str, has_session


def _extract_xhs_url(text):
    urls = [u.rstrip(".,;:!?'\")]") for u in URL_RE.findall(text or "")]
    for u in urls:
        if XHS_HOST_RE.search(u):
            return u
    return ""


def _resolve_short(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": config.UA})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.geturl()
    except Exception:
        return ""


def parse_xhs_target(text, _depth=0):
    """识别小红书主页链接、作品链接、xhslink 短链或内部 xhs_<uid> 标识。"""
    text = (text or "").strip()
    if not text:
        return None

    if re.fullmatch(r"xhs_([A-Za-z0-9]{16,32})", text):
        uid = text[4:]
        return {
            "platform": "xhs",
            "kind": "user",
            "id": uid,
            "xsec_token": "",
            "xsec_source": "pc_user",
            "url": f"{XHS_WEB}/user/profile/{uid}",
        }

    url = _extract_xhs_url(text)
    if not url:
        return None

    parsed = urllib.parse.urlparse(url)
    qs = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
    xsec_token = qs.get("xsec_token", "")
    xsec_source = qs.get("xsec_source", "")

    m = XHS_PROFILE_NOTE_RE.search(url)
    if m:
        return {
            "platform": "xhs",
            "kind": "work",
            "id": m.group(2),
            "user_id": m.group(1),
            "xsec_token": xsec_token,
            "xsec_source": xsec_source or "pc_user",
            "url": url,
        }

    m = XHS_NOTE_RE.search(url)
    if m:
        return {
            "platform": "xhs",
            "kind": "work",
            "id": m.group(1),
            "xsec_token": xsec_token,
            "xsec_source": xsec_source or "pc_feed",
            "url": url,
        }

    m = XHS_USER_RE.search(url)
    if m:
        return {
            "platform": "xhs",
            "kind": "user",
            "id": m.group(1),
            "xsec_token": xsec_token,
            "xsec_source": xsec_source or "pc_user",
            "url": url,
        }

    if _depth < 3 and "xhslink.com" in parsed.netloc.lower():
        real = _resolve_short(url)
        if real and real != url:
            return parse_xhs_target(real, _depth + 1)

    return None


def _signed_request(method, path, params=None, payload=None, raw_cookie="", x_rap=True, timeout=20):
    cookies, cookie_str, _ = _ensure_cookies(raw_cookie)
    uri = XHS_API + path
    if method == "GET":
        headers = _SIGNER.sign_headers_get(
            uri=uri, cookies=cookies, params=params or {}, x_rap=x_rap,
        )
        full_url = uri + ("?" + urllib.parse.urlencode(params) if params else "")
        body = None
    else:
        headers = _SIGNER.sign_headers_post(
            uri=uri, cookies=cookies, payload=payload or {}, x_rap=x_rap,
        )
        headers["Content-Type"] = "application/json;charset=UTF-8"
        full_url = uri
        body = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    headers.update({
        "User-Agent": config.UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": XHS_WEB,
        "Referer": XHS_WEB + "/",
        "Cookie": cookie_str,
    })

    req = urllib.request.Request(full_url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
            return json.loads(raw)
        except Exception:
            raise RuntimeError(f"HTTP {exc.code}: {raw[:160] or exc.reason}") from exc


def _api_error_message(resp):
    code = resp.get("code")
    msg = resp.get("msg") or "请求失败"
    if code in (-100, -101):
        return "小红书登录已过期或未登录（-100/-101）：请到右上角「设置 → 小红书 Cookie」更新一次（可直接粘贴 F12 表格或 Cookie 字符串）"
    if code in (300012, 300013, 461, 471):
        return f"小红书触发风控或验证码（{code}：{msg}），请稍后再试或更新 Cookie"
    return f"小红书接口返回失败（{code}）：{msg}" if code is not None else msg


def _format_ts(ms_or_sec):
    try:
        val = float(ms_or_sec or 0)
    except (TypeError, ValueError):
        return 0, ""
    if val <= 0:
        return 0, ""
    if val > 10 ** 11:
        val = val / 1000.0
    ts = int(val)
    return ts, datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H.%M.%S")


def _extract_image_token(url):
    if not url or not isinstance(url, str):
        return ""
    parts = url.split("/")
    if len(parts) < 6:
        return ""
    return "/".join(parts[5:]).split("!")[0]


def _format_cdn_url(url):
    if not url or not isinstance(url, str):
        return ""
    if url.startswith("http://"):
        return "https://" + url[7:]
    if url.startswith("//"):
        return "https:" + url
    return url


def _extract_live_url(img_item):
    stream = img_item.get("stream")
    if not isinstance(stream, dict):
        return ""
    for codec in ("h264", "h265", "av1"):
        arr = stream.get(codec)
        if not isinstance(arr, list) or not arr:
            continue
        first = arr[0] if isinstance(arr[0], dict) else {}
        backups = first.get("backup_urls") or first.get("backupUrls") or []
        cand = (backups[0] if backups else "") or first.get("master_url") or first.get("masterUrl") or ""
        cand = _format_cdn_url(cand)
        if cand:
            return cand
    return ""


def _extract_video_urls(note_card):
    video = note_card.get("video")
    if not isinstance(video, dict):
        return []
    consumer = video.get("consumer") or {}
    origin_key = consumer.get("origin_video_key") or consumer.get("originVideoKey") or ""
    if origin_key:
        return [f"https://sns-video-bd.xhscdn.com/{origin_key}"]

    media = video.get("media") or {}
    stream = media.get("stream") or {}
    candidates = []
    if isinstance(stream, dict):
        for items in stream.values():
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        candidates.append(it)
    if not candidates:
        return []
    candidates.sort(key=lambda x: int(x.get("height") or 0))
    best = candidates[-1]
    backups = best.get("backup_urls") or best.get("backupUrls") or []
    url = (backups[0] if backups else "") or best.get("master_url") or best.get("masterUrl") or ""
    url = _format_cdn_url(url)
    return [url] if url else []


def _extract_image_urls(note_card):
    images = note_card.get("image_list") or note_card.get("imageList") or []
    if not isinstance(images, list):
        return [], []
    img_urls = []
    live_urls = []
    for item in images:
        if not isinstance(item, dict):
            continue
        token = (
            item.get("file_id")
            or item.get("fileId")
            or _extract_image_token(item.get("url_default") or item.get("urlDefault") or "")
            or _extract_image_token(item.get("url_pre") or item.get("urlPre") or "")
        )
        if token:
            if token.startswith("spectrum/"):
                img_urls.append(f"https://ci.xiaohongshu.com/{token}?imageView2/format/png")
            else:
                img_urls.append(f"https://sns-img-bd.xhscdn.com/{token}")
        else:
            fallback = _format_cdn_url(
                item.get("url_default") or item.get("urlDefault")
                or item.get("url_pre") or item.get("urlPre") or ""
            )
            if fallback:
                img_urls.append(fallback)

        if item.get("live_photo") or item.get("livePhoto"):
            live_u = _extract_live_url(item)
            if live_u:
                live_urls.append(live_u)
    return img_urls, live_urls


def normalize_xhs_note(note_card, user_id_hint=""):
    """把小红书 feed / __INITIAL_STATE__ 笔记转成 DouK-Lite 统一作品格式。"""
    if not isinstance(note_card, dict):
        return None
    note_id = str(note_card.get("note_id") or note_card.get("noteId") or "").strip()
    if not note_id:
        return None

    user = note_card.get("user") if isinstance(note_card.get("user"), dict) else {}
    uid = str(user.get("user_id") or user.get("userId") or user_id_hint or "").strip()
    nickname = str(user.get("nickname") or user.get("nick_name") or user.get("nickName") or "").strip()

    title = str(note_card.get("title") or note_card.get("display_title") or "").strip()
    body_desc = str(note_card.get("desc") or "").strip()
    if title and body_desc and body_desc != title:
        desc = f"{title} {body_desc}"
    else:
        desc = title or body_desc
    desc = re.sub(r"\s+", " ", desc).strip()[:120]

    ts, create_time = _format_ts(note_card.get("time") or note_card.get("last_update_time") or note_card.get("lastUpdateTime"))

    raw_type = str(note_card.get("type") or "normal").lower()
    img_urls, live_urls = _extract_image_urls(note_card)

    if raw_type == "video":
        downloads = _extract_video_urls(note_card)
        item_type = "视频"
        if not downloads and img_urls:
            downloads = img_urls
            item_type = "图集"
    elif live_urls:
        downloads = live_urls + img_urls
        item_type = "实况"
    else:
        downloads = img_urls
        item_type = "图集"

    cover = img_urls[0] if img_urls else ""
    if not cover:
        cover_obj = note_card.get("cover") if isinstance(note_card.get("cover"), dict) else {}
        cover = _format_cdn_url(cover_obj.get("url_default") or cover_obj.get("url_pre") or "")

    return {
        "platform": "xhs",
        "id": note_id,
        "type": item_type,
        "desc": desc,
        "create_time": create_time,
        "create_timestamp": ts,
        "nickname": nickname,
        "uid": uid,
        "sec_uid": f"xhs_{uid}" if uid else "",
        "duration": "",
        "cover": cover,
        "is_top": 0,
        "downloads": [u for u in downloads if isinstance(u, str) and u.startswith("http")],
    }


def _fetch_note_from_html(note_id, target_url, raw_cookie=""):
    """当未登录或 feed 接口受限时，尝试从网页 HTML __INITIAL_STATE__ 读取单篇笔记。"""
    url = target_url or f"{XHS_WEB}/explore/{note_id}"
    _, cookie_str, _ = _ensure_cookies(raw_cookie)
    req = urllib.request.Request(url, headers={
        "User-Agent": config.UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": cookie_str,
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r"window\.__INITIAL_STATE__=(\{.*?\})</script>", html, re.S)
    if not m:
        return None
    data = json.loads(m.group(1).replace("undefined", "null"))
    detail_map = ((data.get("note") or {}).get("noteDetailMap") or {})
    entry = detail_map.get(note_id)
    if not isinstance(entry, dict) and detail_map:
        entry = next(iter(detail_map.values()), {})
    note = (entry or {}).get("note") if isinstance(entry, dict) else None
    return normalize_xhs_note(note) if isinstance(note, dict) else None


def fetch_note_detail(note_id, xsec_token="", xsec_source="pc_user", raw_cookie="", target_url=""):
    """调用 /api/sns/web/v1/feed 获取笔记完整信息；必要时回退 HTML。"""
    payload = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": xsec_source or "pc_user",
        "xsec_token": xsec_token or "",
    }
    resp = _signed_request("POST", "/api/sns/web/v1/feed", payload=payload, raw_cookie=raw_cookie)
    if resp.get("success"):
        items = ((resp.get("data") or {}).get("items") or [])
        if items and isinstance(items[0], dict):
            note_card = items[0].get("note_card") or items[0].get("noteCard")
            if isinstance(note_card, dict):
                return normalize_xhs_note(note_card)

    if target_url:
        try:
            fallback = _fetch_note_from_html(note_id, target_url, raw_cookie)
            if fallback:
                return fallback
        except Exception:
            pass
    raise RuntimeError(_api_error_message(resp))


def run_xhs_work_job(job, target):
    """解析单篇小红书作品。"""
    cfg = config.load_config()
    raw_cookie = cfg.get("xhs_cookie") or ""
    note_id = target["id"]
    job["phase"] = "正在获取小红书作品信息…"
    try:
        it = fetch_note_detail(
            note_id,
            xsec_token=target.get("xsec_token") or "",
            xsec_source=target.get("xsec_source") or "pc_feed",
            raw_cookie=raw_cookie,
            target_url=target.get("url") or "",
        )
    except Exception as exc:
        state.remember_parse(job["id"], [])
        job.update(
            running=False, status="error",
            message=f"取小红书作品失败：{exc}",
            phase="", note="", waiting=0, done_at=time.time(),
        )
        return job

    items = [it] if it and it["id"] else []
    state.remember_parse(job["id"], items)
    if items:
        try:
            douk_api.remember_creator(items, full=False)
        except Exception:
            pass
    job.update(
        running=False,
        status="done" if items else "error",
        count=len(items),
        nickname=items[0]["nickname"] if items else "",
        message="" if items else "没有取到小红书作品数据",
        phase="", note="", waiting=0, done_at=time.time(),
    )
    return job


def _nap_cancellable(job, seconds):
    end = time.time() + max(0.0, seconds)
    while time.time() < end and not job.get("cancelled"):
        time.sleep(0.15)


def run_xhs_account_job(job, target, tab="post", recent_days=0):
    """分页拉取小红书博主主页作品，增量缓存并可随时停止。"""
    cfg = config.load_config()
    raw_cookie = (cfg.get("xhs_cookie") or "").strip()
    _, _, has_session = _ensure_cookies(raw_cookie)
    user_id = target["id"]
    sec_uid = f"xhs_{user_id}"

    if not has_session:
        msg = "小红书博主主页需要登录态：请先在右上角「设置」中填入小红书 Cookie（需含 web_session）"
        state.remember_parse(job["id"], [])
        job.update(
            running=False, status="error", message=msg, reason=msg,
            phase="", note="", waiting=0, done_at=time.time(),
        )
        return job

    cached, cache_complete = douk_api.load_account_cache(sec_uid, tab)
    known = {x["id"]: x for x in cached if isinstance(x, dict) and x.get("id")}
    items = list(known.values())
    nickname = next((x.get("nickname") for x in items if x.get("nickname")), "")
    if items:
        job["cached"] = len(items)
        job["count"] = len(items)
        job["nickname"] = nickname

    cutoff_ts = 0
    if recent_days:
        cutoff_ts = int((datetime.now() - timedelta(days=recent_days)).timestamp())

    def finish(reason, complete=False):
        items.sort(key=lambda x: x.get("create_timestamp") or 0, reverse=True)
        out = [x for x in items if (x.get("create_timestamp") or 0) >= cutoff_ts] if cutoff_ts else items
        if items:
            douk_api.save_account_cache(sec_uid, tab, nickname, items, complete=complete)
            try:
                douk_api.remember_creator(items, tab=tab, full=complete)
            except Exception:
                pass
        state.remember_parse(job["id"], out)
        job.update(
            running=False,
            status="done" if out else "error",
            count=len(out),
            nickname=nickname,
            reason=reason,
            message="" if out else reason,
            phase="", note="", waiting=0, done_at=time.time(),
        )
        return job

    cursor = ""
    page = 0
    reason = ""
    complete = False
    xsec_token = target.get("xsec_token") or ""
    xsec_source = target.get("xsec_source") or "pc_user"

    while not job.get("cancelled"):
        page += 1
        job["pages"] = page
        job["phase"] = f"第 {page} 页：正在拉取小红书博主笔记列表…"
        params = {
            "num": "30",
            "cursor": cursor,
            "user_id": user_id,
            "image_scenes": "FD_WM_WEBP",
        }
        try:
            resp = _signed_request("GET", "/api/sns/web/v1/user_posted", params=params, raw_cookie=raw_cookie)
        except Exception as exc:
            reason = f"拉取第 {page} 页失败：{exc}"
            break

        if not resp.get("success"):
            reason = _api_error_message(resp)
            break

        data = resp.get("data") or {}
        notes = data.get("notes") or []
        if not notes:
            reason = "没有更多作品"
            complete = True
            break

        new_notes = []
        hit_cached = False
        for card in notes:
            if not isinstance(card, dict):
                continue
            nid = str(card.get("note_id") or "").strip()
            if not nid:
                continue
            if not nickname:
                u = card.get("user") if isinstance(card.get("user"), dict) else {}
                nickname = str(u.get("nickname") or u.get("nick_name") or "").strip()
                if nickname:
                    job["nickname"] = nickname
            if nid in known:
                hit_cached = True
                continue
            new_notes.append(card)

        fetched_this_page = 0
        reached_cutoff = False
        for idx, card in enumerate(new_notes, 1):
            if job.get("cancelled"):
                reason = "已停止解析"
                break
            nid = str(card.get("note_id") or "").strip()
            ntoken = str(card.get("xsec_token") or xsec_token or "")
            job["phase"] = f"第 {page} 页：正在解析作品详情 {idx}/{len(new_notes)}…"
            try:
                it = fetch_note_detail(
                    nid, xsec_token=ntoken, xsec_source="pc_user", raw_cookie=raw_cookie,
                )
            except Exception as exc:
                config.get_logger().warning("小红书笔记 %s 详情失败: %s", nid, exc)
                it = None

            if it and it["id"]:
                if not nickname and it["nickname"]:
                    nickname = it["nickname"]
                    job["nickname"] = nickname
                known[it["id"]] = it
                items.append(it)
                fetched_this_page += 1
                job["count"] = len(items)
                job["note"] = f"已获取 {len(items)} 篇：{it['desc'][:24] or it['id']}"
                if fetched_this_page % 10 == 0:
                    douk_api.save_account_cache(sec_uid, tab, nickname, items, complete=False)
                is_sticky = bool((card.get("interact_info") or {}).get("sticky"))
                if cutoff_ts and not is_sticky and it["create_timestamp"] and it["create_timestamp"] < cutoff_ts:
                    reached_cutoff = True
            _nap_cancellable(job, 0.25)

        if job.get("cancelled"):
            reason = "已停止解析"
            break

        if reached_cutoff:
            reason = f"已取完最近 {recent_days} 天的作品"
            break

        if hit_cached and cache_complete and fetched_this_page == 0:
            reason = "已经翻到上次的位置"
            complete = True
            break

        next_cursor = str(data.get("cursor") or "")
        has_more = bool(data.get("has_more"))
        if not has_more or not next_cursor or next_cursor == cursor:
            reason = "没有更多作品"
            complete = True
            break
        cursor = next_cursor
        _nap_cancellable(job, 0.35)

    if job.get("cancelled") and not reason:
        reason = "已停止解析"
    return finish(reason or "解析结束", complete=complete)


HOMEFEED_CATEGORIES = {
    "homefeed_recommend",
    "homefeed.fashion_v3",
    "homefeed.food_v3",
    "homefeed.cosmetics_v3",
    "homefeed.movie_and_tv_v3",
    "homefeed.career_v3",
    "homefeed.love_v3",
    "homefeed.household_product_v3",
    "homefeed.gaming_v3",
    "homefeed.travel_v3",
    "homefeed.fitness_v3",
}


def fetch_homefeed(category="homefeed_recommend", cursor_score="", refresh_type=1, note_index=0):
    """获取小红书网页版主页推荐流。"""
    cfg = config.load_config()
    raw_cookie = (cfg.get("xhs_cookie") or "").strip()
    _, _, has_session = _ensure_cookies(raw_cookie)
    if not has_session:
        return {
            "ok": False,
            "message": "请先在右上角「设置」中填入小红书 Cookie（需含 web_session）",
            "items": [],
        }

    cat = category if category in HOMEFEED_CATEGORIES else "homefeed_recommend"
    payload = {
        "cursor_score": str(cursor_score or ""),
        "num": 20,
        "refresh_type": int(refresh_type or 1),
        "note_index": int(note_index or 0),
        "unread_begin_note_id": "",
        "unread_end_note_id": "",
        "unread_note_count": 0,
        "category": cat,
        "search_key": "",
        "need_num": 20,
        "image_formats": ["jpg", "webp", "avif"],
    }
    try:
        resp = _signed_request("POST", "/api/sns/web/v1/homefeed", payload=payload, raw_cookie=raw_cookie, x_rap=True)
    except Exception as exc:
        return {"ok": False, "message": f"获取小红书推荐失败：{exc}", "items": []}

    if not resp.get("success"):
        return {"ok": False, "message": _api_error_message(resp), "items": []}

    data = resp.get("data") or {}
    out = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("model_type") and raw.get("model_type") != "note":
            continue
        nc = raw.get("note_card") or {}
        if not isinstance(nc, dict) or not nc:
            continue
        nid = str(raw.get("id") or nc.get("note_id") or "").strip()
        if not nid:
            continue
        token = str(raw.get("xsec_token") or "").strip()
        user = nc.get("user") if isinstance(nc.get("user"), dict) else {}
        uid = str(user.get("user_id") or "").strip()
        utoken = str(user.get("xsec_token") or "").strip()
        nickname = str(user.get("nickname") or user.get("nick_name") or "").strip()
        avatar = _format_cdn_url(user.get("avatar") or "")

        cov = nc.get("cover") if isinstance(nc.get("cover"), dict) else {}
        cover = _format_cdn_url(cov.get("url_default") or cov.get("url_pre") or "")
        if not cover:
            info_list = cov.get("info_list") or []
            if info_list and isinstance(info_list[0], dict):
                cover = _format_cdn_url(info_list[0].get("url") or "")

        interact = nc.get("interact_info") if isinstance(nc.get("interact_info"), dict) else {}
        liked = str(interact.get("liked_count") or "0")
        note_type = "视频" if str(nc.get("type") or "").lower() == "video" else "图文"
        title = str(nc.get("display_title") or "").strip()

        user_url = f"{XHS_WEB}/user/profile/{uid}" + (f"?xsec_token={urllib.parse.quote(utoken)}" if utoken else "")
        note_url = f"{XHS_WEB}/explore/{nid}" + (f"?xsec_token={urllib.parse.quote(token)}" if token else "")
        out.append({
            "platform": "xhs",
            "id": nid,
            "xsec_token": token,
            "type": note_type,
            "title": title,
            "cover": cover,
            "liked_count": liked,
            "user_id": uid,
            "nickname": nickname,
            "avatar": avatar,
            "user_url": user_url,
            "note_url": note_url,
        })

    return {
        "ok": True,
        "items": out,
        "cursor_score": str(data.get("cursor_score") or ""),
        "has_more": bool(out),
    }


def fetch_user_notes_page(user_id, cursor=""):
    """快速拉取小红书博主主页一页笔记预览（不逐篇拉详情，供发现页浏览）。"""
    cfg = config.load_config()
    raw_cookie = (cfg.get("xhs_cookie") or "").strip()
    _, _, has_session = _ensure_cookies(raw_cookie)
    if not has_session:
        return {
            "ok": False,
            "message": "小红书博主主页需要登录态：请先在右上角「设置 → 小红书 Cookie」中填入 Cookie",
            "items": [],
        }

    params = {
        "num": "30",
        "cursor": str(cursor or ""),
        "user_id": str(user_id or "").strip(),
        "image_scenes": "FD_WM_WEBP",
    }
    try:
        resp = _signed_request("GET", "/api/sns/web/v1/user_posted", params=params, raw_cookie=raw_cookie)
    except Exception as exc:
        return {"ok": False, "message": f"获取博主主页失败：{exc}", "items": []}

    if not resp.get("success"):
        return {"ok": False, "message": _api_error_message(resp), "items": []}

    data = resp.get("data") or {}
    notes = data.get("notes") or []
    out = []
    nickname = ""
    avatar = ""
    for card in notes:
        if not isinstance(card, dict):
            continue
        nid = str(card.get("note_id") or "").strip()
        if not nid:
            continue
        token = str(card.get("xsec_token") or "").strip()
        user = card.get("user") if isinstance(card.get("user"), dict) else {}
        uid = str(user.get("user_id") or user_id or "").strip()
        nick = str(user.get("nickname") or user.get("nick_name") or "").strip()
        av = _format_cdn_url(user.get("avatar") or "")
        if nick and not nickname:
            nickname = nick
        if av and not avatar:
            avatar = av

        cov = card.get("cover") if isinstance(card.get("cover"), dict) else {}
        cover = _format_cdn_url(cov.get("url_default") or cov.get("url_pre") or cov.get("url") or "")
        if not cover:
            info_list = cov.get("info_list") or []
            if info_list and isinstance(info_list[0], dict):
                cover = _format_cdn_url(info_list[0].get("url") or "")

        interact = card.get("interact_info") if isinstance(card.get("interact_info"), dict) else {}
        liked = str(interact.get("liked_count") or "0")
        note_type = "视频" if str(card.get("type") or "").lower() == "video" else "图文"
        title = str(card.get("display_title") or "").strip()
        note_url = f"{XHS_WEB}/explore/{nid}" + (f"?xsec_token={urllib.parse.quote(token)}" if token else "")
        out.append({
            "platform": "xhs",
            "id": nid,
            "xsec_token": token,
            "type": note_type,
            "title": title,
            "cover": cover,
            "liked_count": liked,
            "user_id": uid,
            "nickname": nick or nickname,
            "avatar": av or avatar,
            "user_url": f"{XHS_WEB}/user/profile/{uid}",
            "note_url": note_url,
        })

    return {
        "ok": True,
        "items": out,
        "cursor": str(data.get("cursor") or ""),
        "has_more": bool(data.get("has_more")),
        "nickname": nickname,
        "avatar": avatar,
        "user_id": str(user_id or "").strip(),
        "user_url": f"{XHS_WEB}/user/profile/{str(user_id or '').strip()}",
    }
