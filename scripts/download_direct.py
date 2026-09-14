# -*- coding: utf-8 -*-
"""Direct downloader using signed video_download_url from the MediaCrawler crawl.

Primary: signed direct URL (fast, no third-party dependency).
Fallback: videodl KedouVideoClient (for expired / missing direct URLs).
Shares logs and lock with run_pipeline.py; existing mp4s are skipped.
"""
from __future__ import annotations

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

UA_MOBILE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{now()}] {line}\n")


def load_config(creator_root: Path) -> dict:
    cfg = {}
    cand = creator_root / "pipeline_config.json"
    if cand.exists():
        try:
            cfg.update(json.loads(cand.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def load_jsonl_urls(path: Path) -> dict:
    """aweme_id -> video_download_url (first occurrence)."""
    out = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            aid = str(o.get("aweme_id") or "").strip()
            url = (o.get("video_download_url") or "").strip()
            if aid and url and aid not in out:
                out[aid] = url
    return out


def has_audio_stream(video: Path, ffprobe: str) -> bool:
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        return bool(p.stdout and "audio" in p.stdout)
    except Exception:
        return False


def find_downloaded_file(work_dir: Path):
    if not work_dir.exists():
        return None
    cands = []
    for p in work_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"} and p.stat().st_size > 100_000:
            cands.append(p)
    if not cands:
        return None

    def score(p: Path) -> tuple:
        stem = p.stem
        hashlike = len(stem) == 32 and all(c in "0123456789abcdef" for c in stem.lower())
        return (0 if hashlike else 1, p.stat().st_size, p.stat().st_mtime)

    cands.sort(key=score, reverse=True)
    return cands[0]


def download_direct(url: str, dest: Path, work_dir: Path, timeout: int = 120) -> tuple[bool, str]:
    tmp = work_dir / "direct.mp4"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(
            url,
            headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"},
            timeout=timeout,
            stream=True,
        ) as r:
            if r.status_code != 200:
                return False, f"direct http {r.status_code}"
            tmp.unlink(missing_ok=True)
            size = 0
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            if size <= 100_000:
                tmp.unlink(missing_ok=True)
                return False, f"direct too small {size}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.unlink()
            shutil.move(str(tmp), str(dest))
            return True, f"direct size={size}"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return False, f"direct exc {e}"


def download_videodl(videodl, videodl_cwd, client, work, url, dest) -> tuple[bool, str]:
    job_dir = work / f"dl_{dest.stem}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    cobj = {client: {"work_dir": str(job_dir).replace("\\", "/")}}
    cmd = [str(videodl), "-i", url, "-g", "-a", client, "-c", json.dumps(cobj, ensure_ascii=False)]
    try:
        p = subprocess.run(
            cmd, cwd=str(videodl_cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1800,
        )
        out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
        found = find_downloaded_file(job_dir)
        if found and found.stat().st_size > 100_000:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.unlink()
            shutil.move(str(found), str(dest))
            shutil.rmtree(job_dir, ignore_errors=True)
            return True, f"videodl size={dest.stat().st_size}"
        shutil.rmtree(job_dir, ignore_errors=True)
        return False, f"videodl rc={p.returncode} detail={out[-600:]}"
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return False, f"videodl exc {e}"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-root", required=True)
    ap.add_argument("--sleep-min", type=float, default=0)
    ap.add_argument("--sleep-max", type=float, default=0)
    ap.add_argument("--no-fallback", action="store_true", help="skip videodl fallback")
    ap.add_argument("--limit", type=int, default=0, help="only process first N pending rows (for testing)")
    args = ap.parse_args()

    root = Path(args.creator_root)
    cfg = load_config(root)
    ffmpeg_bin = cfg.get("ffmpeg_bin") or ""
    if ffmpeg_bin and ffmpeg_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")
    ffprobe = str(Path(ffmpeg_bin) / "ffprobe.exe") if ffmpeg_bin else "ffprobe"

    videos = root / "videos"
    logs = root / "logs"
    work = root / "work"
    meta = root / "meta"
    videodl = Path(cfg.get("videodl_exe", ""))
    videodl_cwd = Path(cfg.get("videodl_cwd", ""))
    client = cfg.get("download_client") or "KedouVideoClient"
    sleep_min = args.sleep_min or float(cfg.get("sleep_min_sec", 8))
    sleep_max = args.sleep_max or float(cfg.get("sleep_max_sec", 15))

    lock = logs / "pipeline.lock"
    if lock.exists():
        print(f"[{now()}] lock exists: {lock.read_text(encoding='utf-8')}")
        return 2
    lock.write_text(f"{os.getpid()}\n{now()}\n", encoding="utf-8")

    queue_csv = meta / "download_queue.csv"
    if not queue_csv.exists():
        print(f"[{now()}] queue not found: {queue_csv}")
        lock.unlink(missing_ok=True)
        return 2
    rows = []
    with queue_csv.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("aweme_url") and row.get("filename"):
                rows.append(row)

    url_map = load_jsonl_urls(meta / "contents_videos.jsonl")
    print(f"[{now()}] queue={len(rows)} direct_urls={len(url_map)} fallback={'off' if args.no_fallback else 'on'} sleep={sleep_min}-{sleep_max}s")
    append_log(logs / "pipeline.log", f"DIRECT_START queue={len(rows)} direct_urls={len(url_map)} fallback={'off' if args.no_fallback else 'on'}")

    done = skipped = failed = 0
    fallback_used = 0
    attempted = 0
    for i, row in enumerate(rows, 1):
        filename = row["filename"]
        aweme_id = row.get("aweme_id", "").strip()
        url = row["aweme_url"]
        dest = videos / filename
        if dest.exists() and dest.stat().st_size > 100_000:
            skipped += 1
            append_log(logs / "download_success.log", f"SKIP\t{filename}\t{url}\talready exists")
            continue
        attempted += 1
        if args.limit and attempted > args.limit:
            break

        durl = url_map.get(aweme_id, "")
        ok, msg = False, ""
        if durl:
            ok, msg = download_direct(durl, dest, work / f"dl_{Path(filename).stem}")
        if not ok and not args.no_fallback:
            if videodl.exists():
                fallback_used += 1
                ok2, msg2 = download_videodl(videodl, videodl_cwd, client, work, url, dest)
                if ok2:
                    ok, msg = True, msg2
                else:
                    msg = f"{msg} | fallback {msg2}"
            else:
                msg = (msg or "") + " | videodl missing"
        if ok:
            done += 1
            append_log(logs / "download_success.log", f"OK\t{filename}\t{url}\t{msg}")
        else:
            failed += 1
            append_log(logs / "download_failed.log", f"FAIL\t{filename}\t{url}\t{msg}")
        if i < len(rows):
            delay = random.uniform(sleep_min, sleep_max)
            time.sleep(delay)
        if i % 25 == 0 or i == len(rows):
            print(f"[{now()}] {i}/{len(rows)} ok={done} skip={skipped} fail={failed} fallback={fallback_used}")

    append_log(logs / "pipeline.log", f"DIRECT_END download_ok={done} skip={skipped} fail={failed} fallback={fallback_used}")
    lock.unlink(missing_ok=True)
    print(f"[{now()}] finished ok={done} skip={skipped} fail={failed} fallback={fallback_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
