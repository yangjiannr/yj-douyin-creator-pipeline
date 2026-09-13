# -*- coding: utf-8 -*-
"""Build meta/download_queue.csv from MediaCrawler jsonl or a URL list."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def local_date_from_ts(ts) -> str:
    try:
        ts_i = int(ts)
        # MediaCrawler sometimes stores ms
        if ts_i > 10_000_000_000:
            ts_i //= 1000
        return datetime.fromtimestamp(ts_i).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def clean_title(title: str) -> str:
    """Normalize a Douyin title for use in a filename.

    - strip #hashtags and whitespace
    - replace Windows-illegal characters with spaces
    - collapse inner whitespace, cap length at 30 chars
    Returns '' when nothing usable remains (caller falls back to ID-only name).
    """
    import re

    if not title:
        return ""
    t = re.sub(r"#[\w\u4e00-\u9fa5]+", "", title)  # strip #hashtags
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", t)  # Windows-illegal chars
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:30].strip(" .-_")


def from_jsonl(jsonl: Path) -> list[dict]:
    rows = []
    seen = set()
    with jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            aweme_id = str(o.get("aweme_id") or "").strip()
            url = (o.get("aweme_url") or "").strip()
            if not aweme_id:
                continue
            if not url:
                url = f"https://www.douyin.com/video/{aweme_id}"
            if aweme_id in seen:
                continue
            seen.add(aweme_id)
            publish_date = local_date_from_ts(o.get("create_time"))
            title = clean_title(str(o.get("title") or ""))
            if title:
                filename = f"{publish_date}_{title}_{aweme_id}.mp4"
            else:
                filename = f"{publish_date}_{aweme_id}.mp4"
            rows.append(
                {
                    "aweme_id": aweme_id,
                    "publish_date": publish_date,
                    "filename": filename,
                    "aweme_url": url,
                }
            )
    return rows


def from_urls(urls_file: Path) -> list[dict]:
    rows = []
    seen = set()
    for line in urls_file.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        aweme_id = ""
        if "/video/" in url:
            aweme_id = url.rstrip("/").split("/video/")[-1].split("?")[0]
        if not aweme_id or aweme_id in seen:
            continue
        seen.add(aweme_id)
        publish_date = datetime.now().strftime("%Y-%m-%d")
        rows.append(
            {
                "aweme_id": aweme_id,
                "publish_date": publish_date,
                "filename": f"{publish_date}_{aweme_id}.mp4",
                "aweme_url": f"https://www.douyin.com/video/{aweme_id}",
            }
        )
    return rows


def write_outputs(meta: Path, rows: list[dict]) -> None:
    meta.mkdir(parents=True, exist_ok=True)
    queue_csv = meta / "download_queue.csv"
    urls_txt = meta / "video_urls.txt"
    urls_csv = meta / "video_urls.csv"

    with queue_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["aweme_id", "publish_date", "filename", "aweme_url"])
        w.writeheader()
        w.writerows(rows)

    urls_txt.write_text("\n".join(r["aweme_url"] for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    with urls_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["aweme_id", "publish_date", "aweme_url", "filename"])
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "aweme_id": r["aweme_id"],
                    "publish_date": r["publish_date"],
                    "aweme_url": r["aweme_url"],
                    "filename": r["filename"],
                }
            )

    print(f"wrote {len(rows)} rows -> {queue_csv}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creator-root", required=True, help="e.g. F:/downloads/许老师学习方法")
    ap.add_argument("--jsonl", default="", help="MediaCrawler contents jsonl")
    ap.add_argument("--urls", default="", help="plain URL list txt")
    args = ap.parse_args()

    root = Path(args.creator_root)
    meta = root / "meta"
    rows: list[dict] = []
    if args.jsonl:
        rows = from_jsonl(Path(args.jsonl))
    elif args.urls:
        rows = from_urls(Path(args.urls))
    else:
        # auto-detect
        cand = meta / "contents.jsonl"
        if cand.exists():
            rows = from_jsonl(cand)
        elif (meta / "video_urls.txt").exists():
            rows = from_urls(meta / "video_urls.txt")
        else:
            raise SystemExit("Provide --jsonl or --urls, or place contents.jsonl under meta/")

    write_outputs(meta, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
