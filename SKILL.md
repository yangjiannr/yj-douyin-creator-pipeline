---
name: yj-douyin-creator-pipeline
description: >-
  End-to-end Douyin creator pipeline: MediaCrawler homepage crawl → video URL
  list → serial videodl download → parallel FunASR/Whisper transcription to
  .txt+.srt under F:/downloads/<博主名>/. Use whenever the user asks to 抓抖音博主
  全部视频、批量下载抖音、MediaCrawler 抖音主页、videodl 串行下载、视频转文字/
  转写口播文案、FunASR/Whisper 字幕、短视频优先转写、暂停下载只转录、续跑失败下载、
  导出 aweme 链接列表、或提到 yj-douyin-creator-pipeline, even if they only
  provide a Douyin user URL or an existing URL list.
---

# yj-douyin-creator-pipeline

把「抖音博主主页 → 作品列表 → 下载 → 转写」做成可续跑流水线。

两种入口都要支持：

| 模式 | 用户给什么 | 做什么 |
|------|------------|--------|
| `creator` | 博主主页 URL / `sec_user_id` + 博主名 | MediaCrawler 抓列表 → 下载 → 转写 |
| `queue` | 已有 `download_queue.csv` 或 URL 列表 | 跳过抓取，直接下载 → 转写 |

## Always do first

1. Read `references/defaults.md` and `references/folder-layout.md`. For ASR details, read `references/asr.md`.
2. Resolve root directory:
   - If skill `.last_root` exists, ask: 「上次根目录是 `<path>`，还用这个吗？」
   - If user says no / wants another path, take their absolute path.
   - Write the chosen root to `.last_root`.
3. Confirm creator folder name (e.g. `许老师学习方法`) → `<root>/<博主名>/`.
4. Confirm mode: `creator` or `queue`.
5. Do **not** start long downloads until the user confirms the plan (or explicitly says 开始/继续全量).
6. Before full pipeline, offer a **quality preview**: pause download, FunASR 转 1–3 个最短已下载视频，让用户看 `.txt`/`.srt` 效果再开全量。

## Defaults (configurable)

Copy `config.example.json` → `<creator_root>/pipeline_config.json` and edit paths.

Critical defaults from this machine (override per project):

- `root_downloads`: `F:/downloads`
- `mediacrawler_dir`: `D:/300GitHub/MediaCrawler`
- `videodl_exe`: `D:/300GitHub/videodl/.venv/Scripts/videodl.exe`
- `ffmpeg_bin`: `D:/300GitHub/ffmpeg/bin` (自动注入 PATH；videodl 合并音视频与 ffmpeg 抽音轨必需)
- Whisper 兜底走镜像: `hf_endpoint: https://hf-mirror.com` + `hf_hub_disable_xet: true` (HF 新 xet 存储直连会 401/超时)
- Download client: `KedouVideoClient`
- Sleep between downloads: random **3–8s**
- ASR: **FunASR primary** (`asr_engine=auto`), Whisper `medium` fallback
- ASR order: **short videos first** (`long_video_bytes` ≈ 80MB deferred)
- Outputs: both `.txt` and `.srt` with real sentence timestamps

Why FunASR first: Chinese 口播 accuracy is usually better. Whisper wins on English / heavy EN-ZH mix. Always keep both paths.

## Environment setup (first run)

One-time install on a fresh machine:

1. **Python 3.9–3.12** — MediaCrawler 不兼容 3.13+；用官方 python.org 安装包（如 3.12.10，3.12.11+ 无 Windows 安装包）
2. **MediaCrawler** — `git clone https://github.com/NanmiCoder/MediaCrawler`，建 venv 装 requirements，装 Playwright Chromium
3. **videodl** — `pip install videofetch`（CLI 名 `videodl`，来自 CharlesPikachu/videodl）
   ⚠️ 用官方 PyPI `https://pypi.org/simple`；清华源等镜像对部分包（videofetch 等）返回 403
4. **FFmpeg** — 解压到固定目录（如 `D:\300GitHub\ffmpeg\bin`），在 `pipeline_config.json` 设 `ffmpeg_bin`
5. **ASR 环境** — FunASR（首选，ModelScope 下载模型）或 faster-whisper（兜底，HF 镜像下载）

MediaCrawler 抓抖音需改两处配置（参考本次实战）：

- `config/base_config.py`：`PLATFORM="dy"`、`CRAWLER_TYPE="creator"`、`ENABLE_GET_COMMENTS=False`、
  `CRAWLER_MAX_NOTES_COUNT=99999`、`CDP_CONNECT_EXISTING=False`
- `config/dy_config.py`：`DY_CREATOR_ID_LIST` 填入博主主页 URL 或 `sec_user_id`

抓取后 jsonl 可能含重复条目（同 aweme_id），`init_queue.py` 负责去重。

## Folder contract

Create and keep this layout (see `references/folder-layout.md`):

```text
<root>/<博主名>/
  meta/ videos/ transcripts/ logs/ scripts/ work/
```

Video name: `yyyy-MM-dd_<aweme_id>.mp4`  
Transcripts: same stem `.txt` + `.srt`  
Logs split by stage/result — never dump everything into one folder.

## Workflow A — creator homepage

1. Ensure MediaCrawler can login to Douyin (CDP/Playwright; QR if needed).
2. Run creator crawl for the given `sec_user_id` / user URL:
   - platform `dy`, type `creator`
   - comments off unless user asks
3. Export into `meta/`:
   - `contents.jsonl`
   - `video_urls.txt` / `video_urls.csv`
   - `download_queue.csv` with columns: `aweme_id,publish_date,filename,aweme_url`
4. `publish_date` = local date from MediaCrawler `create_time`.
5. Copy pipeline scripts from this skill into `<creator_root>/scripts/` if missing (always refresh `transcribe_one.py` when skill ASR logic changes).
6. Start `run_pipeline.py --creator-root <path>` (single instance; lock file required).

## Workflow B — existing queue / URL list

1. If user has MediaCrawler jsonl / URL list, build or refresh `meta/download_queue.csv`.
2. Skip crawl.
3. Run the same download + transcript pipeline.

## Download rules

- Serial only (one videodl job at a time).
- Client: `KedouVideoClient` via videodl (`-g -a KedouVideoClient`).
- After each download attempt, sleep random 3–8 seconds.
- Skip if target mp4 already exists (>100KB).
- Download into `work/dl_<stem>/`, then move/rename to `videos/<filename>`.
- Prefer final videodl output over temp **hash-named** merge files (32-hex stems are usually intermediate).
- Append every OK/SKIP/FAIL to `logs/download_success.log` or `download_failed.log`.
- Download is mostly network/disk; Whisper/FunASR is the CPU hog. If the machine is busy, use `--mode download` or `--mode asr` instead of both.

## Transcription rules

- One transcript worker while downloads continue (unless `--mode download`).
- After each successful download, enqueue ASR. On start, **seed** already-downloaded videos missing transcripts.
- **Short-first**: PriorityQueue by file size; files ≥ `long_video_bytes` (default 80MB) go to the long bucket and wait until shorts finish. Long videos otherwise block the queue for a long time.
- Prefer FunASR; on failure/unavailable → Whisper (`whisper_model`, default `medium`).
- **FunASR timestamps are mandatory for usable SRT**:
  - Call `generate(..., sentence_timestamp=True)` so `sentence_info` has start/end ms.
  - Without this flag, FunASR often returns only char-level `timestamp` and the old code wrote one fake cue `00:00:00 → 00:00:01` for the whole video.
  - Fallback: split text on `。！？；` using char-level timestamps if `sentence_info` is missing.
- **No-speech detection**: whisper(vad) 结果为空时自动无 vad 复检；若内容只出现在无 vad 模式且疑似背景音乐（词曲/演唱 署名、或 1–2 句短重复），标记 `no_speech` 而非失败或产出垃圾字幕。
- 纯字幕/图文配乐视频（无口播）会记入 `transcript_failed.log` 为 `FAIL ... no_speech` —— 这是预期结果，不是错误；可在 transcripts/ 写说明文件标注。
- Write both `.txt` (plain lines) and `.srt` (timed). Reject “success” if SRT has only 1 cue spanning ~1s for a multi-minute video.
- Skip if both transcript files already exist and non-empty.
- Log to `transcript_success.log` / `transcript_failed.log`; log `ASR_START` / `ASR_SEEDED` in `pipeline.log`.

## Pipeline modes

| Flag | Behavior |
|------|----------|
| `--mode all` (default) | Serial download + 1 ASR worker |
| `--mode download` | Download only (skip ASR enqueue) |
| `--mode asr` | No new downloads; seed existing videos short-first and transcribe |

Quality check pattern users liked: stop download → `--mode asr` on a few shorts (or run `transcribe_one.py` on named files) → review → resume `--mode all` / `--mode download`. Resume is safe because existing mp4/txt/srt are skipped.

## Concurrency & resume

- Use `logs/pipeline.lock` with PID; refuse a second concurrent pipeline.
- Before starting, ensure no orphan `run_pipeline.py` / `videodl` / duplicate `transcribe_one.py` for the same creator root.
- Resume = re-run the same pipeline: existing videos/transcripts are skipped.
- Failed rows stay in logs for targeted retry.

## Scripts bundled here

| Script | Role |
|--------|------|
| `scripts/init_queue.py` | Build `download_queue.csv` from jsonl/urls |
| `scripts/run_pipeline.py` | Config-driven serial download + short-first ASR |
| `scripts/transcribe_one.py` | One video → txt+srt (FunASR with sentence timestamps / Whisper) |

Prefer these scripts over ad-hoc one-off commands so behavior stays consistent. When deploying into a creator folder, overwrite `scripts/transcribe_one.py` from the skill so timestamp fixes ship.

## Progress reporting

While running, report:

- downloaded count / queue size
- transcript count (txt/srt pairs)
- recent FAIL lines
- lock PID alive or not
- whether current ASR is a short or long file (`ASR_START … size_mb=`)

How to view results: open `<creator_root>/transcripts/` (`.txt` full text, `.srt` timeline). Do not claim “done” until pipeline END is logged or the process exits cleanly.

## Safety / product notes

- Personal/non-commercial learning use; respect platform ToS and rate limits.
- MediaCrawler teaching builds may mask nickname/uid — ask user for the real folder display name.
- Signed `video_download_url` from crawl may expire; always re-resolve via videodl URL.
- KedouVideoClient 单视频解析约 171s（属正常慢，勿中途杀进程误判卡死）。
- 抓取的 `video_download_url` 直链直接下载通常 403（缺完整登录 cookie），必须走 videodl 重解析。
- 抖音博主昵称可能被平台掩码（如「张***题」），文件夹名用用户确认的真实名。
- FunASR models download from ModelScope (often already fast in CN). TUNA Hugging Face mirror helps Whisper/HF, not ModelScope.
