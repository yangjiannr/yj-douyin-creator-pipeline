# -*- coding: utf-8 -*-
"""Targeted retry for failed Douyin downloads.

Reads failed entries from logs/download_failed.log, retries only those whose
final mp4 does not yet exist (>100KB). Multiple passes with backoff until no
improvement or MAX_PASSES reached. Shares logs with run_pipeline.py so a later
`--mode asr` pass can seed transcripts for newly downloaded videos.
"""
from __future__ import annotations

import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


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


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-root", required=True)
    ap.add_argument("--max-passes", type=int, default=6)
    ap.add_argument("--sleep-min", type=float, default=0)
    ap.add_argument("--sleep-max", type=float, default=0)
    args = ap.parse_args()

    root = Path(args.creator_root)
    cfg = load_config(root)
    ffmpeg_bin = cfg.get("ffmpeg_bin") or ""
    if ffmpeg_bin and ffmpeg_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

    videos = root / "videos"
    logs = root / "logs"
    work = root / "work"
    videodl = Path(cfg["videodl_exe"])
    videodl_cwd = Path(cfg["videodl_cwd"])
    client = cfg.get("download_client") or "KedouVideoClient"
    sleep_min = args.sleep_min or float(cfg.get("sleep_min_sec", 20))
    sleep_max = args.sleep_max or float(cfg.get("sleep_max_sec", 40))

    lock = logs / "pipeline.lock"
    if lock.exists():
        print(f"[{now()}] lock exists: {lock.read_text(encoding='utf-8')}")
        return 2
    lock.write_text(f"{os.getpid()}\n{now()}\n", encoding="utf-8")

    # Collect failed rows from log: [ts] FAIL\tfilename\turl\tdetail
    fail_log = logs / "download_failed.log"
    failed: dict[str, str] = {}  # filename -> url
    if fail_log.exists():
        for line in fail_log.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\[.*?\] FAIL\t(.+?)\t(https?://\S+)\t", line)
            if m:
                failed[m.group(1).strip()] = m.group(2).strip()
    if not failed:
        print(f"[{now()}] no failed entries in {fail_log}")
        lock.unlink(missing_ok=True)
        return 0

    print(f"[{now()}] retrying {len(failed)} failed items, max {args.max_passes} passes, sleep {sleep_min}-{sleep_max}s")
    append_log(logs / "pipeline.log", f"RETRY_START items={len(failed)} max_passes={args.max_passes}")

    done_pass = 0
    for pass_no in range(1, args.max_passes + 1):
        pending = [(fn, url) for fn, url in failed.items()
                   if not (videos / fn).exists() or (videos / fn).stat().st_size <= 100_000]
        if not pending:
            print(f"[{now()}] pass {pass_no}: nothing left to retry")
            break
        print(f"[{now()}] pass {pass_no}/{args.max_passes}: {len(pending)} pending")
        pass_ok = 0
        for i, (fn, url) in enumerate(pending, 1):
            dest = videos / fn
            if dest.exists() and dest.stat().st_size > 100_000:
                continue
            ok, msg = download_one(videodl, videodl_cwd, client, work, url, dest)
            if ok:
                pass_ok += 1
                append_log(logs / "download_success.log", f"OK\t{fn}\t{url}\t{msg} (retry pass {pass_no})")
            else:
                append_log(logs / "download_failed.log", f"FAIL\t{fn}\t{url}\t{msg} (retry pass {pass_no})")
            if i < len(pending):
                time.sleep(random.uniform(sleep_min, sleep_max))
        done_pass = pass_ok
        print(f"[{now()}] pass {pass_no} ok={pass_ok}")
        append_log(logs / "pipeline.log", f"RETRY_PASS {pass_no} ok={pass_ok} pending={len(pending)}")
        if pass_ok == 0:
            print(f"[{now()}] no improvement, stopping")
            break

    append_log(logs / "pipeline.log", f"RETRY_END last_pass_ok={done_pass}")
    lock.unlink(missing_ok=True)
    print(f"[{now()}] retry finished")
    return 0


def download_one(videodl, videodl_cwd, client, work, url, dest) -> tuple[bool, str]:
    job_dir = work / f"dl_{dest.stem}"
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    cobj = {client: {"work_dir": str(job_dir).replace("\\", "/")}}
    cmd = [str(videodl), "-i", url, "-g", "-a", client, "-c", json.dumps(cobj, ensure_ascii=False)]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(videodl_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
        found = find_downloaded_file(job_dir)
        if found and found.stat().st_size > 100_000:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest.unlink()
            shutil.move(str(found), str(dest))
            shutil.rmtree(job_dir, ignore_errors=True)
            tag = "ok" if p.returncode == 0 else "ok(recovered)"
            return True, f"{tag} size={dest.stat().st_size}"
        shutil.rmtree(job_dir, ignore_errors=True)
        return False, f"rc={p.returncode} detail={out[-1000:]}"
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return False, str(e)


if __name__ == "__main__":
    raise SystemExit(main())
