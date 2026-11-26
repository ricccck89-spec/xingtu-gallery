# -*- coding: utf-8 -*-
r"""主页解析逻辑自测（不需要联网、不需要抖音，也不需要启动 DouK）。

用法（在 DouK-Lite 目录下）：
    python\python.exe tests\selftest_parse.py .
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

APP = Path(sys.argv[1]).resolve() if len(sys.argv) >= 2 else Path(__file__).resolve().parent.parent
os.environ["DOUK_API"] = "http://127.0.0.1:9933"
sys.path.insert(0, str(APP))

from douk_lite import config, douk_api, http_server, state  # noqa: E402

_TEST_TMP = Path(tempfile.mkdtemp(prefix="douk_selftest_"))
config.DOUK_API = "http://127.0.0.1:9933"
config.ACCOUNT_CACHE = _TEST_TMP / "accounts"
config.CREATORS_FILE = _TEST_TMP / "creators.json"
config.CONFIG_FILE = _TEST_TMP / "config.json"
FAILED = False


def check(name, cond, extra=""):
    global FAILED
    print(("PASS " if cond else "FAIL ") + name + (("  " + repr(extra)) if extra != "" else ""))
    if not cond:
        FAILED = True


NOW = datetime.now()
STATE = {
    "items": [], "calls": 0, "reqs": [], "fail_after": None, "slow_chunk": 0,
    "slow_first": 0, "slow_secs": 0, "fail_from": 0, "fail_until": 10 ** 9,
    "ignore_cursor": False, "fail_chunks": 0,
}


def mk(pid, when):
    return {
        "id": pid, "type": "视频", "desc": pid,
        "create_time": when.strftime("%Y-%m-%d %H:%M:%S"),
        "create_timestamp": int(when.timestamp()), "nickname": "测试博主",
        "uid": "1", "sec_uid": "SEC", "duration": "10",
        "downloads": ["http://127.0.0.1:9933/v.mp4"], "static_cover": "",
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        path = self.path.split("?")[0]
        if path == "/douyin/detail":
            return self._json({"message": "ok", "data": STATE["items"][0]})
        if path != "/douyin/account":
            return self._json({"message": "nope", "data": None}, 404)

        STATE["calls"] += 1
        STATE["reqs"].append(body)
        if STATE.get("slow_first") and STATE["calls"] <= STATE["slow_first"]:
            time.sleep(STATE.get("slow_secs") or 0)
        elif STATE.get("slow_chunk"):
            time.sleep(STATE["slow_chunk"])
        if STATE.get("fail_chunks") and STATE["calls"] <= STATE["fail_chunks"]:
            return self._json({"detail": "timeout"}, 500)
        if STATE.get("fail_from") and STATE["fail_from"] <= STATE["calls"] <= STATE["fail_until"]:
            return self._json({"detail": "timeout"}, 500)
        if STATE["fail_after"] and STATE["calls"] > STATE["fail_after"]:
            return self._json({"message": "获取数据失败！", "data": None})

        cur = int(body.get("cursor") or 0)
        if STATE["ignore_cursor"]:
            cur = 0
        earliest, latest = body.get("earliest"), body.get("latest")
        d = datetime.strptime(earliest, "%Y/%m/%d").date() if earliest else None
        l = datetime.strptime(latest, "%Y/%m/%d").date() if latest else None
        rows = [
            x for x in STATE["items"]
            if not cur or x["create_timestamp"] * 1000 <= cur
        ]
        out, i = [], 0
        while i < len(rows):
            page = rows[i:i + 18]
            out.extend(
                x for x in page
                if (not d or datetime.fromtimestamp(x["create_timestamp"]).date() >= d)
                and (not l or datetime.fromtimestamp(x["create_timestamp"]).date() <= l)
            )
            i += 18
            if d and datetime.fromtimestamp(page[-1]["create_timestamp"]).date() < d:
                break
        return self._json({"message": "获取数据成功！", "data": out})


srv = http_server.QuietThreadingHTTPServer(("127.0.0.1", 9933), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def new_job(jid):
    job = {
        "id": jid, "kind": "works", "target_kind": "user", "tab": "post", "url": "",
        "nickname": "", "count": 0, "cached": 0, "pages": 0, "running": True,
        "cancelled": False, "status": "running", "message": "", "reason": "",
        "phase": "", "started_at": time.time(),
    }
    state._jobs[jid] = job
    return job


def run(jid, sec_uid="SEC", tab="post", recent_days=0):
    STATE.update(calls=0, reqs=[])
    job = new_job(jid)
    douk_api.run_account_job(job, sec_uid, tab, recent_days)
    return job, state._parse_items.get(jid, [])


HOURS = [mk("h%03d" % i, NOW - timedelta(hours=i)) for i in range(65)]

# ---- 1. 正常情况 ----
STATE.update(items=HOURS)
job, items = run("j1")
check("小账号一段取全", len(items) == 65, len(items))
check("翻到底后标记为完整", douk_api.load_account_cache("SEC", "post")[1] is True)
check("结束原因", job["reason"] == "没有更多作品", job["reason"])

# ---- 2. 开头失败重试 ----
STATE.update(items=HOURS, fail_chunks=2)
job2, items2 = run("j2", sec_uid="SEC2")
STATE["fail_chunks"] = 0
check("开头连续失败也能补齐", len(items2) == 65, len(items2))
check("失败后确实重试了", STATE["calls"] >= 3, STATE["calls"])

# ---- 3. 稀疏账号 ----
STATE.update(items=[mk("s%02d" % i, NOW - timedelta(days=45 * i)) for i in range(24)])
job3, items3 = run("j3", sec_uid="SEC3")
check("稀疏账号也补齐", len(items3) == 24, len(items3))
check("稀疏账号请求数有上界", STATE["calls"] <= 40, STATE["calls"])

# ---- 4. 完整缓存补最新 ----
job4, items4 = run("j4", sec_uid="SEC3")
check("完整缓存只发 1 个请求", STATE["calls"] == 1, STATE["calls"])
check("完整缓存数量不变", len(items4) == 24, len(items4))

# ---- 5. 置顶作品 ----
pinned = [mk("p1", NOW - timedelta(days=700)), mk("p2", NOW - timedelta(days=680))]
STATE.update(items=pinned + [mk("n%03d" % i, NOW - timedelta(hours=i)) for i in range(100)])
job5, items5 = run("j5", sec_uid="SEC5")
check("置顶不影响，102 条全拿到", len(items5) == 102, len(items5))

# ---- 6. 半截缓存补全 ----
STATE["items"] = [mk("c%02d" % i, NOW - timedelta(days=i)) for i in range(60)]
part = [douk_api.normalize_item(x) for x in STATE["items"][:30]]
douk_api.save_account_cache("SEC6", "post", "测试博主", part, complete=False)
job6, items6 = run("j6", sec_uid="SEC6")
check("半截缓存也能补全", len(items6) == 60, len(items6))
check("补全后标记为完整", douk_api.load_account_cache("SEC6", "post")[1] is True)

# ---- 6b. 旧缓存接新 ----
STATE["items"] = [mk("u%03d" % i, NOW - timedelta(days=i)) for i in range(200)]
old_part = [douk_api.normalize_item(x) for x in STATE["items"][100:130]]
douk_api.save_account_cache("SEC6B", "post", "测试博主", old_part, complete=False)
job6b, items6b = run("j6b", sec_uid="SEC6B")
check("旧缓存 + 新作品也能补全", len(items6b) == 200, len(items6b))

# ---- 7. cursor 被无视 ----
STATE.update(items=[mk("g%02d" % i, NOW - timedelta(days=i)) for i in range(60)], ignore_cursor=True)
job7, items7 = run("j7", sec_uid="SEC7")
STATE["ignore_cursor"] = False
check("cursor 被无视时也能取全", len(items7) == 60, len(items7))

# ---- 8. 中途失败保留 ----
STATE.update(items=[mk("d%03d" % i, NOW - timedelta(days=i)) for i in range(400)], fail_from=4, fail_until=10 ** 9)
job8, items8 = run("j8", sec_uid="SEC8")
STATE["fail_from"] = 0
check("失败时保留已取到的", 0 < len(items8) < 400, len(items8))
check("失败也落盘", len(douk_api.load_account_cache("SEC8", "post")[0]) == len(items8))
check("失败后不标成完整", douk_api.load_account_cache("SEC8", "post")[1] is False)

# ---- 9. 单作品 ----
job9 = new_job("j9")
job9["target_kind"] = "work"
STATE["items"] = HOURS
douk_api.run_work_job(job9, "h000")
check("单作品解析", job9["status"] == "done" and len(state._parse_items["j9"]) == 1, (job9["status"], job9["count"]))

# ---- 10. 快速模式 ----
STATE.update(
    items=[mk("q%03d" % i, NOW - timedelta(days=i)) for i in range(90)],
    fail_after=None, ignore_cursor=False, slow_chunk=0,
)
job10, items10 = run("j10", sec_uid="SEC10", recent_days=30)
check("快速模式只发 1 个请求", STATE["calls"] == 1, STATE["calls"])
check("快速模式只取最近 30 天", 29 <= len(items10) <= 32, len(items10))
check("快速模式不把缓存标成完整", douk_api.load_account_cache("SEC10", "post")[1] is False)
check("快速模式结束原因", job10["reason"].startswith("最近 30 天"), job10["reason"])

# ---- 11. 停止解析 ----
STATE.update(
    items=[mk("k%03d" % i, NOW - timedelta(hours=i)) for i in range(40)],
    fail_after=None, ignore_cursor=False, slow_chunk=6,
)
douk_api.save_account_cache(
    "SEC11", "post", "测试博主",
    [douk_api.normalize_item(x) for x in STATE["items"][:10]],
    complete=False,
)
job11 = new_job("j11")
timing = {}


def _cancelled_run():
    t0 = time.time()
    douk_api.run_account_job(job11, "SEC11", "post")
    timing["elapsed"] = time.time() - t0


th = threading.Thread(target=_cancelled_run, daemon=True)
th.start()
time.sleep(0.8)
job11["cancelled"] = True
th.join(4)
STATE["slow_chunk"] = 0
check("停止后很快就返回", timing.get("elapsed", 99) < 2.5, timing)
check("停止后任务不再运行", job11["running"] is False, job11.get("running"))
check("停止原因是已停止解析", job11["reason"] == "已停止解析", job11["reason"])
check("停止后保留缓存里的 10 个", len(state._parse_items.get("j11", [])) == 10, len(state._parse_items.get("j11", [])))

# ---- 12. 千条大号 ----
STATE.update(
    items=[mk("b%04d" % i, NOW - timedelta(days=i)) for i in range(1200)],
    fail_after=None, ignore_cursor=False, slow_chunk=0, fail_chunks=0,
)
job12, items12 = run("j12", sec_uid="SECBIG")
check("1200 条全部取回", len(items12) == 1200, len(items12))
check("1200 条请求数没爆炸", STATE["calls"] <= 60, STATE["calls"])
check("大账号也标成完整", douk_api.load_account_cache("SECBIG", "post")[1] is True)

# ---- 13. 单段超时重试 ----
_old_timeout = douk_api.PARSE_CHUNK_TIMEOUT
douk_api.PARSE_CHUNK_TIMEOUT = 1
STATE.update(
    items=[mk("t%03d" % i, NOW - timedelta(days=i)) for i in range(200)],
    slow_first=1, slow_secs=1.6, slow_chunk=0, fail_chunks=0,
)
job13, items13 = run("j13", sec_uid="SEC13")
douk_api.PARSE_CHUNK_TIMEOUT = _old_timeout
STATE["slow_first"] = 0
STATE["slow_secs"] = 0
check("单段超时后缩小范围也能补齐", len(items13) == 200, len(items13))
check("超时后确实换了更小的段", job13["pages"] > 1, job13["pages"])

# ---- 14. 大号 + 置顶 ----
STATE.update(
    items=[mk("z1", NOW - timedelta(days=1500)), mk("z2", NOW - timedelta(days=1400))]
    + [mk("y%04d" % i, NOW - timedelta(days=i)) for i in range(600)],
    slow_first=0, slow_secs=0, slow_chunk=0, fail_chunks=0, fail_from=0,
)
job14, items14 = run("j14", sec_uid="SECPIN")
check("置顶 + 600 条都能拿全", len(items14) == 602, len(items14))

# ---- 15. 超 30 天未更新完整缓存补漏 ----
old_item = douk_api.normalize_item(mk("old45", NOW - timedelta(days=45)))
douk_api.save_account_cache("SECGAP", "post", "测试博主", [old_item], complete=True)
STATE.update(items=[mk("new38", NOW - timedelta(days=38)), mk("old45", NOW - timedelta(days=45))])
job15, items15 = run("j15", sec_uid="SECGAP")
check("超过 30 天未更新仍能补回 38 天前的新作品", len(items15) == 2, len(items15))
check("补新后仍保持完整标记", douk_api.load_account_cache("SECGAP", "post")[1] is True)

print()
print("RESULT:", "FAILED" if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
