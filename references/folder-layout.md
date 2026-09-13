# Folder layout

```text
<root>/<博主名>/
├── meta/
│   ├── contents.jsonl          # MediaCrawler raw items
│   ├── video_urls.txt          # one URL per line
│   ├── video_urls.csv          # aweme_id,url,create_time,...
│   └── download_queue.csv      # aweme_id,publish_date,filename,aweme_url
├── videos/
│   └── yyyy-MM-dd_<aweme_id>.mp4
├── transcripts/
│   ├── yyyy-MM-dd_<aweme_id>.txt
│   └── yyyy-MM-dd_<aweme_id>.srt
├── logs/
│   ├── pipeline.log
│   ├── pipeline.lock
│   ├── download_success.log
│   ├── download_failed.log
│   ├── transcript_success.log
│   └── transcript_failed.log
├── scripts/                    # copied/adapted pipeline helpers
│   ├── run_pipeline.py
│   ├── transcribe_one.py
│   └── funasr_venv/            # optional local ASR venv
├── work/                       # temp download/ASR scratch
└── pipeline_config.json        # optional overrides
```

## Filename rules

- Video: `yyyy-MM-dd_<title>_<aweme_id>.mp4` (title = cleaned video title, #hashtags stripped, Windows-illegal chars replaced, ≤30 chars; falls back to `yyyy-MM-dd_<aweme_id>.mp4` when title is empty/hashtags-only) using local publish date from `create_time`
- Transcripts: same stem + `.txt` / `.srt` (SRT must have real sentence timestamps)
- Skip download if target mp4 exists and size > 100KB
- Skip transcript if both txt and srt exist and non-empty

## Viewing progress

- Results: open `transcripts/` (txt = full text, srt = timeline)
- Counts: `#videos` vs `#transcript txt`
- Live: `logs/pipeline.log` (`ASR_START`, `ASR_SEEDED`, `END`), lock PID in `pipeline.lock`
- Preview samples may use `*.funasr.txt` / `*.funasr.srt` alongside production stems — delete or ignore after compare
