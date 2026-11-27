# -*- coding: utf-8 -*-
"""HTTP 服务、路由与进程入口。"""

import atexit
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from douk_lite import config, douk_api, downloader, gallery, media, state, xhs_api

_CLIENT_DISCONNECT = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


_PROXY_IMG_HOSTS = (
    "xhscdn.com", "xiaohongshu.com",
    "douyin.com", "iesdouyin.com", "douyinpic.com", "byteimg.com", "snssdk.com", "ibytedtos.com",
    "douyinvod.com", "bytecdn.cn", "bytedance.com", "amemv.com", "zjcdn.com", "byteorge.com",
)


def _is_allowed_proxy_img(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in _PROXY_IMG_HOSTS)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, _CLIENT_DISCONNECT):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "DouKLite/1.0"

    def log_message(self, fmt, *args):
        pass

    def _safe_write(self, data):
        try:
            self.wfile.write(data)
            return True
        except _CLIENT_DISCONNECT:
            return False
        except OSError as exc:
            if getattr(exc, "winerror", None) in (10053, 10054):
                return False
            raise

    def _is_local_origin(self):
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urllib.parse.urlparse(origin)
            return parsed.hostname in ("127.0.0.1", "localhost", "::1")
        except Exception:
            return False

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(body)
        except _CLIENT_DISCONNECT:
            pass

    def _events(self):
        """SSE 保持连接：页面开着就一直连着，窗口一关连接断掉，服务端据此退出。"""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except _CLIENT_DISCONNECT:
            return
        state.presence_connect()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                time.sleep(4)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            state.presence_disconnect()

    def _file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "application/javascript"
        elif path.suffix == ".css":
            ctype = "text/css"
        elif path.suffix == ".html":
            ctype = "text/html"
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._safe_write(data)
        except _CLIENT_DISCONNECT:
            pass

    def _bin(self, path, ctype):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self._safe_write(data)
        except _CLIENT_DISCONNECT:
            pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _save_frame_upload(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return self._json({"ok": False, "message": "没有收到图片数据"})
        if length > 64 * 1024 * 1024:
            return self._json({"ok": False, "message": "图片超过 64 MB，先换个方式抽帧"})
        try:
            data = self.rfile.read(length)
        except Exception:
            return self._json({"ok": False, "message": "读取图片数据失败，重试一次"})
        src = media.thumb_allowed((query.get("path") or [""])[0])
        if not src or src.suffix.lower() not in config.VIDEO_EXT:
            return self._json({"ok": False, "message": "这条视频不在了，或不在保存目录里"})
        root = config.resolve_root((query.get("root") or [""])[0])
        seconds = (query.get("seconds") or ["0"])[0]
        try:
            res = media.save_frame_png(src, root, data, seconds)
            if res.get("ok"):
                state.invalidate_library()
            return self._json(res)
        except Exception as exc:
            config.get_logger().warning("保存截帧失败 %s: %s", src, exc)
            return self._json({"ok": False, "message": f"保存失败：{str(exc)[:220]}"})

    def _serve_heic_preview(self, src):
        try:
            from PIL import Image, ImageOps
            import pillow_heif
            pillow_heif.register_heif_opener()
            img = Image.open(src)
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            data = buf.getvalue()
        except Exception:
            return self.send_error(415)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(src.stem + '.jpg')}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length, Content-Type")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self._safe_write(data)
        except _CLIENT_DISCONNECT:
            pass

    def _serve_media(self, src, preview=False):
        suffix = src.suffix.lower()
        if preview and suffix in (".heic", ".heif"):
            return self._serve_heic_preview(src)
        try:
            size = src.stat().st_size
        except OSError:
            return self.send_error(404)
        start, end, status = 0, max(0, size - 1), 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            start = max(0, start)
            end = min(end, size - 1)
            if start > end:
                try:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                except _CLIENT_DISCONNECT:
                    pass
                return
            status = 206

        length = max(0, end - start + 1) if size else 0
        ctype = mimetypes.guess_type(str(src))[0] or "application/octet-stream"
        if suffix == ".mov":
            ctype = "video/quicktime"
        if suffix in (".heic", ".heif"):
            ctype = "image/heic"

        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{urllib.parse.quote(src.name)}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length, Content-Type")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
        except _CLIENT_DISCONNECT:
            return
        if not length:
            return
        with open(src, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                if not self._safe_write(chunk):
                    break
                left -= len(chunk)

    # ---------- OPTIONS ----------
    def do_OPTIONS(self):
        state.cancel_shutdown()
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()
        except _CLIENT_DISCONNECT:
            pass

    # ---------- GET ----------
    def do_GET(self):
        state.cancel_shutdown()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/events":
            return self._events()

        if path in ("/", "/index.html"):
            return self._file(config.WEB_DIR / "index.html")
        if path == "/app.js":
            return self._file(config.WEB_DIR / "app.js")
        if path == "/style.css":
            return self._file(config.WEB_DIR / "style.css")
        if path == "/build.txt":
            return self._file(config.WEB_DIR / "build.txt")
        if path in ("/favicon.ico", "/icon.ico"):
            return self._bin(config.WEB_DIR / "icon.ico", "image/x-icon")
        if path in ("/icon.png", "/icon-192.png"):
            return self._bin(config.WEB_DIR / "icon.png", "image/png")
        if path in ("/icon-180.png", "/apple-touch-icon.png"):
            return self._bin(config.WEB_DIR / "icon-180.png", "image/png")

        if path == "/api/health":
            try:
                info = douk_api.health_check()
                return self._json(info)
            except Exception as exc:
                return self._json({
                    "ok": False, "engine": "f2", "build": config.BUILD,
                    "message": str(exc)[:200],
                })

        if path == "/api/settings":
            cfg = config.load_config()
            app = config.read_app_settings()
            return self._json({
                "config": cfg,
                "app": {
                    "original_quality": app.get("original_quality", False),
                    "cookie_set": bool(app.get("cookie")),
                    "root": app.get("root", ""),
                },
            })

        if path == "/api/job":
            jid = (query.get("id") or [""])[0]
            job = state.get_job_snapshot(jid)
            if not job:
                return self._json({"ok": False, "message": "任务不存在"}, 404)
            return self._json({"ok": True, "job": job})

        if path == "/api/works/items":
            jid = (query.get("id") or [""])[0]
            job = state.get_job_snapshot(jid)
            if not job:
                return self._json({"ok": False, "message": "任务不存在"}, 404)
            cfg = config.load_config()
            root = Path(cfg.get("root") or config.VOLUME_DIR)
            raw_items = state.get_parse_items(jid)
            items = [{**it, "exists": downloader.item_exists(root, it, cfg)} for it in raw_items]
            return self._json({
                "ok": True,
                "kind": job.get("target_kind"),
                "tab": job.get("tab"),
                "nickname": job.get("nickname") or "",
                "url": job.get("url") or "",
                "message": job.get("message") or "",
                "items": items,
            })

        if path == "/api/creators":
            return self._json({"ok": True, "creators": gallery.creators_payload()})

        if path == "/api/library":
            folder = (query.get("folder") or [""])[0]
            sec = (query.get("sec_uid") or [""])[0]
            cfg = config.load_config()
            root = Path(cfg.get("root") or config.VOLUME_DIR)
            entry = None
            all_entries = douk_api.load_creators()
            if sec:
                entry = next((c for c in all_entries if c.get("sec_uid") == sec), None)
            if entry is None and folder:
                entry = next((c for c in all_entries if Path(c.get("folder") or "").name == folder), None)
            info = gallery.creator_dir_info(
                root, entry=entry, folder_name=folder,
                refresh=(query.get("refresh") or [""])[0] == "1",
            )
            if not info:
                return self._json({
                    "ok": True, "exists": False, "folder": folder,
                    "nickname": (entry or {}).get("nickname") or "",
                    "remark": (entry or {}).get("remark") or "",
                    "posts": [], "files": 0, "bytes": 0,
                })
            return self._json({
                "ok": True, "exists": True, "folder": info["folder"],
                "path": info["path"],
                "nickname": (entry or {}).get("nickname") or info["nickname"],
                "remark": (entry or {}).get("remark") or "",
                "sec_uid": (entry or {}).get("sec_uid") or "",
                "url": (entry or {}).get("url") or "",
                "cover": (entry or {}).get("cover") or (
                    info["posts"][0]["cover"] if info["posts"] else ""
                ),
                "posts": info["posts"], "files": info["files"], "bytes": info["bytes"],
            })

        if path == "/api/gallery":
            return self._json(gallery.gallery_payload(
                folder=(query.get("folder") or [""])[0],
                post_type=(query.get("type") or [""])[0],
                q=(query.get("q") or [""])[0],
                sort=(query.get("sort") or ["newest"])[0],
                offset=(query.get("offset") or ["0"])[0],
                limit=(query.get("limit") or ["60"])[0],
                refresh=(query.get("refresh") or [""])[0] == "1",
            ))

        if path == "/api/livephoto/scan":
            root = config.resolve_root((query.get("root") or [""])[0])
            return self._json(livephoto_scan_payload(root))

        if path == "/api/frame/scan":
            root = config.resolve_root((query.get("root") or [""])[0])
            refresh = (query.get("refresh") or [""])[0] == "1"
            q = (query.get("q") or [""])[0].strip().casefold()
            kind = (query.get("kind") or ["all"])[0]
            try:
                limit = int((query.get("limit") or ["0"])[0])
            except (TypeError, ValueError):
                limit = 0
            limit = max(0, min(limit, 50000))
            items = media.scan_frame_videos(root, refresh=refresh)
            if kind == "live":
                items = [x for x in items if x["live"]]
            elif kind == "normal":
                items = [x for x in items if not x["live"]]
            if q:
                items = [
                    x for x in items
                    if q in x["name"].casefold() or q in x["rel"].casefold()
                ]
            total = len(items)
            return self._json({
                "ok": True, "root": str(root), "total": total,
                "items": items[:limit] if limit > 0 else items,
                "tools": {"ffmpeg": config.tool_path("ffmpeg", "ffmpeg.exe")},
            })

        if path == "/api/asset":
            src = media.thumb_allowed((query.get("path") or [""])[0])
            if not src:
                return self.send_error(404)
            preview = (query.get("preview") or [""])[0] == "1"
            return self._serve_media(src, preview=preview)

        if path == "/api/thumb":
            query_path = (query.get("path") or [""])[0]
            src = media.thumb_allowed(query_path) if query_path else None
            if not src:
                return self.send_error(404)
            try:
                thumb, _hit = media.make_thumb(src, (query.get("w") or ["160"])[0])
                data = thumb.read_bytes()
            except Exception:
                return self.send_error(404)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                self._safe_write(data)
            except _CLIENT_DISCONNECT:
                pass
            return

        if path == "/api/discover/xhs":
            cat = (query.get("category") or ["homefeed_recommend"])[0]
            cursor_score = (query.get("cursor_score") or [""])[0]
            try:
                refresh_type = int((query.get("refresh_type") or ["1"])[0])
            except ValueError:
                refresh_type = 1
            try:
                note_index = int((query.get("note_index") or ["0"])[0])
            except ValueError:
                note_index = 0
            return self._json(xhs_api.fetch_homefeed(cat, cursor_score, refresh_type, note_index))

        if path == "/api/discover/xhs/user":
            uid = (query.get("user_id") or [""])[0]
            cursor = (query.get("cursor") or [""])[0]
            sec = f"xhs_{uid}" if uid else ""
            return self._json(_mark_discover_exists(xhs_api.fetch_user_notes_page(uid, cursor), sec))

        if path == "/api/discover/xhs/note":
            nid = (query.get("id") or [""])[0]
            token = (query.get("xsec_token") or [""])[0]
            source = (query.get("xsec_source") or ["pc_feed"])[0]
            raw_cookie = (config.load_config().get("xhs_cookie") or "").strip()
            try:
                it = xhs_api.fetch_note_detail(nid, xsec_token=token, xsec_source=source, raw_cookie=raw_cookie)
                return self._json({"ok": bool(it), "item": it})
            except Exception as exc:
                return self._json({"ok": False, "message": str(exc)})

        if path == "/api/discover/douyin":
            kw = (query.get("keyword") or [""])[0]
            try:
                pages = int((query.get("pages") or ["1"])[0])
            except ValueError:
                pages = 1
            return self._json(douk_api.search_douyin_users(kw, pages))

        if path == "/api/discover/douyin/user":
            sec = (query.get("sec_uid") or [""])[0]
            return self._json(_mark_discover_exists(douk_api.fetch_douyin_user_preview(sec), sec))

        if path == "/api/discover/douyin/feed":
            cat = (query.get("category") or ["beauty"])[0]
            kw = (query.get("keyword") or [""])[0]
            try:
                offset = int((query.get("offset") or ["0"])[0])
            except ValueError:
                offset = 0
            return self._json(douk_api.fetch_douyin_beauty_feed(cat, offset, kw))

        if path == "/api/proxy/media":
            media_url = (query.get("url") or [""])[0]
            if not _is_allowed_proxy_img(media_url):
                return self.send_error(403)
            is_xhs = "xhscdn.com" in media_url or "xiaohongshu.com" in media_url
            headers = {
                "User-Agent": config.UA,
                "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
                "Accept": "*/*",
            }
            if not is_xhs:
                dy_cookie = config.cookie_header()
                if dy_cookie:
                    headers["Cookie"] = dy_cookie
            rng = self.headers.get("Range")
            if rng:
                headers["Range"] = rng
            try:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(media_url, headers=headers)
                with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                    status = resp.status
                    self.send_response(status)
                    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                        val = resp.headers.get(h)
                        if val:
                            self.send_header(h, val)
                    self.end_headers()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        if not self._safe_write(chunk):
                            break
            except urllib.error.HTTPError as exc:
                self.send_error(exc.code)
            except _CLIENT_DISCONNECT:
                pass
            except Exception:
                self.send_error(404)
            return

        if path == "/api/proxy/img":
            img_url = (query.get("url") or [""])[0]
            if not _is_allowed_proxy_img(img_url):
                return self.send_error(403)
            is_xhs = "xhscdn.com" in img_url or "xiaohongshu.com" in img_url
            headers = {
                "User-Agent": config.UA,
                "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            }
            try:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(img_url, headers=headers)
                with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                    data = resp.read()
                    ctype = resp.headers.get("Content-Type") or "image/jpeg"
            except Exception:
                return self.send_error(404)
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self._safe_write(data)
            except _CLIENT_DISCONNECT:
                pass
            return

        if path.startswith("/api/"):
            return self._json({"ok": False, "message": config.STALE_MSG}, 404)
        return self.send_error(404)

    # ---------- POST ----------
    def do_POST(self):
        state.cancel_shutdown()
        if not self._is_local_origin():
            return self._json({"ok": False, "message": "禁止跨站请求"}, 403)
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/frame/save":
            return self._save_frame_upload()
        body = self._body()

        try:
            if path == "/api/ping":
                return self._json({"ok": True, "build": config.BUILD})

            if path == "/api/shutdown":
                self._json({"ok": True})
                state.request_shutdown(8)
                return

            if path == "/api/parse":
                target = douk_api.parse_target(body.get("text", ""))
                if not target:
                    return self._json({"ok": False, "message": "没认出链接，请粘贴作品链接或主页链接"})
                return self._json({"ok": True, "target": target})

            if path == "/api/works/start":
                target = douk_api.parse_target(body.get("text", ""))
                if not target:
                    return self._json({"ok": False, "message": "没认出链接，请粘贴作品链接或主页链接"})
                tab = (body.get("tab") or "post").lower()
                if tab not in ("post", "favorite"):
                    tab = "post"
                try:
                    recent_days = int(body.get("recent_days") or 0)
                except (TypeError, ValueError):
                    recent_days = 0
                recent_days = max(0, min(recent_days, 365))
                jid = uuid.uuid4().hex
                job = {
                    "id": jid, "kind": "works", "target_kind": target["kind"],
                    "tab": tab, "url": target.get("url") or "", "nickname": "",
                    "count": 0, "cached": 0, "pages": 0, "running": True,
                    "cancelled": False, "status": "running", "message": "",
                    "reason": "", "phase": "", "note": "", "waiting": 0,
                    "recent_days": recent_days, "started_at": time.time(),
                }
                state.set_job(jid, job)
                if target.get("platform") == "xhs":
                    if target["kind"] == "work":
                        threading.Thread(
                            target=xhs_api.run_xhs_work_job, args=(job, target), daemon=True,
                        ).start()
                    else:
                        threading.Thread(
                            target=xhs_api.run_xhs_account_job,
                            args=(job, target, tab, recent_days), daemon=True,
                        ).start()
                elif target["kind"] == "work":
                    threading.Thread(
                        target=douk_api.run_work_job, args=(job, target["id"]), daemon=True,
                    ).start()
                else:
                    threading.Thread(
                        target=douk_api.run_account_job,
                        args=(job, target["id"], tab, recent_days), daemon=True,
                    ).start()
                return self._json({"ok": True, "job": jid, "target": target, "tab": tab})

            if path == "/api/works":
                target = douk_api.parse_target(body.get("text", ""))
                if not target:
                    return self._json({"ok": False, "message": "没认出链接"})
                tab = (body.get("tab") or "post").lower()
                if tab not in ("post", "favorite"):
                    tab = "post"
                if target["kind"] == "work":
                    try:
                        it = douk_api.fetch_work_detail(target["id"])
                        items = [it] if it else []
                        msg = ""
                    except Exception as exc:
                        items = []
                        msg = str(exc)
                    nickname = (items[0]["nickname"] if items else "")
                    return self._json({
                        "ok": bool(items), "kind": "work", "items": items,
                        "message": msg,
                        "nickname": nickname, "url": target.get("url") or "",
                    })
                try:
                    items = douk_api.fetch_account_works(target["id"], tab=tab, count=20)
                    msg = ""
                except Exception as exc:
                    items = []
                    msg = str(exc)
                items.sort(key=lambda x: x.get("create_timestamp") or 0, reverse=True)
                nickname = next((x["nickname"] for x in items if x["nickname"]), "")
                return self._json({
                    "ok": bool(items), "kind": "user", "items": items,
                    "message": msg,
                    "nickname": nickname, "tab": tab, "url": target.get("url") or "",
                })

            if path == "/api/download":
                items = body.get("items") or []
                if not items:
                    return self._json({"ok": False, "message": "没有要下载的作品"})
                cfg = config.load_config()
                if body.get("root"):
                    valid_root, err_msg = config.validate_root(body.get("root"))
                    if not valid_root:
                        return self._json({"ok": False, "message": err_msg})
                    cfg["root"] = valid_root
                cfg["live_folder"] = body.get("live_folder") or cfg["live_folder"]
                cfg["video_folder"] = body.get("video_folder") or cfg.get("video_folder") or "视频"
                if body.get("quality"):
                    cfg["quality"] = body["quality"]
                try:
                    douk_api.remember_creator(
                        items, (body.get("tab") or "").lower(),
                        downloaded=True, full=len(items) > 1,
                    )
                except Exception:
                    pass
                jid = uuid.uuid4().hex
                job = {
                    "id": jid,
                    "kind": "download",
                    "total": len(items),
                    "finished": 0,
                    "running": True,
                    "cancelled": False,
                    "started_at": time.time(),
                    "items": {
                        it["id"]: {
                            "id": it["id"], "desc": it.get("desc", "")[:60],
                            "type": it.get("type", ""), "status": "pending",
                            "message": "", "file": "",
                        } for it in items
                    },
                }
                state.set_job(jid, job)
                threading.Thread(target=downloader.run_download, args=(jid, items, cfg), daemon=True).start()
                return self._json({"ok": True, "job": jid})

            if path == "/api/apply-quality":
                high = bool(body.get("high", True))
                patch = {"original_quality": high, "live_qualities": ""}
                ok = douk_api.patch_app_settings(patch)
                return self._json({"ok": ok, "original_quality": high})

            if path == "/api/settings":
                cfg = config.load_config()
                if "root" in body:
                    valid_root, err_msg = config.validate_root(body["root"])
                    if not valid_root:
                        return self._json({"ok": False, "message": err_msg})
                    cfg["root"] = valid_root
                for key in ("live_folder", "video_folder", "quality", "workers",
                            "folder_template", "best_image", "xhs_cookie"):
                    if key in body:
                        cfg[key] = body[key]
                config.save_config(cfg)
                state.invalidate_library()
                return self._json({"ok": True, "config": cfg})

            if path == "/api/post/delete":
                paths = body.get("paths") or body.get("files") or []
                if not isinstance(paths, list) or not paths:
                    return self._json({"ok": False, "message": "没有指定要删除的文件"})
                cfg = config.load_config()
                active_root = Path(cfg.get("root") or config.VOLUME_DIR).resolve()
                live_dir = config.live_out_dir(active_root).resolve()
                targets = []
                for raw in paths:
                    p = media.thumb_allowed(raw)
                    if not p or not config.is_under(p, active_root) or config.is_under(p, live_dir):
                        return self._json({"ok": False, "message": "包含不允许删除的路径"}, 403)
                    targets.append(p)
                deleted = media.recycle_files(targets)
                affected_folders = set()
                for p in targets:
                    try:
                        parts = p.relative_to(active_root).parts
                        if len(parts) >= 2:
                            affected_folders.add(parts[0])
                        else:
                            affected_folders = None
                            break
                    except ValueError:
                        affected_folders = None
                        break
                if affected_folders is None:
                    state.invalidate_library()
                else:
                    for folder_name in affected_folders:
                        state.invalidate_library(folder_name)
                return self._json({"ok": True, "deleted": deleted})

            if path == "/api/creators/remove":
                sec = (body.get("sec_uid") or "").strip()
                with state._creators_lock:
                    creators = [c for c in douk_api.load_creators() if c.get("sec_uid") != sec]
                    douk_api.save_creators(creators)
                return self._json({"ok": True, "count": len(creators)})

            if path == "/api/creators/remark":
                sec = (body.get("sec_uid") or "").strip()
                folder = (body.get("folder") or "").strip()
                nickname = (body.get("nickname") or "").strip()
                uid = (body.get("uid") or "").strip()
                remark = str(body.get("remark") or "").strip()[:80]
                if not sec and not folder:
                    return self._json({"ok": False, "message": "缺少博主标识"})
                with state._creators_lock:
                    creators = douk_api.load_creators()
                    entry = None
                    if sec:
                        entry = next((c for c in creators if c.get("sec_uid") == sec), None)
                    if entry is None and folder:
                        entry = next((c for c in creators if Path(c.get("folder") or "").name == folder), None)
                    if entry is None:
                        entry = {
                            "sec_uid": sec, "uid": uid, "nickname": nickname,
                            "folder": folder, "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "downloads": 0,
                        }
                        creators.append(entry)
                    if remark:
                        entry["remark"] = remark
                    else:
                        entry.pop("remark", None)
                    douk_api.save_creators(creators)
                state.invalidate_library()
                return self._json({"ok": True, "remark": remark})

            if path == "/api/cancel":
                state.cancel_job(body.get("id", ""))
                return self._json({"ok": True})

            if path == "/api/start-api":
                return self._json({"ok": True, "message": "已内置 f2 解析引擎，无需单独启动服务"})

            if path == "/api/livephoto/scan":
                root = config.resolve_root(body.get("root"))
                return self._json(livephoto_scan_payload(root))

            if path == "/api/livephoto/start":
                root = config.resolve_root(body.get("root"))
                items, _out = media.scan_live(root)
                todo = [x["path"] for x in items if not x["done"]]
                only = body.get("files")
                if isinstance(only, list) and only:
                    wanted = set(only)
                    todo = [p for p in todo if p in wanted]
                if not todo:
                    return self._json({"ok": False, "message": "没有需要转换的实况视频"})
                tools = config.tool_status()
                if not tools["ffmpeg"] or not tools["exiftool"]:
                    msg = (
                        "缺少转换工具：请先安装 ffmpeg 和 exiftool（如 brew install ffmpeg exiftool）"
                        if sys.platform == "darwin"
                        else "缺少转换工具：tools 目录里应有 ffmpeg.exe 和 exiftool.exe"
                    )
                    return self._json({"ok": False, "message": msg})
                jid = uuid.uuid4().hex
                job = {
                    "id": jid, "kind": "livephoto", "total": len(todo), "finished": 0,
                    "running": True, "cancelled": False, "started_at": time.time(),
                    "items": {
                        p: {
                            "id": p, "desc": Path(p).name, "status": "pending",
                            "message": "", "file": "",
                        } for p in todo
                    },
                }
                state.set_job(jid, job)
                threading.Thread(target=media.run_live_job, args=(jid, todo, root), daemon=True).start()
                return self._json({"ok": True, "job": jid, "total": len(todo)})

            if path == "/api/livephoto/open":
                root = config.resolve_root(body.get("root"))
                out = config.live_out_dir(root)
                try:
                    out.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
                return self._json(open_folder(str(out)))

            if path == "/api/frame":
                src = media.thumb_allowed(body.get("path") or "")
                if not src or src.suffix.lower() not in config.VIDEO_EXT:
                    return self._json({"ok": False, "message": "这条视频不在了，或不在保存目录里"})
                root = config.resolve_root(body.get("root"))
                auto = bool(body.get("auto"))
                seconds = None if auto else body.get("seconds")
                try:
                    res = media.extract_frame(src, root, seconds)
                    if res.get("ok"):
                        state.invalidate_library()
                    return self._json(res)
                except Exception as exc:
                    config.get_logger().warning("ffmpeg 抽帧失败 %s: %s", src, exc)
                    return self._json({"ok": False, "message": f"抽帧失败：{str(exc)[:220]}"})

            if path == "/api/frame/open":
                root = config.resolve_root(body.get("root"))
                out = root / config.FRAME_DIR
                try:
                    out.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
                return self._json(open_folder(str(out)))

            if path == "/api/tidy":
                cfg = config.load_config()
                moved, skipped = downloader.tidy_live_images(
                    body.get("root") or cfg["root"],
                    body.get("live_folder") or cfg.get("live_folder"),
                )
                state.invalidate_library()
                return self._json({"ok": True, "moved": moved, "skipped": skipped})

            if path == "/api/openfolder":
                return self._json(open_folder(body.get("path") or ""))

            if path == "/api/pickfolder":
                return self._json(pick_folder(body.get("initial") or ""))

        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            config.get_logger().warning("POST %s DouK API HTTP %s: %s", path, exc.code, detail)
            return self._json({"ok": False, "message": f"DouK API 报错 {exc.code}: {detail}"})
        except urllib.error.URLError as exc:
            config.get_logger().warning("POST %s 连不上 DouK API: %s", path, exc)
            return self._json({"ok": False, "message": f"连不上 DouK API，请确认它已以 Web API 模式运行：{exc}"})
        except Exception as exc:
            config.get_logger().exception("POST %s 未处理异常", path)
            return self._json({"ok": False, "message": f"{type(exc).__name__}: {exc}"})

        if path == "/api/discover/xhs":
            cat = (query.get("category") or ["homefeed_recommend"])[0]
            cursor_score = (query.get("cursor_score") or [""])[0]
            try:
                refresh_type = int((query.get("refresh_type") or ["1"])[0])
            except ValueError:
                refresh_type = 1
            try:
                note_index = int((query.get("note_index") or ["0"])[0])
            except ValueError:
                note_index = 0
            return self._json(xhs_api.fetch_homefeed(cat, cursor_score, refresh_type, note_index))

        if path == "/api/discover/xhs/user":
            uid = (query.get("user_id") or [""])[0]
            cursor = (query.get("cursor") or [""])[0]
            sec = f"xhs_{uid}" if uid else ""
            return self._json(_mark_discover_exists(xhs_api.fetch_user_notes_page(uid, cursor), sec))

        if path == "/api/discover/xhs/note":
            nid = (query.get("id") or [""])[0]
            token = (query.get("xsec_token") or [""])[0]
            source = (query.get("xsec_source") or ["pc_feed"])[0]
            raw_cookie = (config.load_config().get("xhs_cookie") or "").strip()
            try:
                it = xhs_api.fetch_note_detail(nid, xsec_token=token, xsec_source=source, raw_cookie=raw_cookie)
                return self._json({"ok": bool(it), "item": it})
            except Exception as exc:
                return self._json({"ok": False, "message": str(exc)})

        if path == "/api/discover/douyin":
            kw = (query.get("keyword") or [""])[0]
            try:
                pages = int((query.get("pages") or ["1"])[0])
            except ValueError:
                pages = 1
            return self._json(douk_api.search_douyin_users(kw, pages))

        if path == "/api/discover/douyin/user":
            sec = (query.get("sec_uid") or [""])[0]
            return self._json(_mark_discover_exists(douk_api.fetch_douyin_user_preview(sec), sec))

        if path == "/api/discover/douyin/feed":
            cat = (query.get("category") or ["beauty"])[0]
            kw = (query.get("keyword") or [""])[0]
            try:
                offset = int((query.get("offset") or ["0"])[0])
            except ValueError:
                offset = 0
            return self._json(douk_api.fetch_douyin_beauty_feed(cat, offset, kw))

        if path == "/api/proxy/media":
            media_url = (query.get("url") or [""])[0]
            if not _is_allowed_proxy_img(media_url):
                return self.send_error(403)
            is_xhs = "xhscdn.com" in media_url or "xiaohongshu.com" in media_url
            headers = {
                "User-Agent": config.UA,
                "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
                "Accept": "*/*",
            }
            if not is_xhs:
                dy_cookie = config.cookie_header()
                if dy_cookie:
                    headers["Cookie"] = dy_cookie
            rng = self.headers.get("Range")
            if rng:
                headers["Range"] = rng
            try:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(media_url, headers=headers)
                with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                    status = resp.status
                    self.send_response(status)
                    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                        val = resp.headers.get(h)
                        if val:
                            self.send_header(h, val)
                    self.end_headers()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        if not self._safe_write(chunk):
                            break
            except urllib.error.HTTPError as exc:
                self.send_error(exc.code)
            except _CLIENT_DISCONNECT:
                pass
            except Exception:
                self.send_error(404)
            return

        if path == "/api/proxy/img":
            img_url = (query.get("url") or [""])[0]
            if not _is_allowed_proxy_img(img_url):
                return self.send_error(403)
            is_xhs = "xhscdn.com" in img_url or "xiaohongshu.com" in img_url
            headers = {
                "User-Agent": config.UA,
                "Referer": "https://www.xiaohongshu.com/" if is_xhs else "https://www.douyin.com/",
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            }
            try:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                req = urllib.request.Request(img_url, headers=headers)
                with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                    data = resp.read()
                    ctype = resp.headers.get("Content-Type") or "image/jpeg"
            except Exception:
                return self.send_error(404)
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self._safe_write(data)
            except _CLIENT_DISCONNECT:
                pass
            return

        if path.startswith("/api/"):
            return self._json({"ok": False, "message": config.STALE_MSG}, 404)
        return self.send_error(404)


def livephoto_scan_payload(root):
    items, out = media.scan_live(root)
    pending = sum(1 for x in items if not x["done"])
    return {
        "ok": True, "root": str(root), "out": str(out),
        "tools": config.tool_status(), "items": items,
        "total": len(items), "pending": pending,
        "done": len(items) - pending,
    }


def pick_folder(initial=""):
    """弹系统文件夹选择框（支持 Windows 与 macOS），返回选中的路径。"""
    if sys.platform == "darwin":
        prompt = 'choose folder with prompt "选择下载保存目录"'
        if initial and os.path.isdir(initial):
            escaped = initial.replace("\\", "\\\\").replace('"', '\\"')
            prompt += f' default location POSIX file "{escaped}"'
        script = f"POSIX path of ({prompt})"
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
        except Exception as exc:
            return {"ok": False, "message": f"打开选择框失败：{exc}"}
        out = (r.stdout or "").strip().rstrip("/")
        if r.returncode != 0 or not out:
            return {"ok": False, "message": "没有选择文件夹"}
        return {"ok": True, "path": out}
    if os.name != "nt":
        return {"ok": False, "message": "当前系统暂不支持弹出文件夹选择框"}
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description='选择下载保存目录';"
        "$d.ShowNewFolderButton=$true;"
    )
    if initial and os.path.isdir(initial):
        ps += "$d.SelectedPath='%s';" % initial.replace("'", "''")
    ps += "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){Write-Output $d.SelectedPath}"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        return {"ok": False, "message": f"打开选择框失败：{exc}"}
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if not lines:
        return {"ok": False, "message": "没有选择文件夹"}
    return {"ok": True, "path": lines[0]}


def open_folder(target):
    cfg = config.load_config()
    cfg_root = Path(cfg.get("root") or str(config.VOLUME_DIR))
    p = Path(target or cfg_root)
    if not p.exists():
        p = cfg_root
    allowed_roots = [cfg_root, config.VOLUME_DIR, config.APP_DIR, config.BASE_DIR]
    try:
        pr = p.resolve()
        if not any(pr == ar.resolve() or config.is_under(pr, ar.resolve()) for ar in allowed_roots if ar.exists()):
            p = cfg_root
    except OSError:
        p = cfg_root
    if not p.exists():
        return {"ok": False, "message": f"文件夹不存在：{p}"}
    try:
        if os.name == "nt":
            os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"ok": True, "path": str(p)}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def _mark_discover_exists(payload, sec_uid=""):
    """为发现/作者主页预览列表标注是否已在本地下载。"""
    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return payload
    cfg = config.load_config()
    root = config.resolve_root()
    cached_by_id = {}
    sec = (sec_uid or payload.get("sec_uid") or "").strip()
    if sec:
        for tab in ("post", "favorite"):
            c_items, _ = douk_api.load_account_cache(sec, tab)
            for cit in c_items:
                cid = cit.get("id")
                if cid and cid not in cached_by_id:
                    cached_by_id[cid] = cit
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("downloads"):
            it["exists"] = downloader.item_exists(root, it, cfg)
        else:
            cit = cached_by_id.get(it.get("id"))
            it["exists"] = bool(cit and downloader.item_exists(root, cit, cfg))
    return payload


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if not config.WEB_DIR.exists():
        print(f"缺少 web 目录: {config.WEB_DIR}")
        sys.exit(1)
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        config.BUILD_FILE.write_bytes((config.WEB_DIR / "build.txt").read_bytes())
    except OSError:
        pass
    try:
        config.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(state.cleanup_pid_file)
    except OSError:
        pass
    httpd = QuietThreadingHTTPServer((config.HOST, config.PORT), Handler)
    threading.Thread(target=state.quit_watch, daemon=True).start()
    print(f"DouK-Lite 已启动: http://{config.HOST}:{config.PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.cleanup_pid_file()
