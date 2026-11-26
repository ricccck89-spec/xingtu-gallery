# -*- coding: utf-8 -*-
"""把抖音实况 mp4 转成 iPhone 实况照片（HEIC + MOV）。

用法: python3 lp.py <输出目录> <起始编号> <源文件> [...]
"""
import json
import re
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import numpy as np
from PIL import Image
import pillow_heif

pillow_heif.register_heif_opener()

EXIFTOOL = "/tmp/et/r2/usr/bin/exiftool"
ENV_PERL = {"PERL5LIB": "/tmp/et/r2/usr/share/perl5"}
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def exiftool(args):
    import os
    env = dict(os.environ, **ENV_PERL)
    return subprocess.run([EXIFTOOL, "-m", "-q", *args], capture_output=True, text=True, env=env)


def duration(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def apple_makernote(value: bytes):
    """Apple MakerNote：14 字节头（'Apple iOS\\0' + 版本 2B + 'MM'），IFD 紧接其后，
    条目的值偏移相对 maker note 起点。—— 这是 exiftool MakerNotes.pm 里
    Start => valuePtr+14 / Base => start-14 的实测结论，写错了 exiftool 会报 Bad MakerNotes directory。
    """
    hdr = b"Apple iOS\x00" + b"\x00\x01" + b"MM"
    data_off = 14 + 2 + 12 + 4               # count + entry + next-ifd
    entry = struct.pack(">HHII", 0x0011, 2, len(value), data_off)
    ifd = struct.pack(">H", 1) + entry + struct.pack(">I", 0)
    return hdr + ifd + value


def sharpness(img):
    g = np.asarray(img.convert("L"), dtype=np.float32)
    if min(g.shape) < 3:
        return 0.0
    lap = (np.abs(-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]))
    return float(lap.var())


def pick_cover(src: Path, tmp: Path):
    """抽若干帧，跳过过暗的，取最清晰的一帧。"""
    out = tmp / "f%02d.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf", "fps=3",
                    "-t", "3", str(out)], check=False)
    frames = sorted(tmp.glob("f*.png"))
    best, best_score = None, -1.0
    for f in frames:
        img = Image.open(f)
        if np.asarray(img.convert("L"), dtype=np.float32).mean() < 22:
            continue
        s = sharpness(img)
        if s > best_score:
            best, best_score = img, s
    if best is None and frames:
        best = Image.open(frames[len(frames) // 2])
    return best


def convert(src: Path, outdir: Path, name: str):
    stamp = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2})_(\d{2})_(\d{2})", src.name)
    when = f"{stamp.group(1)} {stamp.group(2)}:{stamp.group(3)}:{stamp.group(4)}" if stamp else ""
    exif_when = when.replace("-", ":", 2) if when else ""

    heic = outdir / f"{name}.HEIC"
    mov = outdir / f"{name}.MOV"
    dur = duration(src)
    ident = str(uuid.uuid4()).upper()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cover = pick_cover(src, tmp)
        if cover is None:
            raise RuntimeError("没有抽到画面")
        cover.convert("RGB").save(heic, format="HEIF", quality=92)

        # 静图这边：MakerNote 0x0011 = ContentIdentifier，必须和 MOV 里的 UUID 一模一样
        blk = tmp / "mn.bin"
        blk.write_bytes(apple_makernote(ident.encode() + b"\x00"))
        exiftool([f"-MakerNoteApple<={blk}", "-overwrite_original", str(heic)])

    # MOV：3 秒以内直接复制流，超了才重编码（Apple 规格上限 3.00s）
    base = [FFMPEG, "-y", "-v", "error", "-i", str(src), "-t", "3"]
    if dur > 3.02:
        tail = ["-c:v", "libx264", "-crf", "19", "-c:a", "aac", "-movflags", "+faststart", str(mov)]
    else:
        tail = ["-c", "copy", "-movflags", "+faststart", str(mov)]
    subprocess.run(base + tail, check=True)

    args = [f"-QuickTime:ContentIdentifier={ident}"]
    if exif_when:
        args.append(f"-QuickTime:CreateDate={exif_when}")
    exiftool([*args, "-overwrite_original", str(mov)])
    if exif_when:
        exiftool([f"-EXIF:DateTimeOriginal={exif_when}", "-overwrite_original", str(heic)])

    return {"name": name, "when": when, "duration": round(min(dur, 3.0), 2), "uuid": ident}


def main():
    outdir = Path(sys.argv[1])
    start = int(sys.argv[2])
    files = [Path(x) for x in sys.argv[3:]]
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    n = start
    for i, src in enumerate(files):
        name = f"IMG_{n:04d}"
        try:
            info = convert(src, outdir, name)
            rows.append(info)
            print(f"OK   {name}  {info['duration']}s  {src.name[:50]}")
            n += 1
        except Exception as exc:
            print(f"FAIL {src.name[:50]}: {type(exc).__name__}: {exc}")
    (Path("/tmp/lp_result.json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
