# Defaults & Configuration

## Config resolution order

1. Project file: `<creator_root>/pipeline_config.json`
2. Skill defaults: `config.example.json` values (copied/adapted)
3. Last-used root: skill-local `.last_root` (one line absolute path)

## Required keys

| Key | Meaning |
|-----|---------|
| `root_downloads` | Parent of all creator folders, e.g. `F:/downloads` |
| `mediacrawler_dir` | MediaCrawler repo path |
| `videodl_exe` | Path to `videodl.exe` |
| `videodl_cwd` | Working directory when invoking videodl |
| `asr_python` | Python with FunASR (preferred) or faster-whisper; empty = auto-detect under `<creator_root>/scripts/funasr_venv` |
| `download_client` | Default `KedouVideoClient` |
| `ffmpeg_bin` | FFmpeg bin dir (e.g. `D:/300GitHub/ffmpeg/bin`); auto-prepended to PATH (videodl merge + wav extraction need it) |
| `hf_endpoint` | HF mirror for whisper fallback, default `https://hf-mirror.com` |
| `hf_hub_disable_xet` | `true` to bypass HF new xet storage (CAS 401), default `true` |
| `sleep_min_sec` / `sleep_max_sec` | Gap between downloads |
| `asr_engine` | `auto` \| `funasr` \| `whisper` (default `auto`) |
| `whisper_model` | Fallback model, default `medium` |
| `long_video_bytes` | ASR defer threshold; default `83886080` (80MB). Larger files wait until shorts finish |
| `asr_short_first` | Default `true` |

## Asking for root directory

Before any run:

1. Read skill `.last_root` if present.
2. Ask user: "上次根目录是 `<path>`，还用这个吗？"
3. If no / different → user provides new absolute root; write it to `.last_root`.
4. Creator folder is always: `<root>/<博主名>/`

## Tool prerequisites

- Python 3.9–3.12 (MediaCrawler breaks on 3.13+; official python.org installers only)
- MediaCrawler: Python venv ready; Douyin login cache may be required
- videodl: `pip install videofetch` (CLI `videodl`); use official PyPI — mirrors (TUNA) 403 on some packages; FFmpeg on PATH via `ffmpeg_bin`
- ASR: FunASR (+ torch) preferred; faster-whisper as fallback
- First FunASR run downloads ModelScope models (~2GB total). CN direct is often 30MB/s+; TUNA Hugging Face mirror does **not** replace ModelScope

## Run modes

```text
python run_pipeline.py --creator-root <path> --mode all       # download + ASR
python run_pipeline.py --creator-root <path> --mode download  # download only
python run_pipeline.py --creator-root <path> --mode asr       # ASR only (short-first)
```

## CPU / resource note

- Download ≈ network + disk
- FunASR / Whisper ≈ heavy CPU
- If the machine is busy: stop pipeline → `--mode asr` on a few shorts for quality check → resume `--mode download` or `all`
