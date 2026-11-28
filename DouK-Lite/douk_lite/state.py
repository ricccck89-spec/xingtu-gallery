# -*- coding: utf-8 -*-
"""进程内共享状态、缓存与自动退出看门狗。"""

import os
import subprocess
import threading
import time
from pathlib import Path

from douk_lite import config

_jobs = {}
_jobs_lock = threading.Lock()
_comfy_running = False
_parse_items = {}
_parse_order = []

_probe_sem = threading.Semaphore(6)
_thumb_sem = threading.Semaphore(3)
_thumb_key_locks = {}
_thumb_key_guard = threading.Lock()
_last_thumb_prune = 0.0

_lib_cache = {}
_gallery_cache = {}
_lib_lock = threading.Lock()

_frame_cache = {}
_frame_lock = threading.Lock()

_creators_lock = threading.Lock()

_quit_at = 0.0
_clients = 0
_ever_connected = False
_client_lost_at = 0.0
_presence_lock = threading.Lock()


def request_shutdown(grace=8.0):
    global _quit_at
    _quit_at = time.time() + grace


def cancel_shutdown():
    global _quit_at
    _quit_at = 0.0


def presence_connect():
    global _clients, _ever_connected, _client_lost_at
    with _presence_lock:
        _clients += 1
        _ever_connected = True
        _client_lost_at = 0.0


def presence_disconnect():
    global _clients, _client_lost_at
    with _presence_lock:
        _clients -= 1
        if _clients <= 0:
            _clients = 0
            _client_lost_at = time.time()


def set_comfy_running(running: bool):
    global _comfy_running
    with _jobs_lock:
        _comfy_running = bool(running)


def has_active_work_jobs():
    with _jobs_lock:
        if _comfy_running:
            return True
        for job in _jobs.values():
            if job.get("running") and job.get("kind") in ("download", "livephoto"):
                return True
    return False


def set_job(jid, job):
    with _jobs_lock:
        _jobs[jid] = job


def get_job(jid):
    with _jobs_lock:
        return _jobs.get(jid)


def get_job_snapshot(jid):
    """在锁保护下复制任务字典，避免工作线程并发改写时 json.dumps 读到半成品或抛错。"""
    with _jobs_lock:
        job = _jobs.get(jid)
        if not job:
            return None
        snap = dict(job)
        items = job.get("items")
        if isinstance(items, dict):
            snap["items"] = {k: dict(v) for k, v in items.items()}
        return snap


def cancel_job(jid):
    with _jobs_lock:
        job = _jobs.get(jid)
        if not job:
            return False
        job["cancelled"] = True
        return True


def prune_jobs(max_keep=24):
    with _jobs_lock:
        finished = [
            (jid, job.get("done_at") or job.get("started_at") or 0.0)
            for jid, job in _jobs.items()
            if not job.get("running")
        ]
        if len(finished) <= max_keep:
            return
        finished.sort(key=lambda x: x[1])
        for jid, _ in finished[: len(finished) - max_keep]:
            if jid in _parse_items:
                continue
            _jobs.pop(jid, None)


def remember_parse(jid, items):
    with _jobs_lock:
        _parse_items[jid] = items
        _parse_order.append(jid)
        while len(_parse_order) > 4:
            old = _parse_order.pop(0)
            _parse_items.pop(old, None)
            _jobs.pop(old, None)


def get_parse_items(jid):
    with _jobs_lock:
        items = _parse_items.get(jid)
        return list(items) if items else []


def invalidate_library(folder=None):
    """下载完或设置变了以后清掉扫描缓存。folder 传文件夹名只清那一个。"""
    with _lib_lock:
        _gallery_cache.clear()
        if folder is None:
            _lib_cache.clear()
        else:
            for key in list(_lib_cache):
                if Path(key).name == folder:
                    _lib_cache.pop(key, None)
    with _frame_lock:
        _frame_cache.clear()


def cleanup_pid_file():
    try:
        if config.PID_FILE.exists() and config.PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            config.PID_FILE.unlink()
    except OSError:
        pass


def hard_exit():
    """清理 PID 文件并退出当前服务进程。"""
    cleanup_pid_file()
    os._exit(0)


def quit_watch():
    while True:
        time.sleep(1)
        if has_active_work_jobs():
            continue
        now = time.time()
        if _quit_at and now >= _quit_at:
            hard_exit()
        with _presence_lock:
            idle = _clients == 0
            lost = _client_lost_at
        # 页面开着一条 SSE，关窗口后连接断掉；留 6 秒给刷新/重开
        if _ever_connected and idle and lost and now - lost > 6:
            hard_exit()
