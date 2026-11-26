# -*- coding: utf-8 -*-
"""Douyin 解析引擎（基于 f2）、链接识别、主页分页解析、账号缓存与博主名单。

仅本模块允许直接依赖 f2，上层业务模块只通过本模块暴露的纯标准数据结构交互。
"""

import asyncio
import hashlib
import json
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta

from f2.apps.douyin.crawler import DouyinCrawler
from f2.apps.douyin.model import PostDetail, UserLike, UserPost
from f2.apps.douyin.utils import ClientConfManager
from f2.exceptions import (
    APIConnectionError,
    APIError,
    APINotFoundError,
    APIRateLimitError,
    APIResponseError,
    APIRetryExhaustedError,
    APITimeoutError,
    APIUnauthorizedError,
    APIUnavailableError,
)

from douk_lite import config, state


class DouyinApiError(Exception):
    """面向用户的清晰抖音接口异常。"""


class ParseCancelled(Exception):
    """用户按了「停止解析」。单独一类，别让兜底逻辑把它当成网络失败吞掉。"""


def _f2_kwargs(timeout=20):
    cookie = config.cookie_header()
    headers = dict(ClientConfManager.headers())
    return {
        "headers": headers,
        "proxies": {"http://": None, "https://": None},
        "cookie": cookie,
        "timeout": timeout,
    }


def _run_async(coro):
    return asyncio.run(coro)


def _translate_f2_error(exc):
    """把 f2 异常和 HTTP 状态码转成明确的用户提示。"""
    if isinstance(exc, DouyinApiError):
        return exc
    msg = str(exc or "").strip()
    low = msg.lower()
    if isinstance(exc, APIUnauthorizedError) or "401" in low or "403" in low or "unauthorized" in low:
        return DouyinApiError("抖音 Cookie 已失效或权限不足，请在设置中更新 Cookie")
    if isinstance(exc, APIRateLimitError) or "429" in low or "rate limit" in low:
        return DouyinApiError("请求过于频繁，已被抖音限流，请稍后再试")
    if isinstance(exc, APINotFoundError) or "404" in low:
        return DouyinApiError("作品或博主不存在、已删除或设为私密")
    if isinstance(exc, (APITimeoutError, APIConnectionError, APIUnavailableError, APIRetryExhaustedError)):
        return DouyinApiError(f"网络连接抖音失败或超时，请检查网络后重试（{msg[:120]}）")
    if isinstance(exc, APIResponseError):
        if "status code: 403" in low or "status code: 401" in low:
            return DouyinApiError("抖音 Cookie 已失效或触发风控，请更新 Cookie 后重试")
        if "status code: 429" in low:
            return DouyinApiError("请求过于频繁，已被抖音限流，请稍后再试")
        if "fetch_one_video" in low or "动图" in msg:
            return DouyinApiError("作品不存在、已删除或设为私密")
        return DouyinApiError(f"抖音接口返回异常：{msg[:160]}")
    if isinstance(exc, APIError):
        return DouyinApiError(f"抖音接口调用失败：{msg[:160]}")
    if "timed out" in low or "timeout" in low or "connect" in low:
        return DouyinApiError(f"网络连接抖音超时或失败：{msg[:120]}")
    return DouyinApiError(msg[:200] or "抖音解析失败")


def health_check():
    """检查内置 f2 解析引擎与 Cookie 状态。"""
    cookie = config.cookie_header()
    return {
        "ok": True,
        "engine": "f2",
        "cookie_set": bool(cookie),
        "build": config.BUILD,
    }


def patch_app_settings(patch):
    """直接改写 Volume/settings.json。"""
    data = config.read_app_settings()
    if not isinstance(data, dict):
        data = {}
    data.update(patch)
    try:
        config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        with config._json_cache_lock:
            config._json_cache.pop(str(config.SETTINGS_FILE), None)
        return True
    except Exception as exc:
        config.get_logger().warning("写入 settings.json 失败: %s", exc)
        return False


def _run_cancellable(job, fn, *args, **kwargs):
    """在后台线程里跑同步任务，每 0.25 秒检查一次取消标记。"""
    box = {}

    def worker():
        try:
            box["res"] = fn(*args, **kwargs)
        except BaseException as exc:
            box["exc"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    started = time.time()
    try:
        while t.is_alive():
            if job.get("cancelled"):
                raise ParseCancelled()
            job["waiting"] = int(time.time() - started)
            t.join(0.25)
    finally:
        job["waiting"] = 0
    if "exc" in box:
        raise box["exc"]
    return box.get("res")


def _nap(job, seconds):
    """可打断的等待：用户点「停止解析」就立刻返回，不用干等完。"""
    end = time.time() + max(0.0, seconds)
    while time.time() < end and not job.get("cancelled"):
        time.sleep(0.25)


def resolve_redirect(url, timeout=15):
    """把 v.douyin.com 短链跟到真实地址。"""
    req = urllib.request.Request(url, headers={"User-Agent": config.UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:
        return ""


URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
USER_RE = re.compile(r"douyin\.com/(?:share/)?user/([A-Za-z0-9_\-]+)")
USER_RE2 = re.compile(r"[?&]sec_uid=([A-Za-z0-9_\-]+)")
WORK_RE = re.compile(r"douyin\.com/(?:share/)?(?:video|note|slides)/(\d+)")
WORK_RE2 = re.compile(r"modal_id=(\d+)")


def extract_url(text):
    """从「长按复制此条消息，打开抖音搜索… https://v.douyin.com/xxx/」这种分享文案里抠出网址。"""
    urls = [u.rstrip(".,;:!?'\")]") for u in URL_RE.findall(text or "")]
    urls = [u for u in urls if u]
    for u in urls:
        if "douyin.com" in u:
            return u
    return urls[0] if urls else ""


def parse_target(text, _depth=0):
    text = (text or "").strip()
    if not text:
        return None

    if _depth == 0:
        from douk_lite import xhs_api
        xhs_target = xhs_api.parse_xhs_target(text)
        if xhs_target:
            return xhs_target

    url = extract_url(text)

    if not url:
        if re.fullmatch(r"\d{6,}", text):
            return {"platform": "douyin", "kind": "work", "id": text, "url": ""}
        if re.fullmatch(r"MS4wLjABAAAA[\w\-]+", text):
            return {"platform": "douyin", "kind": "user", "id": text, "url": ""}
        return None

    m = WORK_RE2.search(url)
    if m:
        return {"platform": "douyin", "kind": "work", "id": m.group(1), "url": url}

    m = WORK_RE.search(url)
    if m:
        return {"platform": "douyin", "kind": "work", "id": m.group(1), "url": url}

    m = USER_RE.search(url)
    if m and m.group(1) != "self":
        return {"platform": "douyin", "kind": "user", "id": m.group(1), "url": url}

    m = USER_RE2.search(url)
    if m:
        return {"platform": "douyin", "kind": "user", "id": m.group(1), "url": url}

    if _depth < 3 and ("v.douyin.com" in url or "iesdouyin.com/share" in url):
        real = resolve_redirect(url)
        if real and real != url:
            return parse_target(real, _depth + 1)
    return None


def _format_duration_ms(ms):
    try:
        sec = max(0, int(ms or 0) // 1000)
    except (TypeError, ValueError):
        return ""
    if not sec:
        return ""
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _pick_video_url(video):
    if not isinstance(video, dict):
        return ""
    bit_rates = video.get("bit_rate")
    if isinstance(bit_rates, list) and bit_rates:
        ordered = sorted(
            (b for b in bit_rates if isinstance(b, dict)),
            key=lambda b: int(b.get("bit_rate") or 0),
            reverse=True,
        )
        for b in ordered:
            urls = (b.get("play_addr") or {}).get("url_list") or []
            for u in urls:
                if isinstance(u, str) and u.startswith("http"):
                    return u
    play_urls = (video.get("play_addr") or {}).get("url_list") or []
    for u in play_urls:
        if isinstance(u, str) and u.startswith("http"):
            return u
    return ""


def _pick_cover_url(aweme, images):
    video = aweme.get("video") or {}
    for key in ("origin_cover", "cover", "dynamic_cover"):
        urls = (video.get(key) or {}).get("url_list") or []
        for u in urls:
            if isinstance(u, str) and u.startswith("http"):
                return u
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict):
                for u in (img.get("url_list") or []):
                    if isinstance(u, str) and u.startswith("http"):
                        return u
    return ""


def _raw_aweme_to_dict(aweme):
    """把抖音原始 aweme 字典转成 DouK-Lite 标准未归一化字典。"""
    if not isinstance(aweme, dict):
        return {}
    author = aweme.get("author") or {}
    video = aweme.get("video") or {}
    images = aweme.get("images") or []
    ts = int(aweme.get("create_time") or 0)
    if ts > 10 ** 11:
        ts = ts // 1000
    create_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else ""

    downloads = []
    if isinstance(images, list) and images:
        img_urls = []
        live_urls = []
        for img in images:
            if not isinstance(img, dict):
                continue
            u_list = img.get("url_list") or []
            img_u = next((u for u in u_list if isinstance(u, str) and u.startswith("http")), "")
            if img_u:
                img_urls.append(img_u)
            live_v = img.get("video")
            if isinstance(live_v, dict):
                lv_urls = (live_v.get("play_addr") or {}).get("url_list") or []
                lv_u = next((u for u in lv_urls if isinstance(u, str) and u.startswith("http")), "")
                if lv_u:
                    live_urls.append(lv_u)
        if live_urls:
            item_type = "实况"
            downloads = live_urls + img_urls
        else:
            item_type = "图集"
            downloads = img_urls
    else:
        item_type = "视频"
        v_url = _pick_video_url(video)
        if v_url:
            downloads = [v_url]

    desc_text = str(aweme.get("desc") or "").strip() or str(aweme.get("aweme_id") or "")
    return {
        "id": str(aweme.get("aweme_id") or ""),
        "type": item_type,
        "desc": desc_text,
        "create_time": create_str,
        "create_timestamp": ts,
        "nickname": str(author.get("nickname") or ""),
        "uid": str(author.get("uid") or ""),
        "sec_uid": str(author.get("sec_uid") or ""),
        "duration": _format_duration_ms(video.get("duration")),
        "static_cover": _pick_cover_url(aweme, images),
        "is_top": 1 if aweme.get("is_top") else 0,
        "downloads": downloads,
    }


def normalize_item(item):
    if isinstance(item, dict) and "aweme_id" in item and "downloads" not in item:
        item = _raw_aweme_to_dict(item)
    downloads = item.get("downloads")
    if isinstance(downloads, str):
        downloads = [downloads]
    if not downloads:
        downloads = []
    return {
        "id": item.get("id") or "",
        "type": item.get("type") or "视频",
        "desc": item.get("desc") or "",
        "create_time": item.get("create_time") or "",
        "create_timestamp": item.get("create_timestamp") or 0,
        "nickname": item.get("nickname") or "",
        "uid": item.get("uid") or "",
        "sec_uid": item.get("sec_uid") or "",
        "duration": item.get("duration") or "",
        "cover": item.get("static_cover") or item.get("dynamic_cover") or item.get("cover") or "",
        "is_top": 1 if str(item.get("is_top") or "0") == "1" else 0,
        "downloads": [u for u in downloads if isinstance(u, str) and u.startswith("http")],
    }


def _check_douyin_status(resp, empty_msg=""):
    if not isinstance(resp, dict) or not resp:
        raise DouyinApiError(empty_msg or "抖音接口未返回数据（可能 Cookie 已失效或网络异常）")
    status_code = resp.get("status_code")
    if status_code not in (None, 0):
        status_msg = str(resp.get("status_msg") or "").strip()
        if status_code in (8, 2154, 10002):
            raise DouyinApiError(f"抖音 Cookie 已失效或需要登录验证（{status_msg or status_code}）")
        raise DouyinApiError(f"抖音接口错误（code={status_code}）：{status_msg or '请稍后重试'}")


def fetch_work_detail(work_id):
    """通过 f2 拉取单个抖音作品并转成 DouK-Lite 标准作品字典。"""
    wid = str(work_id or "").strip()
    if not wid:
        raise DouyinApiError("缺少作品 ID")

    async def _go():
        async with DouyinCrawler(_f2_kwargs()) as crawler:
            return await crawler.fetch_post_detail(PostDetail(aweme_id=wid))

    try:
        resp = _run_async(_go())
    except Exception as exc:
        raise _translate_f2_error(exc) from exc

    _check_douyin_status(resp, "作品不存在或请求被拦截")
    detail = resp.get("aweme_detail")
    if not isinstance(detail, dict) or not detail.get("aweme_id"):
        raise DouyinApiError("作品不存在、已删除或设为私密")
    item = normalize_item(_raw_aweme_to_dict(detail))
    if not item["id"]:
        raise DouyinApiError("作品解析结果缺少 ID")
    return item


def fetch_account_page(sec_uid, tab="post", max_cursor=0, count=20):
    """通过 f2 拉取单页博主发布或喜欢作品，返回 (items, has_more, next_cursor)。"""
    sec = str(sec_uid or "").strip()
    if not sec:
        raise DouyinApiError("缺少博主 sec_uid")

    async def _go():
        async with DouyinCrawler(_f2_kwargs()) as crawler:
            if tab == "favorite":
                return await crawler.fetch_user_like(
                    UserLike(max_cursor=int(max_cursor or 0), count=int(count), sec_user_id=sec)
                )
            return await crawler.fetch_user_post(
                UserPost(max_cursor=int(max_cursor or 0), count=int(count), sec_user_id=sec)
            )

    try:
        resp = _run_async(_go())
    except Exception as exc:
        raise _translate_f2_error(exc) from exc

    _check_douyin_status(resp, "博主主页未返回数据（可能 Cookie 已失效或被限流）")
    raw_list = resp.get("aweme_list") or []
    items = [normalize_item(_raw_aweme_to_dict(x)) for x in raw_list if isinstance(x, dict)]
    items = [it for it in items if it["id"]]
    has_more = bool(resp.get("has_more"))
    next_cursor = int(resp.get("max_cursor") or 0)
    return items, has_more, next_cursor


def fetch_account_works(sec_uid, tab="post", count=20):
    """供 /api/works 同步拉取单页博主作品。"""
    items, _has_more, _next_cursor = fetch_account_page(sec_uid, tab=tab, max_cursor=0, count=count)
    return items


PARSE_PAGE_TIMEOUT = 60
PARSE_CHUNK_TIMEOUT = 240
PARSE_CHUNK_FAILS = 4
PARSE_MAX_REQUESTS = 500
PARSE_PAGE_SIZE = 20
PINNED_GAP = 30 * 86400


def account_cache_file(sec_uid, tab):
    key = hashlib.sha1(f"{sec_uid}|{tab}".encode("utf-8")).hexdigest()[:16]
    return config.ACCOUNT_CACHE / f"{key}.json"


def load_account_cache(sec_uid, tab):
    """返回 (作品列表, 上次是否已经翻到底)。"""
    try:
        data = json.loads(account_cache_file(sec_uid, tab).read_text(encoding="utf-8"))
        items = data.get("items")
        return (items if isinstance(items, list) else []), bool(data.get("complete"))
    except Exception:
        return [], False


def save_account_cache(sec_uid, tab, nickname, items, complete=False):
    try:
        config.ACCOUNT_CACHE.mkdir(parents=True, exist_ok=True)
        path = account_cache_file(sec_uid, tab)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "sec_uid": sec_uid, "tab": tab, "nickname": nickname,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "complete": bool(complete),
            "count": len(items), "items": items,
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        config.get_logger().warning("保存账号缓存失败 %s: %s", sec_uid, exc)


def load_creators():
    try:
        data = json.loads(config.CREATORS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_creators(creators):
    try:
        config.CREATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = config.CREATORS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(creators, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(config.CREATORS_FILE)
    except Exception as exc:
        config.get_logger().warning("保存博主名单失败: %s", exc)


def remember_creator(items, tab="", downloaded=False, full=True):
    """从一组作品里认出博主并写进名单。"""
    if not isinstance(items, list) or not items:
        return None
    sec = next((str(x.get("sec_uid") or "").strip() for x in items if x.get("sec_uid")), "")
    if not sec:
        return None
    ordered = sorted(items, key=lambda x: x.get("create_timestamp") or 0, reverse=True)
    nickname = next((x.get("nickname") for x in ordered if x.get("nickname")), "")
    uid = next((str(x.get("uid")) for x in ordered if x.get("uid")), "")
    cover = next((x.get("cover") for x in ordered if x.get("cover")), "")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with state._creators_lock:
        creators = load_creators()
        entry = next((c for c in creators if c.get("sec_uid") == sec), None)
        if entry is None:
            entry = {"sec_uid": sec, "first_seen": now, "downloads": 0}
            creators.append(entry)
        if nickname:
            entry["nickname"] = nickname
        if uid:
            entry["uid"] = uid
        if cover:
            entry["cover"] = cover
        if sec.startswith("xhs_"):
            entry["platform"] = "xhs"
            entry["url"] = "https://www.xiaohongshu.com/user/profile/" + sec[4:]
        else:
            entry["platform"] = "douyin"
            entry["url"] = "https://www.douyin.com/user/" + sec
        try:
            tpl = config.load_config().get("folder_template") or ""
            if "{date}" not in tpl:
                entry["folder"] = config.render_folder_name(ordered[0], tpl)
        except Exception:
            pass
        entry["last_active"] = now
        entry["last_parsed_at"] = now
        if downloaded:
            entry["last_download_at"] = now
            entry["downloads"] = int(entry.get("downloads") or 0) + len(items)
        if tab in ("post", "favorite"):
            entry["last_tab"] = tab
        elif not entry.get("last_tab"):
            entry["last_tab"] = "post"
        if full or "last_count" not in entry:
            entry["last_count"] = len(items)
        save_creators(creators)
    return entry


def _item_ts(it):
    try:
        ts = int(it.get("create_timestamp") or 0)
        return ts // 1000 if ts > 10 ** 11 else ts
    except (TypeError, ValueError):
        return 0


def run_account_job(job, sec_uid, tab, recent_days=0):
    jid = job["id"]
    cached, cache_complete = load_account_cache(sec_uid, tab)
    known, items = {}, []
    for it in cached:
        tid = it.get("id")
        if tid and tid not in known:
            known[tid] = it
            items.append(it)
    nickname = next((x.get("nickname") for x in items if x.get("nickname")), "") or ""
    job["cached"] = len(items)
    job["count"] = len(items)
    job["nickname"] = nickname

    def absorb(page_items):
        fresh = 0
        for it in page_items:
            tid = it.get("id")
            if not tid:
                continue
            if tid in known:
                old = known[tid]
                if it.get("downloads"):
                    old["downloads"] = it["downloads"]
                if it.get("cover"):
                    old["cover"] = it["cover"]
                if it.get("type"):
                    old["type"] = it["type"]
                if it.get("desc"):
                    old["desc"] = it["desc"]
                if it.get("nickname"):
                    old["nickname"] = it["nickname"]
                continue
            known[tid] = it
            items.append(it)
            fresh += 1
        nonlocal nickname
        if not nickname:
            nickname = next((x["nickname"] for x in items if x["nickname"]), "")
        job["count"] = len(items)
        job["nickname"] = nickname
        return fresh

    def finish(reason, complete):
        items.sort(key=lambda x: x.get("create_timestamp") or 0, reverse=True)
        try:
            remember_creator(items, tab, full=bool(complete))
        except Exception:
            pass
        save_account_cache(sec_uid, tab, nickname, items, complete)
        state.remember_parse(jid, items)
        job["count"] = len(items)
        job["nickname"] = nickname
        job["reason"] = reason
        job["phase"] = ""
        job["note"] = ""
        job["waiting"] = 0
        job["running"] = False
        job["status"] = "done" if items else "error"
        job["message"] = reason if (reason and not items) else (reason or "解析完成")
        job["done_at"] = time.time()
        return job

    cutoff_ts = 0
    if recent_days > 0:
        days = max(1, min(int(recent_days), 365))
        cutoff_ts = int((datetime.now() - timedelta(days=days)).timestamp())
        job["phase"] = "快速解析"
        job["note"] = f"快速模式只取最近 {days} 天，更早的作品这次不列；缓存里的旧作品照样保留"
    elif cached and cache_complete:
        newest_ts = max((_item_ts(it) for it in items), default=0)
        cutoff_ts = max(0, newest_ts - 7 * 86400) if newest_ts else 0
        job["phase"] = "补最新"
        job["note"] = "检查主页有没有新作品"
    else:
        job["phase"] = "分页获取"
        job["note"] = "使用 f2 引擎逐页获取并落盘；随时可以停止，已取到的会保留"

    cursor = 0
    requests = 0
    fails = 0
    stall = 0
    reason = ""
    total_fresh = 0
    reached_end = False

    while not job["cancelled"] and requests < PARSE_MAX_REQUESTS:
        requests += 1
        job["pages"] = requests
        try:
            page_items, has_more, next_cursor = _run_cancellable(
                job, fetch_account_page, sec_uid, tab, cursor, PARSE_PAGE_SIZE,
            )
        except ParseCancelled:
            reason = "已停止解析"
            break
        except Exception as exc:
            fails += 1
            err_msg = str(exc)
            if "Cookie" in err_msg or "限流" in err_msg or fails >= PARSE_CHUNK_FAILS:
                reason = (
                    f"{err_msg}（已保留 {len(items)} 个）" if items else err_msg
                )
                break
            job["note"] = f"第 {requests} 页请求失败（{fails}/{PARSE_CHUNK_FAILS}），重试中：{err_msg}"
            _nap(job, min(2 * fails, 6))
            continue

        fails = 0
        if not page_items:
            reached_end = not has_more or requests == 1
            reason = "没有更多作品" if items else "该主页暂无公开作品，或 Cookie 权限不足"
            break

        fresh = absorb(page_items)
        total_fresh += fresh
        save_account_cache(sec_uid, tab, nickname, items, complete=cache_complete)
        job["note"] = f"第 {requests} 页取回 {len(page_items)} 个，累计 {len(items)} 个"

        non_pinned_ts = [_item_ts(it) for it in page_items if not it.get("is_top") and _item_ts(it)]
        if cutoff_ts and non_pinned_ts and min(non_pinned_ts) < cutoff_ts:
            reached_end = True
            reason = (
                f"最近 {recent_days} 天取回 {len(items)} 个，新增 {total_fresh} 个"
                if recent_days > 0
                else ("已是最新" if not total_fresh else f"新增 {total_fresh} 个")
            )
            break

        if recent_days > 0 and tab == "favorite":
            reached_end = True
            reason = f"收藏第一页取回 {len(items)} 个，新增 {total_fresh} 个"
            break

        if cached and cache_complete and fresh == 0:
            stall += 1
            if stall >= 2:
                reached_end = True
                reason = "已是最新" if not total_fresh else f"新增 {total_fresh} 个"
                break
        else:
            stall = 0

        if not has_more or not next_cursor or next_cursor == cursor:
            reached_end = True
            reason = "没有更多作品"
            break

        cursor = next_cursor
        _nap(job, 0.35)

    if job["cancelled"]:
        reason = "已停止解析"
    if not reason and requests >= PARSE_MAX_REQUESTS:
        reason = f"已达 {PARSE_MAX_REQUESTS} 页上限，先停在这里"

    if recent_days > 0:
        final_complete = cache_complete
    elif cached and cache_complete:
        final_complete = True
    else:
        final_complete = reached_end and not job["cancelled"]
    return finish(reason or "解析完成", final_complete)


def run_work_job(job, work_id):
    try:
        it = _run_cancellable(job, fetch_work_detail, work_id)
    except ParseCancelled:
        state.remember_parse(job["id"], [])
        job.update(
            running=False, status="error", message="已停止解析", reason="已停止解析",
            phase="", note="", waiting=0, done_at=time.time(),
        )
        return job
    except Exception as exc:
        state.remember_parse(job["id"], [])
        job.update(running=False, status="error", message=f"取作品失败：{exc}", done_at=time.time())
        return job

    items = [it] if it and it.get("id") else []
    state.remember_parse(job["id"], items)
    if items:
        try:
            remember_creator(items, full=False)
        except Exception:
            pass
    job.update(
        running=False,
        status="done" if items else "error",
        count=len(items),
        nickname=items[0]["nickname"] if items else "",
        message="" if items else "没有取到作品",
        done_at=time.time(),
    )
    return job


def search_douyin_users(keyword, pages=1):
    """内置 f2 引擎暂不提供抖音关键词搜人接口，提示用户直接贴主页链接。"""
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "message": "请输入搜索关键词", "users": []}
    return {
        "ok": False,
        "message": "已切换至内置 f2 引擎，暂不支持关键词搜人；请直接复制抖音博主主页链接到右上角「+ 指定链接下载」",
        "users": [],
    }


def fetch_douyin_user_preview(sec_uid):
    """通过 f2 快速拉取抖音博主近期作品列表供发现页浏览（不触发下载）。"""
    sec = (sec_uid or "").strip()
    if not sec:
        return {"ok": False, "message": "缺少 sec_uid", "items": []}
    try:
        items, _has_more, _next_cursor = fetch_account_page(sec, tab="post", max_cursor=0, count=20)
    except Exception as exc:
        return {"ok": False, "message": f"获取抖音博主作品失败：{exc}", "items": []}
    items.sort(key=lambda x: x.get("create_timestamp") or 0, reverse=True)
    nickname = next((x["nickname"] for x in items if x["nickname"]), "")
    return {
        "ok": True,
        "items": items,
        "nickname": nickname,
        "sec_uid": sec,
        "user_url": f"https://www.douyin.com/user/{sec}",
        "message": "" if items else "该博主暂无公开作品",
    }


def fetch_douyin_beauty_feed(category="beauty", offset=0, keyword=""):
    """内置 f2 引擎暂不提供抖音关键词推荐流，提示用户使用小红书发现或直接贴链接。"""
    return {
        "ok": False,
        "items": [],
        "next_offset": int(offset or 0),
        "has_more": False,
        "query": (keyword or "").strip(),
        "message": "已切换至内置 f2 引擎，抖音关键词推荐暂不可用（可切到小红书发现页，或用右上角「+ 指定链接下载」贴入抖音链接）",
    }
