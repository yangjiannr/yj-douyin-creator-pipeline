# yj-douyin-creator-pipeline

抖音博主作品「抓取 → 下载 → 转写」全自动流水线 Skill：从博主主页抓取作品元数据，串行下载全部视频，并用 FunASR/Whisper 转写成带时间轴的 `.txt` + `.srt` 字幕。

```
博主主页 URL / sec_user_id
        │
        ▼
MediaCrawler 抓取作品列表 (jsonl)
        │
        ▼
init_queue.py 去重 → download_queue.csv (73 条)
        │
        ▼
run_pipeline.py 串行下载 (videodl/KedouVideoClient)
        │
        ▼
短视频优先 → FunASR 转写 (Whisper medium 兜底)
        │
        ▼
transcripts/ *.txt (全文) + *.srt (时间轴)
```

## 功能特性

- **双入口**：`creator`（博主主页 URL）与 `queue`（已有队列/URL 列表）两种模式
- **可续跑**：已下载/已转写的文件自动跳过，中断后重跑即可续传
- **短视频优先**：大文件（默认 >80MB）延迟处理，避免阻塞队列
- **双引擎转写**：FunASR 主用（中文口播准确），Whisper medium 兜底（英文/混语）
- **无口播识别**：纯字幕/图文配乐视频自动标记 `no_speech`，不产出垃圾字幕
- **真实时间轴**：SRT 使用 FunASR `sentence_timestamp` 分句时间戳，非整片单条假时间轴

## 环境安装（首次运行）

| 依赖 | 要求 |
|------|------|
| Python | **3.9–3.12**（MediaCrawler 不兼容 3.13+；官方 python.org 安装包，如 3.12.10） |
| MediaCrawler | `git clone https://github.com/NanmiCoder/MediaCrawler`，venv 装依赖 + Playwright Chromium |
| videodl | `pip install videofetch`（CLI 名 `videodl`，来自 CharlesPikachu/videodl） |
| FFmpeg | 解压到固定目录（如 `D:\300GitHub\ffmpeg\bin`），配置 `ffmpeg_bin` |
| ASR 环境 | FunASR（首选，ModelScope 下载模型）或 faster-whisper（兜底，HF 镜像下载） |

> ⚠️ 安装提示
> - pip 请使用官方源 `https://pypi.org/simple`；清华等镜像对部分包（videofetch 等）返回 403
> - Whisper 模型下载走 HF 镜像（见下文配置），HF 新 xet 存储需禁用

### MediaCrawler 抓抖音配置

改 `config/base_config.py`：

```python
PLATFORM = "dy"
CRAWLER_TYPE = "creator"
ENABLE_GET_COMMENTS = False
CRAWLER_MAX_NOTES_COUNT = 99999
CDP_CONNECT_EXISTING = False
```

改 `config/dy_config.py`：

```python
DY_CREATOR_ID_LIST = ["https://www.douyin.com/user/<sec_user_id>"]
```

抓取需要登录抖音（CDP/Playwright + 手机扫码）。

## 配置（pipeline_config.json）

复制 `config.example.json` → `<创作目录>/pipeline_config.json` 并修改：

| 键 | 说明 |
|----|------|
| `root_downloads` | 所有博主文件夹的父目录，如 `F:/downloads` |
| `mediacrawler_dir` | MediaCrawler 仓库路径 |
| `videodl_exe` / `videodl_cwd` | videodl 可执行文件路径与工作目录 |
| `ffmpeg_bin` | FFmpeg bin 目录（自动注入 PATH，videodl 合并音视频必需） |
| `asr_python` | FunASR/faster-whisper 所在 Python（留空自动探测） |
| `download_client` | 默认 `KedouVideoClient` |
| `asr_engine` | `auto` \| `funasr` \| `whisper` |
| `whisper_model` | 兜底模型，默认 `medium` |
| `hf_endpoint` | HF 镜像，默认 `https://hf-mirror.com` |
| `hf_hub_disable_xet` | 禁用 HF 新 xet 存储（CAS 401 问题），默认 `true` |
| `long_video_bytes` | ASR 延迟阈值，默认 80MB |
| `sleep_min_sec` / `sleep_max_sec` | 下载间隔（随机），默认 3–8s |

## 使用方法

### 方式 A：博主主页抓取

```
python run_pipeline.py --creator-root F:/downloads/张老师不讲题 --mode all
```

### 方式 B：已有队列/URL 列表

先用 `init_queue.py` 从 MediaCrawler jsonl 重建队列，再跑同一流水线。

### 运行模式

| 参数 | 行为 |
|------|------|
| `--mode all`（默认） | 串行下载 + 1 个 ASR 转写 worker |
| `--mode download` | 仅下载（跳过转写） |
| `--mode asr` | 不下载，对已下载视频短视频优先补转写 |

> 建议：先下载少量视频 → `--mode asr` 转 1–3 个最短的预览质量 → 确认后再全量。

## 输出结构

```
<root>/<博主名>/
├── meta/
│   └── download_queue.csv      # aweme_id,publish_date,filename,aweme_url
├── videos/                     # yyyy-MM-dd_<aweme_id>.mp4
├── transcripts/                # 同名 .txt（全文）+ .srt（时间轴）
├── logs/
│   ├── pipeline.log            # START / ASR_START / END
│   ├── download_success.log / download_failed.log
│   └── transcript_success.log / transcript_failed.log
├── scripts/                    # 从本 Skill 拷贝的流水线脚本
├── work/                       # 临时下载/转写目录
└── pipeline_config.json
```

## 已知限制（实战验证）

- **Kedou 解析慢**：KedouVideoClient 单视频解析约 **171s**，属正常现象，勿中途杀进程误判卡死
- **直链不可用**：抓取到的 `video_download_url` 直接下载通常 403（缺完整登录 cookie），必须走 videodl 重解析
- **jsonl 重复**：MediaCrawler 抓取结果可能含重复条目（同 aweme_id），由 `init_queue.py` 去重
- **无口播视频**：纯字幕/图文配乐视频（无口播）会标记为 `no_speech` 并记入 `transcript_failed.log`，属预期而非错误
- **博主昵称掩码**：平台可能掩码昵称（如「张***题」），文件夹名请用用户确认的真实名

## 免责声明

本项目仅供个人学习与研究使用，请遵守抖音平台服务条款，控制抓取频率，勿用于商业用途。
