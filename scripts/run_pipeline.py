# -*- coding: utf-8 -*-
"""Batch download Douyin videos + short-first transcription (config-driven).

Modes:
  all       — serial download + 1 ASR worker (default)
  download  — download only
  asr       — ASR only (seed existing videos, short first)
"""
from __future__ import annotations

import csv
import json
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "videodl_exe": r"D:\300GitHub\videodl\.venv\Scripts\videodl.exe",
    "videodl_cwd": r"D:\300GitHub\videodl",
    "download_client": "KedouVideoClient",
    "sleep_min_sec": 3.0,
    "sleep_max_sec": 8.0,
    "asr_engine": "auto",
    "whisper_model": "medium",
    "asr_python": "",
    "asr_short_first": True,
    "long_video_bytes": 80 * 1024 * 1024,
    "ffmpeg_bin": r"D:\300GitHub\ffmpeg\bin",
    "hf_endpoint": "https://hf-mirror.com",
    "hf_hub_disable_xet": True,
}


def load_config(creator_root: Path) -> dict:
    cfg = dict(DEFAULTS)
    for cand in (
        creator_root / "pipeline_config.json",
        Path(__file__).resolve().parents[1] / "config.example.json",
    ):
        if cand.exists():
            try:
                cfg.update(json.loads(cand.read_text(encoding="utf-8")))
            except Exception:
                pass
    return cfg


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{now()}] {line}\n")


def find_downloaded_file(work_dir: Path) -> Path | None:
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
    ap.add_argument("--creator-root", required=True, help="e.g. F:/downloads/许老师学习方法")
    ap.add_argument("--mode", choices=["all", "download", "asr"], default="all")
    args = ap.parse_args()

    root = Path(args.creator_root)
    cfg = load_config(root)
    mode = args.mode

    # Windows: ensure ffmpeg on PATH for videodl merge + wav extraction
    ffmpeg_bin = cfg.get("ffmpeg_bin") or ""
    if ffmpeg_bin and ffmpeg_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

    meta = root / "meta"
    videos = root / "videos"
    transcripts = root / "transcripts"
    logs = root / "logs"
    work = root / "work"
    scripts = root / "scripts"
    for d in (meta, videos, transcripts, logs, work, scripts):
        d.mkdir(parents=True, exist_ok=True)

    queue_csv = meta / "download_queue.csv"
    videodl = Path(cfg["videodl_exe"])
    videodl_cwd = Path(cfg["videodl_cwd"])
    client = cfg.get("download_client") or "KedouVideoClient"
    sleep_min = float(cfg.get("sleep_min_sec", 3))
    sleep_max = float(cfg.get("sleep_max_sec", 8))
    asr_engine = cfg.get("asr_engine") or "auto"
    whisper_model = cfg.get("whisper_model") or "medium"
    long_video_bytes = int(cfg.get("long_video_bytes") or DEFAULTS["long_video_bytes"])
    short_first = bool(cfg.get("asr_short_first", True))

    transcribe_py = scripts / "transcribe_one.py"
    if not transcribe_py.exists():
        bundled = Path(__file__).with_name("transcribe_one.py")
        if bundled.exists():
            shutil.copy2(bundled, transcribe_py)

    asr_python = cfg.get("asr_python") or ""
    if not asr_python:
        local_venv = scripts / "funasr_venv" / "Scripts" / "python.exe"
        asr_python = str(local_venv if local_venv.exists() else sys.executable)

    if mode != "asr" and not videodl.exists():
        print(f"videodl not found: {videodl}")
        return 2
    if not queue_csv.exists():
        print(f"queue not found: {queue_csv}")
        return 2

    lock = logs / "pipeline.lock"
    if lock.exists():
        try:
            old_pid = int(lock.read_text(encoding="utf-8").strip().splitlines()[0])
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                raise SystemExit(f"pipeline already running, pid={old_pid}")
        except SystemExit:
            raise
        except Exception:
            pass
    lock.write_text(f"{os.getpid()}\n{now()}\n", encoding="utf-8")

    rows = []
    with queue_csv.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("aweme_url") and row.get("filename"):
                rows.append(row)

    def target_paths(filename: str):
        stem = Path(filename).stem
        return videos / filename, transcripts / f"{stem}.txt", transcripts / f"{stem}.srt"

    def transcript_exists(txt: Path, srt: Path) -> bool:
        return txt.exists() and srt.exists() and txt.stat().st_size > 0 and srt.stat().st_size > 0

    def asr_priority(video: Path) -> int:
        size = video.stat().st_size if video.exists() else 10**15
        if not short_first:
            return size
        bucket = 1 if size >= long_video_bytes else 0
        return bucket * 10**15 + size

    def download_one(url: str, dest: Path) -> tuple[bool, str]:
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

    def transcribe_one(video: Path, txt: Path, srt: Path) -> tuple[bool, str]:
        cmd = [
            asr_python,
            str(transcribe_py),
            "--video",
            str(video),
            "--txt",
            str(txt),
            "--srt",
            str(srt),
            "--engine",
            asr_engine,
            "--whisper-model",
            whisper_model,
            "--work-dir",
            str(work / "asr"),
        ]
        env = os.environ.copy()
        hf_endpoint = cfg.get("hf_endpoint") or ""
        if hf_endpoint:
            env["HF_ENDPOINT"] = hf_endpoint
        if cfg.get("hf_hub_disable_xet", False):
            env["HF_HUB_DISABLE_XET"] = "1"
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200, env=env)
            out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if data.get("ok"):
                            return True, f"engine={data.get('engine')} segments={data.get('segments')}"
                        if data.get("no_speech"):
                            return False, f"no_speech ({data.get('error') or 'background music or silent'})"
                        return False, data.get("error") or out[-800:]
                    except Exception:
                        pass
            if p.returncode == 0 and transcript_exists(txt, srt):
                return True, "ok"
            return False, f"rc={p.returncode} detail={out[-1000:]}"
        except Exception as e:
            return False, str(e)

    pq: queue.PriorityQueue = queue.PriorityQueue()
    counter = {"n": 0}
    stop_event = threading.Event()
    do_asr = mode in ("all", "asr")
    do_dl = mode in ("all", "download")

    def enqueue_asr(row: dict) -> None:
        if not do_asr:
            return
        video, txt, srt = target_paths(row["filename"])
        if transcript_exists(txt, srt) or not video.exists():
            return
        pri = asr_priority(video)
        seq = counter["n"]
        counter["n"] += 1
        pq.put((pri, seq, row))

    def worker():
        while True:
            try:
                pri, seq, item = pq.get(timeout=1.0)
            except queue.Empty:
                if stop_event.is_set() and pq.empty():
                    break
                continue
            if item is None:
                pq.task_done()
                break
            filename = item["filename"]
            video, txt, srt = target_paths(filename)
            try:
                if transcript_exists(txt, srt):
                    append_log(logs / "transcript_success.log", f"SKIP\t{filename}\talready exists")
                else:
                    size_mb = round(video.stat().st_size / 1e6, 1) if video.exists() else -1
                    append_log(logs / "pipeline.log", f"ASR_START\t{filename}\tsize_mb={size_mb}\tpriority={pri}")
                    ok, msg = transcribe_one(video, txt, srt)
                    append_log(
                        logs / ("transcript_success.log" if ok else "transcript_failed.log"),
                        f"{'OK' if ok else 'FAIL'}\t{filename}\t{msg}\tsize_mb={size_mb}",
                    )
            except Exception as e:
                append_log(logs / "transcript_failed.log", f"FAIL\t{filename}\t{e}")
            finally:
                pq.task_done()

    t = None
    if do_asr:
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    print(f"[{now()}] queue={len(rows)} mode={mode} asr=short-first={short_first} engine={asr_engine}/{whisper_model}")
    append_log(
        logs / "pipeline.log",
        f"START queue={len(rows)} pid={os.getpid()} mode={mode} asr=short-first {asr_engine}/{whisper_model}",
    )

    # Seed ASR from existing videos
    seeded = 0
    if do_asr:
        pending = []
        for row in rows:
            video, txt, srt = target_paths(row["filename"])
            if video.exists() and video.stat().st_size > 100_000 and not transcript_exists(txt, srt):
                pending.append(row)
        pending.sort(key=lambda r: asr_priority(target_paths(r["filename"])[0]))
        for row in pending:
            enqueue_asr(row)
            seeded += 1
        append_log(logs / "pipeline.log", f"ASR_SEEDED count={seeded}")
        print(f"[{now()}] seeded ASR with {seeded} videos (short first)")

    done = skipped = failed = 0
    if do_dl:
        for i, row in enumerate(rows, 1):
            filename = row["filename"]
            url = row["aweme_url"]
            video, txt, srt = target_paths(filename)
            print(f"[{now()}] ({i}/{len(rows)}) {filename}")
            if video.exists() and video.stat().st_size > 100_000:
                skipped += 1
                append_log(logs / "download_success.log", f"SKIP\t{filename}\t{url}\talready exists")
            else:
                ok, msg = download_one(url, video)
                if ok:
                    done += 1
                    append_log(logs / "download_success.log", f"OK\t{filename}\t{url}\t{msg}")
                    enqueue_asr(row)
                else:
                    failed += 1
                    append_log(logs / "download_failed.log", f"FAIL\t{filename}\t{url}\t{msg}")
                if i < len(rows):
                    delay = random.uniform(sleep_min, sleep_max)
                    print(f"[{now()}] sleep {delay:.1f}s")
                    time.sleep(delay)

    if do_asr:
        print(f"[{now()}] downloads done ok={done} skip={skipped} fail={failed}; waiting transcripts...")
        pq.join()
        stop_event.set()
        pq.put((0, counter["n"], None))
        if t:
            t.join(timeout=30)
    else:
        print(f"[{now()}] downloads done ok={done} skip={skipped} fail={failed} (ASR skipped)")

    append_log(logs / "pipeline.log", f"END mode={mode} download_ok={done} skip={skipped} fail={failed}")
    try:
        lock.unlink(missing_ok=True)
    except Exception:
        pass
    print(f"[{now()}] all done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
