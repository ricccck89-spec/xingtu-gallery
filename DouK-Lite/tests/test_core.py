# -*- coding: utf-8 -*-
r"""DouK-Lite 核心功能、安全边界与实况转换测试。

用法（在 DouK-Lite 目录下）：
    python\python.exe tests\test_core.py
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))

from douk_lite import config, douk_api, downloader, gallery, http_server, media, state, xhs_api  # noqa: E402


def main():
    # 1. 作品归组正则边界
    assert gallery._post_group_key("IMG_0001") == "IMG_0001"
    assert gallery._post_group_key("IMG_0002") == "IMG_0002"
    assert gallery._post_group_key("2024-01-06 18.40.17-视频-昵称-vlog_2025") == "2024-01-06 18.40.17-视频-昵称-vlog_2025"
    assert gallery._post_group_key("2024-01-06 18.40.17-图集-昵称-标题_1") == "2024-01-06 18.40.17-图集-昵称-标题"
    assert gallery._post_group_key("2024-01-06 18.40.17-实况-昵称-标题_2") == "2024-01-06 18.40.17-实况-昵称-标题"

    # 2. 路径白名单校验
    assert config.validate_root("")[0] is None
    assert config.validate_root(str(config.APP_DIR))[0] is None
    assert config.validate_root(str(config.WEB_DIR))[0] is None
    assert config.validate_root(r"E:\Files\pics")[0] == r"E:\Files\pics"

    # 3. scan_live 跳过 截帧 与 LivePhotos
    td = Path(tempfile.mkdtemp(prefix="douk_core_test_"))
    (td / "博主A").mkdir()
    (td / "博主A" / "2025-01-01 10.00.00-实况-博主A-测试.mp4").write_bytes(b"00")
    (td / "截帧" / "博主A").mkdir(parents=True)
    (td / "截帧" / "博主A" / "2025-01-01 10.00.00-实况-博主A-测试.mp4").write_bytes(b"00")
    (td / "LivePhotos").mkdir()
    (td / "LivePhotos" / "2025-01-01 10.00.00-实况-博主A-测试.mp4").write_bytes(b"00")
    live_items, _ = media.scan_live(td)
    assert len(live_items) == 1 and "截帧" not in live_items[0]["rel"]

    # 4. gallery_payload 排序与缓存
    (td / "自制").mkdir()
    (td / "自制" / "IMG_0001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100)
    (td / "自制" / "IMG_0002.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 500)
    old_cfg_file = config.CONFIG_FILE
    old_creators_file = config.CREATORS_FILE
    config.CONFIG_FILE = td / "config.json"
    config.CREATORS_FILE = td / "creators.json"
    config.save_config({**config.DEFAULT_CONFIG, "root": str(td)})
    state.invalidate_library()

    g1 = gallery.gallery_payload(folder="自制", sort="largest")
    assert g1["counts"]["filtered"] == 2
    assert g1["posts"][0]["key"] == "IMG_0002"
    g2 = gallery.gallery_payload(folder="自制", sort="newest")
    assert len(g2["posts"]) == 2

    # 5. HTTP 同源拦截 + 删除越权拦截
    httpd = http_server.QuietThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings",
            data=b'{"workers":2}',
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Expected 403 for cross-origin POST"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/post/delete",
            data=json.dumps({"paths": [str(config.APP_DIR / "server.py")]}).encode(),
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req2, timeout=5)
            assert False, "Expected 403 for deleting outside root"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        config.CONFIG_FILE = old_cfg_file
        config.CREATORS_FILE = old_creators_file
        state.invalidate_library()

    # 6. 实况照片端到端转换
    tools = config.tool_status()
    assert tools["ffmpeg"] and tools["exiftool"] and tools["heic"]
    src = td / "2026-11-18 12.00.00-实况-测试-demo.mp4"
    out = td / "live_out"
    out.mkdir()
    config.run_tool([
        tools["ffmpeg"], "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1.5",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
    ], timeout=30)
    rec = media.convert_live_one(src, out, "demo", tools["ffmpeg"], tools["exiftool"])
    assert Path(rec["still"]).exists() and Path(rec["still"]).stat().st_size > 0
    assert Path(rec["mov"]).exists() and Path(rec["mov"]).stat().st_size > 0

    # 7. Cookie 字典与字符串转换
    assert config.cookie_header({"a": "1", "b": "2", "empty": ""}) == "a=1; b=2"
    assert config.cookie_header("  k=v; x=y  ") == "k=v; x=y"
    assert config.cookie_header(None) == ""

    # 8. 配置缓存刷新 + item_exists 查重 + /api/works/items exists 标注
    td2 = Path(tempfile.mkdtemp(prefix="douk_core_test2_"))
    old_cfg_file = config.CONFIG_FILE
    config.CONFIG_FILE = td2 / "config.json"
    config.save_config({**config.DEFAULT_CONFIG, "root": str(td2), "folder_template": "{nickname}"})
    assert config.load_config()["root"] == str(td2)
    config.save_config({**config.DEFAULT_CONFIG, "root": str(td2), "folder_template": "{nickname}", "workers": 5})
    assert config.load_config()["workers"] == 5

    creator_dir = td2 / "测试作者"
    (creator_dir / "视频").mkdir(parents=True)
    (creator_dir / "视频" / "2026-11-18 10.00.00-视频-测试作者-单条.mp4").write_bytes(b"ftyp" + b"0" * 32)
    (creator_dir / "2026-11-18 11_00_00-图集-测试作者-双图_1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
    (creator_dir / "2026-11-18 11_00_00-图集-测试作者-双图_2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)

    it_vid = {
        "id": "w1", "type": "视频", "desc": "单条", "create_time": "2026-11-18 10:00:00",
        "nickname": "测试作者", "uid": "1", "sec_uid": "S1", "downloads": ["http://x/1"],
    }
    it_img = {
        "id": "w2", "type": "图集", "desc": "双图", "create_time": "2026-11-18 11:00:00",
        "nickname": "测试作者", "uid": "1", "sec_uid": "S1", "downloads": ["http://x/1", "http://x/2"],
    }
    it_miss = {
        "id": "w3", "type": "视频", "desc": "未下", "create_time": "2026-11-18 12:00:00",
        "nickname": "测试作者", "uid": "1", "sec_uid": "S1", "downloads": ["http://x/3"],
    }
    assert downloader.item_exists(td2, it_vid) is True
    assert downloader.item_exists(td2, it_img) is True
    assert downloader.item_exists(td2, it_miss) is False

    # 9. _jobs 快照隔离 + /api/works/items 返回 exists + scan_frame_videos limit 排序截断
    jid = "test_job_1"
    state.set_job(jid, {"id": jid, "running": False, "target_kind": "user", "tab": "post",
                        "items": {"a": {"status": "pending"}}})
    snap = state.get_job_snapshot(jid)
    snap["items"]["a"]["status"] = "mutated"
    assert state.get_job_snapshot(jid)["items"]["a"]["status"] == "pending"
    state.remember_parse(jid, [it_vid, it_miss])

    httpd2 = http_server.QuietThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    port2 = httpd2.server_address[1]
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port2}/api/works/items?id={jid}", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            by_id = {x["id"]: x for x in payload["items"]}
            assert by_id["w1"]["exists"] is True and by_id["w3"]["exists"] is False
    finally:
        httpd2.shutdown()
        httpd2.server_close()

    # 10. 局部失效缓存与视频扫描排序
    (td2 / "另一作者").mkdir()
    (td2 / "另一作者" / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"1" * 20)
    state.invalidate_library()
    gallery.creator_dir_info(td2, folder_name="测试作者")
    gallery.creator_dir_info(td2, folder_name="另一作者")
    assert len(state._lib_cache) == 2
    state.invalidate_library("测试作者")
    assert any(Path(k).name == "另一作者" for k in state._lib_cache)
    assert not any(Path(k).name == "测试作者" for k in state._lib_cache)

    vids = media.scan_frame_videos(td2, refresh=True, limit=1)
    assert len(vids) == 1
    config.CONFIG_FILE = old_cfg_file
    state.invalidate_library()

    # 11. 小红书链接识别、作品归一化与博主记忆
    t_user = douk_api.parse_target("https://www.xiaohongshu.com/user/profile/59d8cb33de5fb4696bf17217?xsec_token=abc")
    assert t_user and t_user["platform"] == "xhs" and t_user["kind"] == "user"
    assert t_user["id"] == "59d8cb33de5fb4696bf17217" and t_user["xsec_token"] == "abc"

    t_note = douk_api.parse_target("看这个 https://www.xiaohongshu.com/explore/66aabbccdd11223344556677?xsec_token=tok")
    assert t_note and t_note["platform"] == "xhs" and t_note["kind"] == "work"
    assert t_note["id"] == "66aabbccdd11223344556677"

    t_pnote = douk_api.parse_target("https://www.rednote.com/user/profile/59d8cb33de5fb4696bf17217/66aabbccdd11223344556677")
    assert t_pnote and t_pnote["platform"] == "xhs" and t_pnote["kind"] == "work"
    assert t_pnote["id"] == "66aabbccdd11223344556677"

    t_internal = douk_api.parse_target("xhs_59d8cb33de5fb4696bf17217")
    assert t_internal and t_internal["platform"] == "xhs" and t_internal["kind"] == "user"

    n_vid = xhs_api.normalize_xhs_note({
        "note_id": "n1", "type": "video", "title": "测试视频", "desc": "正文",
        "time": 1700000000000,
        "user": {"user_id": "u1", "nickname": "小红书博主"},
        "video": {"consumer": {"origin_video_key": "abc/vid.mp4"}},
        "image_list": [{"url_default": "https://sns-img-bd.xhscdn.com/00/11/22/33/44/cover!nd_dft"}],
    })
    assert n_vid["type"] == "视频" and n_vid["sec_uid"] == "xhs_u1"
    assert n_vid["downloads"] == ["https://sns-video-bd.xhscdn.com/abc/vid.mp4"]

    n_live = xhs_api.normalize_xhs_note({
        "noteId": "n2", "type": "normal", "title": "实况图",
        "time": 1700000000000,
        "user": {"userId": "u1", "nickname": "小红书博主"},
        "imageList": [{
            "fileId": "img_tok_1",
            "livePhoto": True,
            "stream": {"h264": [{"masterUrl": "http://sns-video-bd.xhscdn.com/live1.mp4"}]},
        }],
    })
    assert n_live["type"] == "实况"
    assert n_live["downloads"] == [
        "https://sns-video-bd.xhscdn.com/live1.mp4",
        "https://sns-img-bd.xhscdn.com/img_tok_1",
    ]

    cookies_dict, cookie_str, has_sess = xhs_api._ensure_cookies("a1=testa1; web_session=sess123")
    assert has_sess is True and "webId=" in cookie_str and cookies_dict["a1"] == "testa1"

    tsv_cookie = "a1\tmya1\t.xiaohongshu.com\nacw_tc\tshort\twww.xiaohongshu.com\nweb_session\tmysess\t.xiaohongshu.com"
    parsed_tsv = xhs_api.parse_cookie_dict(tsv_cookie)
    assert parsed_tsv == {"a1": "mya1", "web_session": "mysess"}

    json_cookie = json.dumps([{"name": "web_session", "value": "jsess"}, {"name": "sec_poison_id", "value": "skip"}])
    assert xhs_api.parse_cookie_dict(json_cookie) == {"web_session": "jsess"}

    print("ALL CORE TESTS PASS")


if __name__ == "__main__":
    main()
