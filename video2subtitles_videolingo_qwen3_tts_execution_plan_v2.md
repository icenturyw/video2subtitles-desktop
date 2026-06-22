# Video2Subtitles × VideoLingo 桌面端整合开发计划

> 适用仓库：`https://github.com/icenturyw/video2subtitles-desktop`  
> 参考项目：`https://github.com/Huanshere/VideoLingo`  
> 计划用途：直接复制给 DeepSeek V4 Flash、Codex 或其他代码智能体执行  
> 制定日期：2026-06-13  
> 目标平台：Windows 优先，同时保持 Linux/macOS 核心处理能力  
> 当前桌面技术栈：Python 3.10+、PyQt5、FastAPI、faster-whisper、yt-dlp、FFmpeg  
> VideoLingo 参考基线：主分支，GitHub 当前显示最新 release 为 v3.0.1（2026-02-28），Apache-2.0

---

# 0. 给执行智能体的总指令

你正在维护仓库：

```text
video2subtitles-desktop
```

你的任务是在**不破坏现有视频下载、Whisper 转写、字幕导出、历史记录和 ChatGPT 分析包功能**的前提下，把项目逐步升级为：

```text
下载/导入视频
→ 语音识别
→ 字幕语义切分
→ 指定目标语言翻译
→ 生成原文/译文/双语字幕
→ 烧录指定语言字幕
→ 可选生成指定语言配音
→ 输出完整本地化视频
```

必须遵守以下原则：

1. **不要直接复制 VideoLingo 的 Streamlit 界面。**
2. **不要把 VideoLingo 整个仓库直接嵌入当前项目。**
3. 只提取、重构或借鉴其：
   - WhisperX 单词级时间轴；
   - NLP/LLM 字幕切分；
   - 翻译、反思、润色；
   - 术语表；
   - 字幕对齐；
   - TTS 与配音时长适配；
   - FFmpeg 合成流程。
4. 当前 PyQt5 程序继续作为唯一桌面界面。
5. 现有 `whisper-server/` 继续作为轻量 ASR 与下载服务，默认端口 `8765`。
6. 新增独立的 `localization-engine/`，默认端口 `8766`，负责翻译、字幕渲染、WhisperX 和配音。
7. 高级组件必须按需安装，不能让基础字幕功能强制依赖 WhisperX、PyTorch、spaCy、声源分离或大型 TTS。
8. 不允许使用全局固定 `output/` 处理所有任务；每个任务必须有独立项目目录。
9. 所有外部命令必须使用参数数组调用，禁止 `shell=True`。
10. Windows 路径、中文路径、空格路径必须经过测试。
11. 不允许把 API Key、Cookie、令牌或用户隐私数据写入仓库、日志和 `manifest.json`。
12. 每个阶段完成后：
    - 更新 README；
    - 补充或更新测试；
    - 运行验证命令；
    - 使用中文 commit 消息提交；
    - 汇报修改文件、测试结果、遗留问题。
13. 不要一次性重写 `main_window.py`。早期通过新增模块和小范围接入降低风险；待功能稳定后再拆分巨型文件。
14. 遇到不明确细节时，优先选择向后兼容、可测试、可回滚的实现，不要停下来等待确认。
15. 每次写入前读取文件当前内容，避免覆盖用户已有改动。
16. 不要修改或提交：
    - `.env`
    - Cookie 文件
    - 模型文件
    - 输出视频
    - `.cache/`
    - 临时音频
    - 第三方虚拟环境
17. 当前仓库已有多个 `*_patch.py`。新增功能可以先通过明确的独立模块接入，但禁止继续无限堆叠难以追踪的猴子补丁。
18. 所有任务应在独立功能分支中完成，禁止直接在 `master/main` 开发。

---

# 1. 项目现状摘要

当前项目已经具备：

- PyQt5 桌面界面；
- 本地文件和在线视频链接；
- yt-dlp 下载；
- YouTube 播放列表展开；
- faster-whisper 本地识别；
- FastAPI 本地 sidecar；
- NVIDIA GPU 自动检测；
- SRT、VTT、TXT 输出；
- 双语字幕字段基础展示；
- 历史记录；
- 任务进度和错误日志；
- 独立输出目录；
- `manifest.json`；
- ChatGPT 分析包；
- Windows 后台进程隐藏；
- 模型安装和环境检查。

当前主要结构：

```text
video2subtitles-desktop/
├── app.py
├── main_window.py
├── api_client.py
├── local_whisper.py
├── client_settings.py
├── diagnostics.py
├── output_manifest.py
├── subtitle_utils.py
├── history.py
├── *_patch.py
├── whisper-server/
│   ├── main.py
│   └── requirements.txt
├── tests/
└── tools/check_project.py
```

当前风险：

- `main_window.py` 体积较大，包含 UI、线程、输出、FFmpeg 打包等多种职责；
- 补丁模块较多，后续继续猴子补丁会增加维护成本；
- `WorkerThread` 返回值主要是字幕和语言，媒体文件、任务产物缺少统一模型；
- `whisper-server/main.py` 同时承担下载和 ASR，但任务模型仍偏简单；
- 当前 `manifest.json` 主要描述基础字幕输出，尚未描述多语言字幕、渲染视频和配音音轨；
- 高级翻译和 TTS 依赖不能与基础客户端混装。

---

# 2. 最终产品目标

桌面端提供三种处理模式。

## 2.1 快速字幕模式

```text
本地视频/在线视频
→ 下载或读取
→ faster-whisper
→ SRT/VTT/TXT
```

要求：

- 保留现有行为；
- 不要求 LLM；
- 不要求高级引擎；
- 高级引擎安装失败时仍可使用。

## 2.2 翻译字幕成片模式

```text
下载/读取
→ ASR
→ 字幕清洗和切分
→ 翻译
→ 原文/译文/双语 SRT 与 ASS
→ FFmpeg 烧录
→ 输出成片
```

第一版必须支持：

- 自动识别源语言；
- 选择目标语言；
- 原文字幕；
- 译文字幕；
- 双语字幕；
- 软字幕封装；
- 硬字幕烧录；
- 字体、字号、描边、阴影、位置和安全边距；
- 预设字幕样式；
- OpenAI-compatible 翻译 API；
- 术语表；
- 失败重试；
- 断点续跑。

## 2.3 指定语言配音成片模式

```text
下载/读取
→ ASR
→ 翻译
→ TTS
→ 句级时长适配
→ 原声/背景音混合
→ 字幕烧录
→ 配音成片
```

逐步支持：

1. **Qwen3-TTS 本地引擎（主要方案）**；
2. Edge-TTS（无 GPU、未安装模型时的轻量回退）；
3. OpenAI-compatible TTS；
4. Azure TTS；
5. 自定义 TTS HTTP 接口；
6. 后续可选 Fish Speech、GPT-SoVITS、CosyVoice 等。

---

# 3. 架构决策

## 3.1 服务划分

```text
PyQt5 Desktop
├── 任务和项目管理
├── 设置与环境检查
├── 字幕预览和编辑
├── 进度、取消、重试
└── 历史记录和产物浏览
        │
        ├── Whisper Server :8765
        │   ├── yt-dlp 下载
        │   ├── faster-whisper
        │   └── 基础字幕结果
        │
        └── Localization Engine :8766
            ├── 字幕标准化
            ├── 翻译
            ├── 术语表
            ├── ASS/SRT 生成
            ├── FFmpeg 渲染
            ├── 可选 WhisperX
            ├── TTS Provider 调度
            └── 音视频合成
                    │
                    └── Qwen3-TTS Sidecar :8767
                        ├── 0.6B/1.7B 模型管理
                        ├── 预设音色
                        ├── Voice Design
                        ├── Voice Clone
                        ├── 批量语音生成
                        └── GPU 模型生命周期管理
```

## 3.2 为什么新建独立高级引擎

- 避免 PyTorch/WhisperX 与现有 faster-whisper 环境冲突；
- 高级功能可按需安装；
- 桌面程序即使没有高级引擎仍可工作；
- 将来可以把高级引擎放到远程 GPU 服务器；
- 可单独升级 VideoLingo 派生逻辑；
- 便于实现任务恢复和 API 测试；
- 便于打包基础版和完整版。

## 3.3 不采用的方案

禁止：

```text
PyQt5 → 直接启动 Streamlit → iframe/浏览器展示
```

禁止：

```text
把 VideoLingo 整个 core 目录复制过来后直接调用其全局 output 文件
```

禁止：

```text
在 main_window.py 中继续直接堆积翻译、TTS、FFmpeg 命令
```

---

# 4. 建议目录结构

目标目录：

```text
video2subtitles-desktop/
├── app.py
├── main_window.py
├── api_client.py
├── localization_client.py
├── job_models.py
├── project_workspace.py
├── pipeline_types.py
├── subtitle_utils.py
├── subtitle_ass.py
├── output_manifest.py
├── client_settings.py
├── diagnostics.py
├── ui/
│   ├── localization_dialog.py
│   ├── subtitle_style_dialog.py
│   ├── subtitle_editor.py
│   ├── engine_install_dialog.py
│   └── task_detail_dialog.py
├── services/
│   ├── process_runner.py
│   ├── ffmpeg_service.py
│   ├── font_service.py
│   └── artifact_service.py
├── whisper-server/
│   └── ...
├── localization-engine/
│   ├── main.py
│   ├── requirements-base.txt
│   ├── requirements-whisperx.txt
│   ├── requirements-tts.txt
│   ├── engine/
│   │   ├── models.py
│   │   ├── task_store.py
│   │   ├── pipeline.py
│   │   ├── cancellation.py
│   │   ├── progress.py
│   │   └── workspace.py
│   ├── translation/
│   │   ├── base.py
│   │   ├── openai_compatible.py
│   │   ├── batching.py
│   │   ├── glossary.py
│   │   ├── prompts.py
│   │   └── response_parser.py
│   ├── subtitles/
│   │   ├── normalize.py
│   │   ├── segment.py
│   │   ├── align.py
│   │   ├── srt_writer.py
│   │   ├── ass_writer.py
│   │   └── validate.py
│   ├── rendering/
│   │   ├── ffmpeg.py
│   │   ├── filters.py
│   │   └── presets.py
│   ├── asr/
│   │   ├── base.py
│   │   └── whisperx_adapter.py
│   ├── tts/
│   │   ├── base.py
│   │   ├── edge_tts.py
│   │   ├── openai_compatible.py
│   │   ├── custom_http.py
│   │   └── timing.py
│   └── audio/
│       ├── mix.py
│       ├── normalize.py
│       └── separation.py
├── qwen3-tts-engine/
│   ├── main.py
│   ├── requirements.txt
│   ├── engine/
│   │   ├── model_manager.py
│   │   ├── schemas.py
│   │   ├── synthesis.py
│   │   ├── voice_clone.py
│   │   ├── voice_design.py
│   │   ├── cache.py
│   │   └── device.py
│   ├── models/              # 不提交，按需下载
│   ├── voices/              # 用户自建音色提示，不提交隐私音频
│   └── tests/
│       ├── test_schemas.py
│       ├── test_cache.py
│       └── test_health.py
├── licenses/
│   ├── VideoLingo-Apache-2.0.txt
│   └── Qwen3-TTS-Apache-2.0.txt
├── THIRD_PARTY_NOTICES.md
├── docs/
│   ├── LOCALIZATION_ENGINE.md
│   ├── API.md
│   └── PACKAGING.md
└── tests/
    ├── test_job_models.py
    ├── test_workspace.py
    ├── test_manifest_v2.py
    ├── test_translation_parser.py
    ├── test_glossary.py
    ├── test_ass_writer.py
    ├── test_ffmpeg_filters.py
    └── test_localization_api.py
```

注意：

- 不要求第一阶段一次创建所有文件；
- 只在相关功能落地时创建对应目录；
- `ui/` 的拆分可稍后进行；
- 不能为了“目录看起来漂亮”而进行无功能价值的大规模移动。

---

# 5. 核心数据模型

优先使用 `dataclass` 或 Pydantic，避免在各层传递无约束字典。

## 5.1 JobSpec

```python
class JobSpec:
    job_id: str
    source: str
    source_type: Literal["local", "url"]
    mode: Literal["subtitle", "translate", "dub"]
    source_language: str
    target_language: str | None
    subtitle_mode: Literal["source", "translated", "bilingual"]
    burn_subtitles: bool
    embed_soft_subtitles: bool
    dubbing_enabled: bool
    translation_provider: str | None
    tts_provider: str | None
    subtitle_style: SubtitleStyle
    workspace_dir: str
```

## 5.2 SubtitleSegment

统一字段：

```python
class SubtitleSegment:
    index: int
    start: float
    end: float
    text: str
    translation: str = ""
    speaker: str | None = None
    words: list[WordTiming] = []
    metadata: dict = {}
```

约束：

- `start >= 0`
- `end > start`
- 时间轴不能倒序；
- 文本必须去除首尾空白；
- 原文和译文不能写进同一个不可解析字符串；
- 保留扩展字段兼容 WhisperX。

## 5.3 PipelineStage

```text
prepare
download
transcribe
segment
translate
subtitle_export
tts
audio_mix
render
finalize
completed
error
cancelled
```

桌面端可以把这些映射为中文：

```text
准备
下载
语音识别
字幕切分
翻译
字幕生成
语音合成
音频混合
视频渲染
整理产物
完成
失败
已取消
```

## 5.4 Artifact

```python
class Artifact:
    kind: Literal[
        "source_video",
        "source_audio",
        "source_srt",
        "translated_srt",
        "bilingual_srt",
        "source_ass",
        "translated_ass",
        "bilingual_ass",
        "softsub_video",
        "burned_video",
        "tts_audio",
        "dubbed_video",
        "log",
    ]
    path: str
    language: str | None
    created_at: str
    size_bytes: int
```

## 5.5 TaskResult

```python
class TaskResult:
    job_id: str
    status: str
    stage: str
    progress: int
    message: str
    detected_language: str
    segments: list[SubtitleSegment]
    artifacts: list[Artifact]
    error_code: str | None
    error_detail: str | None
```

---

# 6. 工作目录规范

每个任务必须生成独立目录：

```text
output/
└── <safe-title>__<job-id-short>/
    ├── manifest.json
    ├── source/
    │   ├── video.mp4
    │   └── audio.wav
    ├── subtitles/
    │   ├── source.srt
    │   ├── source.ass
    │   ├── zh-CN.srt
    │   ├── zh-CN.ass
    │   ├── bilingual_zh-CN.srt
    │   └── bilingual_zh-CN.ass
    ├── translation/
    │   ├── glossary.json
    │   ├── segments.json
    │   ├── batches/
    │   └── checkpoints/
    ├── audio/
    │   ├── tts/
    │   ├── dubbed.wav
    │   └── mixed.wav
    ├── rendered/
    │   ├── translated_subtitles.mp4
    │   ├── bilingual_subtitles.mp4
    │   └── dubbed_zh-CN.mp4
    └── logs/
        ├── localization.log
        └── ffmpeg.log
```

要求：

- `job_id` 使用 UUID；
- 标题只用于可读性，不能作为任务唯一标识；
- 所有路径写入 manifest 时尽量使用相对路径；
- 禁止任务之间共享可写的临时文件；
- 缓存必须根据源文件指纹、语言和模型参数区分；
- 删除任务时不能误删输出根目录；
- 重跑某一步时只清理该步骤产物。

---

# 7. Manifest v2 设计

扩展当前 `manifest.json`，保持旧字段兼容。

建议结构：

```json
{
  "schema_version": 2,
  "job_id": "uuid",
  "title": "video title",
  "source": {
    "input": "url or local path",
    "type": "url",
    "video_file": "source/video.mp4",
    "sha256": "",
    "duration_seconds": 123.45
  },
  "pipeline": {
    "mode": "translate",
    "status": "completed",
    "current_stage": "completed",
    "source_language": "en",
    "detected_language": "en",
    "target_language": "zh-CN",
    "subtitle_mode": "bilingual",
    "burn_subtitles": true,
    "dubbing_enabled": false
  },
  "settings": {
    "asr_engine": "faster-whisper",
    "translation_provider": "openai_compatible",
    "translation_model": "model-name",
    "tts_provider": "",
    "subtitle_style_preset": "default"
  },
  "artifacts": [
    {
      "kind": "source_srt",
      "path": "subtitles/source.srt",
      "language": "en"
    },
    {
      "kind": "translated_srt",
      "path": "subtitles/zh-CN.srt",
      "language": "zh-CN"
    },
    {
      "kind": "burned_video",
      "path": "rendered/bilingual_subtitles.mp4",
      "language": "zh-CN"
    }
  ],
  "checkpoints": {
    "transcribe": true,
    "translate": true,
    "render": true
  },
  "created_at": "",
  "updated_at": ""
}
```

兼容规则：

- 保留旧的 `video_file`、`srt_file`、`vtt_file`、`txt_file`；
- 新代码优先读取 v2；
- 旧历史记录仍能打开；
- v1 升级为 v2 时不能移动用户文件；
- 不存储 API Key；
- 可存储 provider 和 model 名称，但不能存储认证信息。

---

# 8. Localization Engine API

默认：

```text
http://127.0.0.1:8766
```

## 8.1 健康检查

```http
GET /health
```

返回：

```json
{
  "status": "ok",
  "service": "video2subtitles-localization-engine",
  "version": "0.1.0",
  "capabilities": {
    "translation": true,
    "rendering": true,
    "whisperx": false,
    "tts": ["edge-tts"]
  },
  "ffmpeg": true
}
```

## 8.2 创建任务

```http
POST /jobs
```

请求示例：

```json
{
  "job_id": "uuid",
  "workspace_dir": "D:/output/project",
  "source_video": "D:/output/project/source/video.mp4",
  "source_subtitle": "D:/output/project/subtitles/source.srt",
  "source_language": "en",
  "target_language": "zh-CN",
  "subtitle_mode": "bilingual",
  "burn_subtitles": true,
  "embed_soft_subtitles": false,
  "dubbing_enabled": false,
  "translation": {
    "provider": "openai_compatible",
    "base_url": "https://example/v1",
    "model": "model-name",
    "api_key_env": "V2S_TRANSLATION_API_KEY"
  },
  "style": {
    "preset": "default",
    "font_family": "Microsoft YaHei",
    "font_size": 48,
    "outline": 2,
    "shadow": 1,
    "margin_v": 40
  }
}
```

注意：

- API Key 不应作为普通 JSON 明文落盘；
- 推荐桌面端启动 sidecar 时通过环境变量传入；
- 若需要请求传递，必须只在本机回环地址使用，并禁止日志记录；
- 远程服务模式必须使用 HTTPS 与认证。

## 8.3 查询任务

```http
GET /jobs/{job_id}
```

返回统一 `TaskResult`。

## 8.4 取消任务

```http
POST /jobs/{job_id}/cancel
```

要求：

- 设置取消标记；
- 翻译批次间检查；
- TTS 句子间检查；
- FFmpeg 使用 `Popen`，取消时终止子进程；
- 状态必须最终进入 `cancelled`；
- 不删除已完成的阶段产物。

## 8.5 重试阶段

```http
POST /jobs/{job_id}/retry
```

请求：

```json
{
  "from_stage": "translate"
}
```

## 8.6 获取日志

```http
GET /jobs/{job_id}/logs
```

返回最近日志片段或日志路径，不返回敏感信息。

---

# 9. 统一进度权重

翻译字幕成片：

```text
prepare             0 - 5
transcribe          5 - 35
segment            35 - 43
translate          43 - 72
subtitle_export    72 - 80
render             80 - 97
finalize           97 - 100
```

配音成片：

```text
prepare             0 - 4
transcribe          4 - 25
segment            25 - 31
translate          31 - 52
subtitle_export    52 - 58
tts                58 - 78
audio_mix          78 - 87
render             87 - 97
finalize           97 - 100
```

要求：

- 进度不得倒退；
- 单个阶段内部进度必须可计算；
- 无法计算时显示阶段状态，不伪造精确百分比；
- 任务重试时允许从检查点重新计算总进度；
- UI 必须同时显示阶段名、百分比和当前消息。

---

# 10. 翻译实现要求

## 10.1 第一版 Provider

实现 OpenAI-compatible provider：

```text
POST {base_url}/chat/completions
```

配置：

- base URL；
- API Key；
- model；
- temperature；
- timeout；
- maximum batch characters；
- retry count；
- concurrency；
- JSON mode 是否启用。

## 10.2 批处理

不要逐句请求。建议：

- 每批 10～30 条；
- 同时受总字符数限制；
- 保留唯一 segment ID；
- LLM 返回 JSON 数组；
- 严格校验 ID 数量和顺序；
- 缺失条目只重试缺失部分；
- 解析失败使用 `json_repair` 可选修复；
- 修复后仍失败则记录原始响应的脱敏摘要。

标准返回：

```json
{
  "translations": [
    {
      "id": 1,
      "text": "译文"
    }
  ]
}
```

## 10.3 三步翻译模式

提供两个质量档：

### 快速

```text
Translate
```

### 高质量

```text
Translate
→ Reflect
→ Adapt
```

第一版允许只实现快速模式，但接口必须预留：

```python
quality_mode: Literal["fast", "quality"]
```

高质量模式后续借鉴 VideoLingo 的 Translate-Reflect-Adaptation 思路，但不能依赖其 Streamlit 或全局文件。

## 10.4 术语表

支持：

- JSON；
- CSV；
- XLSX 可作为后续可选；
- 源词；
- 目标词；
- 是否区分大小写；
- 是否强制替换；
- 备注。

术语表必须：

- 注入翻译提示词；
- 翻译后进行一致性检查；
- 不要无脑字符串替换破坏单词边界；
- 保存到项目目录，便于复现；
- 不写入密钥。

## 10.5 翻译检查

至少检查：

- 返回数量；
- 空译文；
- 时间轴未改变；
- ID 对齐；
- 译文异常重复；
- 译文长度极端膨胀；
- JSON 格式；
- 被 LLM 误加说明文字；
- 相邻字幕上下文一致性。

---

# 11. 字幕切分和对齐要求

## 11.1 MVP

先对现有 faster-whisper 片段做规则切分：

- 标点优先；
- 最大字符数；
- 最大持续时间；
- 最小持续时间；
- 最小间隔；
- 不产生空字幕；
- 中文和英文使用不同长度阈值；
- 不破坏原有时间顺序；
- 切分后的时间按字符或词数比例分配。

## 11.2 高级模式

接入 WhisperX 时：

- 作为独立 adapter；
- 输出统一 `SubtitleSegment`；
- 单词级时间戳放入 `words`；
- 模型加载必须延迟；
- 无 WhisperX 时 `/health` 显示 capability 为 false；
- 背景音乐较大时给出提示；
- 多语言混合视频明确提示限制；
- 不允许 WhisperX 安装失败影响基础服务。

## 11.3 单行字幕规则

字幕样式目标：

- 默认最多一行；
- 过长时优先语义切分，而不是强制缩小字体；
- 中日韩按字符数限制；
- 拉丁语言按单词和像素宽度估计；
- 每段阅读速度可配置；
- 生成前运行字幕质量检查报告。

建议默认值：

```text
中文：每行 18～22 个汉字
英文：每行 42～48 个字符
最短显示：0.8 秒
最长显示：7 秒
字幕间隔：至少 0.05 秒
```

这些值必须可配置，不应硬编码到多个文件。

---

# 12. 字幕格式和烧录

## 12.1 输出格式

必须支持：

- Source SRT；
- Translated SRT；
- Bilingual SRT；
- Source ASS；
- Translated ASS；
- Bilingual ASS。

## 12.2 ASS 优先

硬字幕烧录优先使用 ASS，因为需要：

- 字体；
- 字号；
- 描边；
- 阴影；
- 行间距；
- 上下双语布局；
- 位置；
- 安全边距。

## 12.3 字幕样式模型

```python
class SubtitleStyle:
    preset: str
    font_family: str
    font_size: int
    primary_color: str
    secondary_color: str
    outline_color: str
    background_color: str
    outline: float
    shadow: float
    margin_v: int
    alignment: int
    bold: bool
    bilingual_source_scale: float
    bilingual_translation_scale: float
```

预设：

- `default`
- `netflix`
- `youtube`
- `bilingual`
- `mobile_vertical`

## 12.4 FFmpeg 调用

实现统一 `FFmpegService`：

- 查找可执行文件；
- 获取版本；
- 获取视频信息；
- 转义字幕路径；
- 处理 Windows 盘符冒号；
- 处理单引号、反斜杠和空格；
- 捕获 stderr；
- 日志写入任务日志；
- 可取消；
- 失败返回结构化错误；
- 输出文件先写 `.partial`，成功后原子重命名；
- 不覆盖源视频。

建议产物：

```text
rendered/<base>.<target-language>.<mode>.mp4
```

## 12.5 软字幕

使用 MP4 时：

- mov_text 作为基础软字幕；
- 双语复杂样式仍使用硬字幕；
- MKV 可封装 SRT/ASS；
- 软字幕输出与硬字幕输出分开命名。

---

# 13. 配音实现要求

配音放在字幕翻译与烧录稳定之后。

## 13.1 Provider 接口

```python
class TTSProvider(Protocol):
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        ...
```

## 13.2 第一版 Edge-TTS

支持：

- 获取音色列表；
- 按语言筛选；
- 语速；
- 音调；
- 音量；
- 句级输出；
- 超时和重试；
- 缓存。

## 13.3 时长适配

每段 TTS 对齐对应字幕：

1. 生成语音；
2. 获取实际时长；
3. 与字幕可用时长比较；
4. 小差异通过 atempo 调整；
5. 大差异优先重新翻译成更短表达；
6. 不允许无限加速；
7. 最后插入静音或调整句间间隔；
8. 记录每句速度比。

建议限制：

```text
默认最大加速：1.25x
默认最大减速：0.90x
超过阈值：标记 timing_warning
```

## 13.4 音频混合

模式：

- 完全替换原声；
- 保留低音量原声；
- 保留背景音乐；
- 人声分离后替换人声。

第一版只实现：

```text
原视频音量降低 + TTS 音轨覆盖
```

声源分离作为可选高级组件，不能阻塞首版。

## 13.5 配音限制提示

UI 和 README 明确：

- 不同语言语速不同；
- 多角色分离暂不保证；
- 声音克隆可能需要额外模型；
- 第三方 TTS 可能收费；
- 生成内容需遵守声音授权和平台规则。

---

# 14. 桌面 UI 设计

## 14.1 处理模式

主界面增加模式选择：

```text
快速字幕
翻译字幕成片
指定语言配音
```

默认保持：

```text
快速字幕
```

保证老用户操作不变。

## 14.2 新增“本地化设置”对话框

字段：

- 源语言：自动/指定；
- 目标语言；
- 翻译 Provider；
- 模型；
- 翻译质量：快速/高质量；
- 字幕输出：原文/译文/双语；
- 导出 SRT；
- 导出 ASS；
- 软字幕；
- 硬字幕；
- 字幕样式预设；
- 启用配音；
- TTS Provider；
- 音色；
- 原声保留音量；
- 断点续跑；
- 完成后打开目录。

## 14.3 字幕预览

增强现有预览：

- 原文列；
- 译文列；
- 可切换：
  - 原文；
  - 译文；
  - 双语；
- 显示时间轴；
- 显示字幕质量警告；
- 可编辑单条原文和译文；
- 保存后重新生成字幕文件；
- 支持只重新渲染，不重复 ASR 和翻译。

字幕编辑器第一版可以是表格，不需要立即实现视频实时预览。

## 14.4 任务列表

状态细分：

```text
等待
下载
转写
切分
翻译
生成字幕
配音
混音
渲染
完成
失败
取消
```

右键菜单：

- 打开项目目录；
- 查看产物；
- 查看日志；
- 编辑字幕；
- 仅重新翻译；
- 仅重新渲染；
- 仅重新配音；
- 取消；
- 删除任务记录；
- 复制错误信息。

## 14.5 服务状态

状态栏分别显示：

```text
Whisper：已连接/未连接
本地化引擎：已连接/未安装/启动失败
FFmpeg：可用/不可用
GPU：CUDA/CPU
```

高级引擎不可用时：

- 快速字幕按钮仍可用；
- 翻译/配音模式显示安装引导；
- 不应弹出致命错误退出程序。

---

# 15. 设置和密钥管理

## 15.1 新增配置项

建议存入 `.cache/settings.json`，但密钥单独处理：

```text
localization_engine_url
localization_engine_auto_start
translation_provider
translation_base_url
translation_model
translation_timeout
translation_concurrency
translation_quality
default_target_language
subtitle_style_preset
tts_provider
tts_voice
original_audio_volume
```

## 15.2 密钥

优先级：

1. 环境变量；
2. 操作系统凭据存储；
3. 最后才是本地配置文件。

建议环境变量：

```text
V2S_TRANSLATION_API_KEY
V2S_TTS_API_KEY
V2S_AZURE_SPEECH_KEY
V2S_AZURE_SPEECH_REGION
```

要求：

- UI 中使用密码输入框；
- 日志脱敏；
- 错误信息脱敏；
- 测试中使用假 Key；
- `.gitignore` 覆盖本地 secrets 文件；
- 不能把密钥复制进 manifest。

---

# 16. 分阶段开发任务

以下阶段必须按顺序执行。每个任务应独立提交。

---

## 阶段 0：安全基线与工程准备

### 任务 0.1：创建分支并记录基线

分支：

```text
feat/videolingo-localization-engine
```

执行：

```bash
git status
git branch --show-current
python tools/check_project.py
python -m unittest discover -s tests
```

记录：

- 当前通过的测试；
- 当前失败的测试；
- 当前工作树是否干净；
- Python 版本；
- FFmpeg 是否可用。

验收：

- 未改代码前有基线结果；
- 不覆盖未提交改动。

提交：无。

### 任务 0.2：补充许可文件

新增：

```text
LICENSE
THIRD_PARTY_NOTICES.md
licenses/VideoLingo-Apache-2.0.txt
```

要求：

- 当前项目明确 MIT；
- 若复制或改写 VideoLingo 代码，记录原文件、来源 commit、修改说明；
- Apache-2.0 NOTICE 要求按实际情况保留；
- 不声称 VideoLingo 官方背书。

提交：

```text
docs: 补充项目许可与第三方开源声明
```

### 任务 0.3：扩展 `.gitignore`

覆盖：

```text
localization-engine/.venv/
localization-engine/temp/
localization-engine/cache/
localization-engine/models/
qwen3-tts-engine/.venv/
qwen3-tts-engine/models/
qwen3-tts-engine/cache/
qwen3-tts-engine/temp/
qwen3-tts-engine/voices/private/
output/
*.partial
*.wav
*.mp3
*.m4a
*.ass.tmp
cookies.txt
.env
.env.*
!.env.example
```

注意不要忽略测试 fixture。

提交：

```text
chore: 完善本地化引擎和媒体产物忽略规则
```

---

## 阶段 1：任务模型、工作区和 Manifest v2

### 任务 1.1：新增统一模型

新增：

```text
job_models.py
pipeline_types.py
tests/test_job_models.py
```

实现：

- `JobSpec`
- `SubtitleSegment`
- `Artifact`
- `TaskResult`
- `SubtitleStyle`
- 枚举或 Literal；
- 字典序列化；
- 向后兼容转换函数。

测试：

- 合法模型；
- 非法时间轴；
- 字典往返；
- 旧字幕字典兼容；
- 未知字段兼容策略。

提交：

```text
feat: 新增本地化任务和产物统一数据模型
```

### 任务 1.2：项目工作区

新增：

```text
project_workspace.py
tests/test_workspace.py
```

实现：

- 根据标题和 UUID 创建目录；
- 创建 source/subtitles/translation/audio/rendered/logs；
- 安全文件名；
- 防止路径穿越；
- 相对路径；
- 阶段产物清理；
- 原子写入 JSON；
- 项目锁文件；
- 文件指纹。

测试：

- 中文标题；
- Windows 非法字符；
- 同名视频；
- `../` 路径；
- 重复创建；
- 只清理指定阶段。

提交：

```text
feat: 增加独立任务工作区和安全路径管理
```

### 任务 1.3：Manifest v2

修改：

```text
output_manifest.py
history.py
```

新增测试：

```text
tests/test_manifest_v2.py
```

要求：

- 写入 schema_version=2；
- 保留 v1 字段；
- artifacts 数组；
- checkpoints；
- load 时兼容 v1；
- 历史窗口仍能打开旧任务；
- 不迁移或删除旧文件。

提交：

```text
feat: 升级输出清单并兼容旧版历史记录
```

阶段验收：

```bash
python tools/check_project.py
python -m unittest discover -s tests
```

---

## 阶段 2：轻量 Localization Engine 骨架

### 任务 2.1：创建基础服务

新增：

```text
localization-engine/main.py
localization-engine/requirements-base.txt
localization-engine/engine/models.py
localization-engine/engine/task_store.py
localization-engine/engine/progress.py
localization-engine/engine/cancellation.py
localization-engine/engine/workspace.py
localization-engine/engine/pipeline.py
```

基础依赖尽量只包含：

```text
fastapi
uvicorn
pydantic
httpx
```

接口：

- `/health`
- `POST /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/{job_id}/retry`
- `GET /jobs/{job_id}/logs`

第一版 pipeline 可以只执行：

```text
prepare → subtitle_export → finalize
```

用于验证任务系统。

要求：

- loopback 默认绑定 `127.0.0.1`；
- 线程安全任务存储；
- 任务 JSON 持久化；
- 服务重启后能恢复已完成/失败任务；
- 运行中的任务重启后标记 interrupted；
- 结构化错误；
- 日志轮转或限制大小。

提交：

```text
feat: 新增轻量本地化引擎和任务接口
```

### 任务 2.2：桌面客户端和自动启动

新增：

```text
localization_client.py
```

修改：

```text
app.py
diagnostics.py
client_settings.py
```

实现：

- 默认 URL `http://127.0.0.1:8766`；
- 健康检查；
- 提交任务；
- 查询进度；
- 取消；
- 启动独立虚拟环境中的服务；
- 独立日志 `.cache/localization-service.log`；
- 不与 `8765` 服务共用 PID 检测；
- 启动失败不影响主程序；
- 设置中允许关闭自动启动。

不要复制 `app.py` 中 Whisper 启动逻辑形成第二份难维护代码。优先抽取可复用 `SidecarManager`：

```text
services/sidecar_manager.py
```

提交：

```text
feat: 接入本地化引擎客户端和独立服务管理
```

### 任务 2.3：环境检查

扩展诊断：

- 8766 端口；
- 引擎健康状态；
- FFmpeg；
- 字体；
- 基础依赖；
- WhisperX 可选依赖；
- Edge-TTS 可选依赖；
- 写入权限；
- 输出目录空间。

提交：

```text
feat: 扩展本地化引擎和媒体处理环境检查
```

阶段验收：

- 不启动 8766 时快速字幕仍工作；
- 启动 8766 后健康检查通过；
- 可创建、查询、取消空任务；
- 服务日志可打开；
- 无高级依赖也能启动基础引擎。

---

## 阶段 3：翻译字幕与烧录成片 MVP

这是第一个正式可交付版本。

### 任务 3.1：字幕读取、标准化和输出

新增：

```text
localization-engine/subtitles/normalize.py
localization-engine/subtitles/srt_writer.py
localization-engine/subtitles/ass_writer.py
localization-engine/subtitles/validate.py
subtitle_ass.py
tests/test_ass_writer.py
```

实现：

- 读取现有字幕 JSON/SRT；
- 统一为 `SubtitleSegment`；
- 原文 SRT；
- 译文 SRT；
- 双语 SRT；
- 原文 ASS；
- 译文 ASS；
- 双语 ASS；
- 样式预设；
- 时间轴检查；
- HTML/ASS 特殊字符转义。

提交：

```text
feat: 支持单语双语字幕标准化与 ASS 输出
```

### 任务 3.2：翻译 Provider

新增：

```text
localization-engine/translation/base.py
localization-engine/translation/openai_compatible.py
localization-engine/translation/batching.py
localization-engine/translation/glossary.py
localization-engine/translation/prompts.py
localization-engine/translation/response_parser.py
tests/test_translation_parser.py
tests/test_glossary.py
```

实现：

- OpenAI-compatible；
- 批处理；
- JSON 响应；
- 指数退避；
- 超时；
- 429、5xx 重试；
- 401 不重复重试；
- 断点文件；
- 只重试失败批次；
- 术语表；
- 脱敏日志；
- 假 Provider 供测试。

不要在单元测试访问真实网络。

提交：

```text
feat: 增加兼容 OpenAI 接口的字幕批量翻译
```

### 任务 3.3：FFmpeg 渲染服务

新增：

```text
services/process_runner.py
services/ffmpeg_service.py
localization-engine/rendering/ffmpeg.py
localization-engine/rendering/filters.py
localization-engine/rendering/presets.py
tests/test_ffmpeg_filters.py
```

实现：

- 硬字幕；
- 软字幕；
- 复制音频；
- H.264 基础编码；
- 可选硬件编码预留；
- `.partial`；
- 取消；
- 路径转义；
- stderr 日志；
- 原视频不覆盖。

第一版编码建议：

```text
libx264
preset=medium 或 veryfast
crf=20～23
audio=aac
movflags=+faststart
```

不要默认强制使用 NVENC，后续配置化。

提交：

```text
feat: 新增可取消的 FFmpeg 字幕烧录与软字幕封装
```

### 任务 3.4：Pipeline 串联

实现：

```text
source video + source subtitles
→ normalize
→ translate
→ write srt/ass
→ render
→ update manifest
```

要求：

- 检查点；
- 从 translate/render 重试；
- 进度权重；
- 取消；
- artifacts；
- 失败不删除成功字幕；
- 同一任务重复提交幂等；
- 配置变化时正确使缓存失效。

提交：

```text
feat: 串联字幕翻译和视频渲染本地化流程
```

### 任务 3.5：桌面 UI 接入 MVP

新增：

```text
ui/localization_dialog.py
ui/subtitle_style_dialog.py
```

小范围修改：

```text
main_window.py
client_settings.py
```

实现：

- 处理模式选择；
- 目标语言；
- 翻译设置；
- 单语/双语；
- 硬字幕/软字幕；
- 字幕样式；
- 高级引擎状态；
- 提交任务；
- 展示阶段；
- 取消；
- 打开产物；
- 错误日志。

重要：

- 不要重写整个 `MainWindow`；
- 不要删除现有 `WorkerThread`；
- 快速字幕仍走现有流程；
- 翻译模式在 ASR 完成并保存源视频/源字幕后提交到 8766；
- 现有 Whisper 返回的 `media_file` 应逐步纳入统一结果，避免仅靠临时目录猜文件名。

提交：

```text
feat: 在桌面端加入翻译字幕和烧录成片模式
```

### 任务 3.6：README 和端到端验证

README 增加：

- 模式说明；
- 高级引擎安装；
- API 配置；
- 字幕样式；
- 输出目录；
- 常见错误；
- 隐私说明；
- 许可证。

端到端测试素材：

1. 10～30 秒英文视频；
2. 中文路径；
3. 文件名含空格；
4. 目标语言中文；
5. 原文字幕；
6. 译文字幕；
7. 双语字幕；
8. 硬字幕视频；
9. 软字幕视频；
10. 中途取消；
11. 渲染重试；
12. API 返回非法 JSON；
13. API 429；
14. 没有 FFmpeg；
15. 8766 不可用。

提交：

```text
docs: 完善翻译字幕和烧录成片使用说明
```

阶段 3 完成标准：

- 用户可选择本地视频或在线视频；
- 生成源字幕；
- 翻译为指定语言；
- 导出原文、译文和双语字幕；
- 成功烧录译文或双语字幕；
- 可取消；
- 可重试翻译或渲染；
- 旧快速字幕模式完全可用；
- 无 WhisperX、无 TTS 也能完成全部 MVP 流程。

建议此时打标签：

```text
v0.5.0-localization-mvp
```

---

## 阶段 4：字幕编辑与重渲染

### 任务 4.1：字幕编辑器

新增：

```text
ui/subtitle_editor.py
```

功能：

- 表格编辑；
- 原文/译文；
- 开始/结束时间；
- 校验；
- 搜索替换；
- 保存；
- 撤销至少一层；
- 标记未保存；
- 不允许非法时间轴直接写入。

提交：

```text
feat: 新增双语字幕编辑器和时间轴校验
```

### 任务 4.2：只重新渲染

实现：

- 修改字幕后不重新 ASR；
- 不重新翻译；
- 重新生成 SRT/ASS；
- 重新渲染；
- 产生新 artifact；
- 保留旧产物或明确覆盖策略。

提交：

```text
feat: 支持编辑字幕后独立重新渲染视频
```

---

## 阶段 5：VideoLingo 高级字幕能力

### 任务 5.1：VideoLingo 代码审计

在复制任何代码前完成：

- 记录所参考文件；
- 记录 commit SHA；
- 标注 Apache-2.0；
- 分析 Streamlit 耦合；
- 分析全局 `output/`；
- 分析 config 全局读取；
- 只列出需要提取的纯算法。

输出：

```text
docs/VIDEOLINGO_INTEGRATION_NOTES.md
```

提交：

```text
docs: 记录 VideoLingo 高级字幕模块集成边界
```

### 任务 5.2：高级语义切分

先独立实现 adapter：

```text
localization-engine/subtitles/segment.py
```

可借鉴：

- NLP 断句；
- LLM 辅助切分；
- 单行字幕；
- 阅读速度；
- 语义完整性。

必须：

- 输入输出使用统一模型；
- 不读取 Streamlit session；
- 不读取固定 output；
- 不依赖 UI；
- 可禁用；
- 失败回退规则切分。

提交：

```text
feat: 增加可回退的高级字幕语义切分
```

### 任务 5.3：WhisperX 可选 Adapter

新增：

```text
localization-engine/asr/base.py
localization-engine/asr/whisperx_adapter.py
localization-engine/requirements-whisperx.txt
```

实现：

- 延迟导入；
- capability 检测；
- GPU/CPU；
- 模型目录；
- 单词时间戳；
- 进度；
- 取消点；
- 统一输出；
- 安装检查；
- 不影响基础引擎。

桌面端允许：

```text
ASR 引擎：
- faster-whisper（快速）
- WhisperX（精确时间轴）
```

提交：

```text
feat: 接入可选 WhisperX 单词级时间轴引擎
```

### 任务 5.4：高质量三步翻译

实现：

```text
Translate → Reflect → Adapt
```

要求：

- 可配置；
- 每步独立检查点；
- 可只重跑 Adapt；
- 成本预估；
- 上下文控制；
- 弱模型 JSON 失败时明确提示；
- 不覆盖快速译文，允许比较。

提交：

```text
feat: 增加翻译反思润色的高质量模式
```

---

## 阶段 6：Qwen3-TTS 本地配音与指定语言成片

Qwen3-TTS 作为项目的**主要本地 TTS 方案**，Edge-TTS 作为轻量回退。

官方 Qwen3-TTS 当前提供：

- `Qwen3-TTS-12Hz-0.6B-CustomVoice`
- `Qwen3-TTS-12Hz-0.6B-Base`
- `Qwen3-TTS-12Hz-1.7B-CustomVoice`
- `Qwen3-TTS-12Hz-1.7B-Base`
- `Qwen3-TTS-12Hz-1.7B-VoiceDesign`
- `Qwen3-TTS-Tokenizer-12Hz`

支持中文、英文、日语、韩语、德语、法语、俄语、葡萄牙语、西班牙语和意大利语，并支持预设音色、自然语言风格控制、Voice Design 和参考音频克隆。

官方仓库和 Python 包为 Apache-2.0。必须将许可证副本加入：

```text
licenses/Qwen3-TTS-Apache-2.0.txt
```

并在 `THIRD_PARTY_NOTICES.md` 中说明：

- 代码来源；
- 使用的模型；
- 是否修改官方代码；
- 模型权重不直接包含在 Git 仓库；
- 用户首次使用时按需下载。

### 任务 6.1：建立独立 Qwen3-TTS Sidecar

新增：

```text
qwen3-tts-engine/
├── main.py
├── requirements.txt
├── engine/
│   ├── model_manager.py
│   ├── schemas.py
│   ├── synthesis.py
│   ├── voice_clone.py
│   ├── voice_design.py
│   ├── cache.py
│   └── device.py
└── tests/
```

默认服务：

```text
http://127.0.0.1:8767
```

必须使用独立虚拟环境，不能安装进桌面主程序环境，也不能默认与 WhisperX 共用环境。

原因：

- 官方推荐新建隔离环境；
- `qwen-tts` 会引入固定版本 Transformers、Accelerate、torchaudio、librosa、soundfile、sox 和 onnxruntime；
- PyTorch、CUDA、Transformers 和 WhisperX 可能发生依赖冲突；
- 模型需要独立管理 GPU 显存和生命周期；
- TTS 服务崩溃不能导致桌面端退出。

基础安装：

```text
Python >= 3.9
pip install -U qwen-tts
```

项目安装器可以优先创建 Python 3.12 环境，但不能强制用户的主项目升级到 Python 3.12。

FlashAttention 2：

- 作为可选加速项；
- 检测兼容性后再安装；
- 安装失败必须回退到 SDPA/eager；
- 不允许因缺少 FlashAttention 导致 Qwen3-TTS 完全不可用；
- Windows 原生环境不能假定 FlashAttention 安装一定成功。

提交：

```text
feat: 新增独立 Qwen3-TTS 本地语音服务
```

### 任务 6.2：Qwen3-TTS 服务 API

实现：

```http
GET /health
GET /models
POST /models/install
POST /models/load
POST /models/unload
GET /voices
POST /synthesize/custom-voice
POST /synthesize/voice-design
POST /synthesize/voice-clone
POST /voice-clone/prompts
GET /tasks/{task_id}
POST /tasks/{task_id}/cancel
```

健康检查示例：

```json
{
  "status": "ok",
  "service": "video2subtitles-qwen3-tts",
  "version": "0.1.0",
  "device": "cuda",
  "dtype": "bfloat16",
  "loaded_model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
  "flash_attention": false,
  "capabilities": {
    "custom_voice": true,
    "voice_design": false,
    "voice_clone": false,
    "batch": true
  }
}
```

要求：

- 默认只绑定 `127.0.0.1`；
- 支持任务取消；
- 音频先输出 `.partial`；
- 模型加载互斥；
- 同一 GPU 默认只加载一个 Qwen3-TTS 主模型；
- 模型切换前释放旧模型；
- 调用 `torch.cuda.empty_cache()` 只能作为辅助，不能替代正确释放引用；
- 记录模型加载失败、CUDA OOM 和依赖错误；
- 不记录用户参考音频内容；
- 参考音频保存由用户显式选择；
- Sidecar 服务重启后不自动恢复私人音色，除非用户启用“记住该音色”。

提交：

```text
feat: 完成 Qwen3-TTS 模型管理和语音生成接口
```

### 任务 6.3：模型分级与默认选择

桌面端提供三档：

#### 标准本地配音

```text
Qwen3-TTS-12Hz-0.6B-CustomVoice
```

适合：

- 普通字幕配音；
- 预设音色；
- 中文、英文等多语言；
- 较低资源占用；
- 默认推荐。

#### 本地声音克隆

```text
Qwen3-TTS-12Hz-0.6B-Base
```

适合：

- 根据参考音频克隆；
- 用户自己的声音；
- 经授权的角色声音；
- 需要保存可复用 voice prompt 的场景。

#### 高质量/高级创作

```text
Qwen3-TTS-12Hz-1.7B-CustomVoice
Qwen3-TTS-12Hz-1.7B-Base
Qwen3-TTS-12Hz-1.7B-VoiceDesign
```

适合：

- 更高质量；
- 自然语言音色设计；
- 高质量声音克隆；
- 显存和内存条件较好的设备。

针对 RTX 4070 12GB：

- 默认先推荐 0.6B CustomVoice；
- 1.7B 作为“高级模型”提供下载和实机测试；
- 不在没有官方或本机基准的情况下承诺固定显存占用；
- 加载前检查可用显存；
- OOM 时自动卸载并提示切换 0.6B；
- 不允许 WhisperX、faster-whisper 和 1.7B TTS 同时长期占用 GPU；
- Pipeline 应按阶段串行释放模型。

建议 GPU 调度：

```text
ASR 完成
→ 卸载或释放 ASR 模型
→ 加载 Qwen3-TTS
→ 完成全部句子配音
→ 卸载 Qwen3-TTS
→ 启动 FFmpeg 渲染
```

提交：

```text
feat: 增加 Qwen3-TTS 模型分级和 GPU 资源调度
```

### 任务 6.4：Qwen3-TTS Provider Adapter

新增：

```text
localization-engine/tts/qwen3_tts.py
localization-engine/tts/base.py
localization-engine/tts/timing.py
```

统一接口：

```python
class TTSProvider(Protocol):
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None,
        output_path: Path,
        options: dict,
    ) -> TTSResult:
        ...
```

Qwen3-TTS 参数：

```text
model
mode: custom_voice / voice_design / voice_clone
language
speaker
instruct
reference_audio
reference_text
voice_clone_prompt_id
max_new_tokens
top_p
temperature
seed
```

要求：

- 字幕语言映射到 Qwen3-TTS 官方语言名称；
- 已知目标语言时不要总是传 Auto；
- CustomVoice 使用官方支持的 speaker 列表；
- VoiceDesign 仅在对应模型已安装时显示；
- VoiceClone 仅在 Base 模型已安装时显示；
- 同一任务的 voice clone prompt 只计算一次并复用；
- 批量合成保留 segment ID；
- 失败只重试失败句；
- 每句生成结果进入缓存；
- 缓存键包含模型、文本、语言、speaker、instruct、voice prompt 指纹和生成参数。

提交：

```text
feat: 将 Qwen3-TTS 接入统一配音 Provider
```

### 任务 6.5：桌面端 Qwen3-TTS 安装与模型管理

新增 UI：

```text
ui/qwen_tts_install_dialog.py
ui/qwen_tts_model_dialog.py
ui/voice_clone_dialog.py
ui/voice_design_dialog.py
```

功能：

- 检测 Python；
- 创建独立环境；
- 安装/修复 `qwen-tts`；
- 显示已安装模型；
- 从 ModelScope 或 Hugging Face 下载；
- 国内用户默认可选择 ModelScope；
- 显示模型下载进度；
- 暂停/取消下载；
- 校验模型目录；
- 删除模型前二次确认；
- 显示磁盘占用；
- 启动/停止 Qwen3-TTS 服务；
- 预览测试语句；
- 自动检测 CUDA；
- 允许 CPU 模式，但明确提示可能很慢；
- 显示 FlashAttention 是否可用。

“内置”的用户体验应为：

```text
打开程序
→ 设置
→ 安装 Qwen3-TTS
→ 选择 0.6B 标准模型
→ 自动下载
→ 自动启动
→ 在配音模式选择 Qwen3-TTS
```

而不是让用户手动打开命令行安装。

提交：

```text
feat: 增加 Qwen3-TTS 一键安装和模型管理界面
```

### 任务 6.6：预设音色、Voice Design 和 Voice Clone UI

配音模式提供：

#### 预设音色

- Vivian
- Serena
- Uncle_Fu
- Dylan
- Eric
- Ryan
- Aiden
- Ono_Anna
- Sohee

音色列表必须从 Sidecar 动态获取，不在 UI 中永久硬编码。

#### Voice Design

字段：

- 音色描述；
- 年龄感；
- 性别表达；
- 情绪；
- 语速；
- 语调；
- 场景；
- 试听文本。

保存：

```text
voice_design_profile.json
```

不默认保存生成音频以外的私人信息。

#### Voice Clone

字段：

- 参考音频；
- 参考文本；
- 语言；
- 仅 speaker embedding 模式；
- 保存可复用 prompt；
- 删除 prompt。

安全要求：

- 弹出确认：用户必须拥有声音使用权或取得授权；
- 禁止默认勾选保存参考音频；
- 私人参考音频不进入 ChatGPT 分析包；
- 不进入 Git；
- 不进入普通日志；
- 提供“一键删除音色数据”。

提交：

```text
feat: 增加 Qwen3-TTS 音色设计和授权声音克隆
```

### 任务 6.7：句级生成与时长适配

每段字幕：

1. 生成音频；
2. 获取实际时长；
3. 与字幕时间窗比较；
4. 小差异使用 FFmpeg `atempo`；
5. 大差异先尝试更短翻译；
6. 再次生成；
7. 仍超长则标记人工检查；
8. 最后按用户策略裁切、延长视频间隔或允许轻微重叠。

建议策略：

```text
normal: 0.90x ～ 1.20x
warning: 0.80x ～ 1.30x
outside: 重新改写或人工处理
```

Qwen3-TTS 的 `instruct` 可用于语气和节奏控制，但不能把它当作精确时长控制器。

保存：

```text
audio/tts/<segment-id>.wav
audio/tts/index.json
```

`index.json` 包含：

- segment ID；
- 文本；
- 模型；
- speaker；
- instruct；
- 原始时长；
- 目标时长；
- atempo；
- 状态；
- warning；
- 文件指纹。

提交：

```text
feat: 支持 Qwen3-TTS 句级缓存和配音时长适配
```

### 任务 6.8：音频拼接、混合与最终视频

新增：

```text
localization-engine/audio/normalize.py
localization-engine/audio/mix.py
```

实现：

- 根据字幕开始时间放置句级音频；
- 静音填充；
- 响度标准化；
- 原声降低；
- TTS 覆盖；
- 峰值保护；
- 输出配音 WAV/AAC；
- 与字幕和视频合成。

第一版：

```text
原视频音量降低 + Qwen3-TTS 音轨覆盖
```

后续再做声源分离。

提交：

```text
feat: 完成 Qwen3-TTS 配音音轨混合与视频合成
```

### 任务 6.9：Edge-TTS 回退

保留 Edge-TTS，但定位调整为：

- 无 NVIDIA GPU；
- 用户不想下载模型；
- Qwen3-TTS 安装失败；
- 临时快速生成；
- 低资源设备。

Provider 顺序：

```text
Qwen3-TTS Local
Edge-TTS
OpenAI-compatible TTS
Custom HTTP TTS
```

自动回退必须由用户设置允许，不能在 Qwen3-TTS 失败时悄悄换音色生成成片。

提交：

```text
feat: 增加 Qwen3-TTS 失败时的可控轻量配音回退
```

### 阶段 6 完成标准

- 可一键安装 Qwen3-TTS 独立环境；
- 可下载 0.6B 标准模型；
- 可启动 8767；
- 可选择预设音色；
- 可使用自然语言控制语气；
- 可选 Voice Design；
- 可选授权 Voice Clone；
- 可逐句生成；
- 可缓存；
- 可重试失败句；
- 可做时长适配；
- 可降低原声并混音；
- 可输出指定语言配音视频；
- Qwen3-TTS 未安装时字幕翻译和烧录正常；
- Edge-TTS 仍可作为轻量回退；
- RTX 4070 12GB 默认选择 0.6B，不对 1.7B 做未经验证的显存承诺；
- 隐私音频和 voice prompt 可一键删除；
- Apache-2.0 声明完整。

建议标签：

```text
v0.8.0-qwen3-tts-dubbing
```

---

## 阶段 7：打包、安装与稳定性

### 任务 7.1：组件安装器

实现：

- 基础桌面依赖；
- Whisper 服务依赖；
- 本地化基础依赖；
- WhisperX 可选依赖；
- TTS 可选依赖；
- 模型单独下载；
- 显示磁盘需求；
- 安装日志；
- 修复安装；
- 不要求管理员权限，除非安装系统 FFmpeg。

建议优先使用独立 `.venv` 或 `uv`。

提交：

```text
feat: 增加高级本地化组件按需安装与修复
```

### 任务 7.2：打包策略

不要立即追求单文件 EXE。

推荐：

```text
基础桌面程序
+ whisper-server 独立环境
+ localization-engine 独立环境
+ 可选模型目录
```

文档说明：

- PyInstaller 文件夹模式；
- FFmpeg 分发或系统检测；
- 第三方许可证；
- CUDA 与 CPU 版本；
- 自动更新边界。

提交：

```text
docs: 增加桌面端和高级引擎打包发布方案
```

### 任务 7.3：稳定性

增加：

- 任务恢复；
- 崩溃后 interrupted；
- 磁盘空间预检；
- 超长视频提示；
- 日志大小限制；
- 临时文件清理；
- 并发限制；
- GPU 显存不足回退；
- 网络断开重试；
- API 成本提示；
- 产物完整性检查。

提交：

```text
fix: 完善长视频任务恢复和资源异常处理
```

---

# 17. 提交顺序总表

严格按以下顺序，允许根据实际代码合并极小提交，但不要把全部内容塞进一个提交。

```text
1. docs: 补充项目许可与第三方开源声明
2. chore: 完善本地化引擎和媒体产物忽略规则
3. feat: 新增本地化任务和产物统一数据模型
4. feat: 增加独立任务工作区和安全路径管理
5. feat: 升级输出清单并兼容旧版历史记录
6. feat: 新增轻量本地化引擎和任务接口
7. feat: 接入本地化引擎客户端和独立服务管理
8. feat: 扩展本地化引擎和媒体处理环境检查
9. feat: 支持单语双语字幕标准化与 ASS 输出
10. feat: 增加兼容 OpenAI 接口的字幕批量翻译
11. feat: 新增可取消的 FFmpeg 字幕烧录与软字幕封装
12. feat: 串联字幕翻译和视频渲染本地化流程
13. feat: 在桌面端加入翻译字幕和烧录成片模式
14. docs: 完善翻译字幕和烧录成片使用说明
15. feat: 新增双语字幕编辑器和时间轴校验
16. feat: 支持编辑字幕后独立重新渲染视频
17. docs: 记录 VideoLingo 高级字幕模块集成边界
18. feat: 增加可回退的高级字幕语义切分
19. feat: 接入可选 WhisperX 单词级时间轴引擎
20. feat: 增加翻译反思润色的高质量模式
21. feat: 新增独立 Qwen3-TTS 本地语音服务
22. feat: 完成 Qwen3-TTS 模型管理和语音生成接口
23. feat: 增加 Qwen3-TTS 模型分级和 GPU 资源调度
24. feat: 将 Qwen3-TTS 接入统一配音 Provider
25. feat: 增加 Qwen3-TTS 一键安装和模型管理界面
26. feat: 增加 Qwen3-TTS 音色设计和授权声音克隆
27. feat: 支持 Qwen3-TTS 句级缓存和配音时长适配
28. feat: 完成 Qwen3-TTS 配音音轨混合与视频合成
29. feat: 增加 Qwen3-TTS 失败时的可控轻量配音回退
30. feat: 增加兼容 OpenAI 接口的配音服务
31. feat: 增加高级本地化组件按需安装与修复
32. docs: 增加桌面端和高级引擎打包发布方案
33. fix: 完善长视频任务恢复和资源异常处理
```

---

# 18. 测试策略

## 18.1 单元测试

必须覆盖：

- 安全文件名；
- 项目工作区；
- Manifest v1/v2；
- 字幕时间格式；
- SRT；
- ASS 特殊字符；
- 双语布局；
- 翻译批次；
- JSON 修复；
- 缺失翻译；
- 术语表；
- FFmpeg filter 转义；
- Windows 路径；
- 状态转换；
- 取消；
- 检查点；
- API Key 脱敏；
- TTS 时长比例；
- Qwen3-TTS 模型能力映射；
- Qwen3-TTS 缓存键；
- 模型加载/卸载状态；
- CUDA OOM 回退；
- Voice Clone 私有数据清理。

## 18.2 API 测试

使用 FastAPI TestClient：

- health；
- create；
- get；
- cancel；
- retry；
- invalid request；
- unknown task；
- interrupted recovery；
- capability；
- auth（远程模式预留）。

## 18.3 集成测试

使用短媒体 fixture，避免大文件：

- 2～5 秒无声视频；
- 2～5 秒带音频视频；
- 简短 SRT；
- 中文路径；
- 空格路径。

测试：

- 生成 ASS；
- 硬字幕；
- 软字幕；
- 取消 FFmpeg；
- 不覆盖源文件；
- `.partial` 清理；
- manifest artifact。

## 18.4 手动测试矩阵

| 场景 | CPU | NVIDIA | 本地视频 | URL | 翻译 | 烧录 | 配音 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 快速字幕 | ✅ | ✅ | ✅ | ✅ | - | - | - |
| 翻译字幕 | ✅ | ✅ | ✅ | ✅ | ✅ | - | - |
| 双语硬字幕 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | - |
| Qwen3-TTS 0.6B 配音 | 较慢/可选 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qwen3-TTS 1.7B 配音 | 不推荐 | 实机测试 | ✅ | ✅ | ✅ | ✅ | ✅ |
| Edge-TTS 回退 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| WhisperX | 可选 | ✅ | ✅ | ✅ | ✅ | ✅ | 可选 |

## 18.5 每次提交后验证

```bash
python tools/check_project.py
python -m unittest discover -s tests
```

本地化引擎测试可增加：

```bash
python -m unittest discover -s localization-engine/tests
```

若后续使用 pytest，则统一并更新 README，不要混乱地维护两套命令。

---

# 19. 错误码建议

```text
ENGINE_UNAVAILABLE
INVALID_JOB_SPEC
SOURCE_NOT_FOUND
SOURCE_SUBTITLE_NOT_FOUND
WORKSPACE_NOT_WRITABLE
FFMPEG_NOT_FOUND
FFMPEG_FAILED
TRANSLATION_AUTH_FAILED
TRANSLATION_RATE_LIMITED
TRANSLATION_TIMEOUT
TRANSLATION_INVALID_RESPONSE
TRANSLATION_INCOMPLETE
SUBTITLE_INVALID_TIMELINE
SUBTITLE_RENDER_FAILED
WHISPERX_NOT_INSTALLED
WHISPERX_FAILED
TTS_NOT_INSTALLED
TTS_AUTH_FAILED
TTS_FAILED
AUDIO_MIX_FAILED
TASK_CANCELLED
TASK_INTERRUPTED
DISK_SPACE_LOW
```

UI 显示用户友好中文信息，同时保留技术详情供复制。

---

# 20. 性能与资源限制

默认：

- 同时只运行 1 个 GPU ASR/WhisperX 任务；
- 翻译并发 1～3；
- TTS 并发 2～4；
- FFmpeg 渲染并发 1；
- 批量任务排队；
- 支持用户取消；
- 模型复用；
- 不重复加载 WhisperX；
- 不把整段视频读入内存；
- 大 JSON 使用分阶段文件；
- 日志限制大小；
- 长视频每阶段持久化。

磁盘空间预估：

```text
至少需要：
源视频大小 × 2
+ 临时音频
+ 渲染输出
+ 可选 TTS 句级文件
```

开始任务前做简单预检。

---

# 21. 安全要求

- FastAPI 默认只绑定 `127.0.0.1`；
- 远程模式必须显式开启；
- 远程模式必须有 API Key；
- 路径限制在任务 workspace；
- 不允许请求传入任意删除目录；
- 上传文件限制扩展名和大小；
- 防止 ZIP 路径穿越；
- FFmpeg 参数不拼 shell 字符串；
- 日志脱敏；
- Cookie 不写入输出；
- API 原始响应只在调试模式保存，并脱敏；
- 错误弹窗不展示密钥；
- 第三方模型下载校验来源；
- TTS 声音克隆功能必须展示授权提示；
- Qwen3-TTS 参考音频默认不持久化；
- 私人声音数据不得进入 ChatGPT 包、日志或 Git；
- Sidecar 仅绑定回环地址；
- 模型下载来源必须是官方 Hugging Face、ModelScope 或用户明确指定的本地目录。

---

# 22. DeepSeek 执行时的工作方式

每轮只执行一个阶段或 1～3 个相邻任务，避免上下文失控。

每轮开始：

1. 查看 `git status`；
2. 查看当前分支；
3. 阅读 README；
4. 阅读本轮涉及文件；
5. 搜索已有同类实现；
6. 写出本轮简短执行清单；
7. 再修改代码。

每轮结束必须输出：

```text
本轮完成：
- 修改文件：
- 新增功能：
- 兼容性：
- README：
- 测试命令：
- 测试结果：
- Commit：
- 下一步：
- 遗留风险：
```

出现工具异常时：

- 重新读取文件验证真实状态；
- 不盲目重复写入；
- 不连续创建重复分支；
- 不在不确定写入成功时直接提交；
- 检查 diff；
- 检查语法；
- 再继续。

---

# 23. 第一轮可直接复制给 DeepSeek 的执行提示词

以下提示词只执行阶段 0 和阶段 1，不要一次执行完整计划。

```text
你正在维护 Git 仓库 video2subtitles-desktop。

请严格阅读仓库当前 README、项目结构、app.py、main_window.py、api_client.py、
output_manifest.py、history.py、subtitle_utils.py、whisper-server/main.py 和现有测试，
然后执行《Video2Subtitles × VideoLingo 桌面端整合开发计划》的阶段 0 与阶段 1。

本轮目标：
1. 检查 Git 工作树和当前分支，不覆盖未提交改动。
2. 创建或切换到 feat/videolingo-localization-engine。
3. 运行现有基线测试并记录结果。
4. 补充 MIT LICENSE、THIRD_PARTY_NOTICES.md，以及 VideoLingo Apache-2.0 许可证副本。
5. 扩展 .gitignore，忽略高级引擎虚拟环境、模型、缓存、媒体临时文件和密钥文件。
6. 新增统一 JobSpec、SubtitleSegment、Artifact、TaskResult、SubtitleStyle 数据模型。
7. 新增独立任务工作区 project_workspace.py。
8. 将 output_manifest.py 升级为兼容旧版的 manifest v2。
9. 更新 history.py 以兼容 v1 和 v2，不能破坏旧历史记录。
10. 为模型、工作区和 manifest v2 增加 unittest 测试。
11. 更新 README，说明新的任务工作区和 manifest v2。
12. 每个逻辑任务独立使用中文 commit，不要一次性提交所有内容。

重要约束：
- 不要开始开发翻译、WhisperX、TTS 或 FFmpeg 烧录。
- 不要重写 main_window.py。
- 不要直接复制 VideoLingo 的 core 目录。
- 不要引入大型依赖。
- 不要把 API Key、Cookie、模型和媒体文件提交进仓库。
- 所有新路径逻辑必须测试中文、空格、Windows 非法字符和路径穿越。
- manifest v2 必须保留旧字段兼容。
- 修改前先读取文件，修改后检查 git diff。
- 每次提交前运行：
  python tools/check_project.py
  python -m unittest discover -s tests

完成后报告：
- 修改文件；
- 实现内容；
- 测试结果；
- README 是否更新；
- commit 列表；
- 未完成事项；
- 下一轮应执行的阶段 2 任务。
```

---

# 24. 第二轮提示词

阶段 0 和 1 合并完成后使用：

```text
继续维护 video2subtitles-desktop，不要从头重做。

请先读取当前分支、git log、git status、阶段 1 新增的数据模型、工作区和 manifest v2，
确认测试通过，然后执行计划中的阶段 2：轻量 Localization Engine 骨架。

目标：
1. 新增 localization-engine 基础目录和 requirements-base.txt。
2. 实现 FastAPI 服务，默认 127.0.0.1:8766。
3. 实现 health、创建任务、查询、取消、重试、日志接口。
4. 实现线程安全任务存储和 JSON 持久化。
5. 服务重启后将未完成任务标记 interrupted。
6. 新增 localization_client.py。
7. 抽取可复用 SidecarManager，避免复制 app.py 的 Whisper 启动代码。
8. 保持 8765 Whisper 服务行为不变。
9. 扩展 diagnostics.py 和设置。
10. 新增 API 测试。
11. 更新 README。
12. 使用中文 commit 分步提交。

本轮不要实现真实翻译、WhisperX、TTS 和视频烧录。
本地化引擎没有安装或启动失败时，桌面快速字幕模式必须正常使用。
```

---

# 25. 第三轮提示词

阶段 2 完成后使用：

```text
继续当前 feat/videolingo-localization-engine 分支。

执行计划阶段 3：翻译字幕与烧录成片 MVP。

必须实现：
1. 字幕标准化。
2. 原文、译文、双语 SRT。
3. 原文、译文、双语 ASS。
4. OpenAI-compatible 批量翻译。
5. JSON 严格校验、重试、断点续跑和脱敏日志。
6. JSON/CSV 术语表。
7. 可取消的 FFmpeg 硬字幕与软字幕。
8. .partial 输出和成功后原子重命名。
9. 翻译→字幕输出→渲染 Pipeline。
10. Manifest v2 artifacts 和 checkpoints。
11. PyQt5 模式选择、本地化设置、目标语言、字幕模式和任务进度。
12. 快速字幕模式保持不变。
13. 没有 WhisperX 和 TTS 时 MVP 必须完整可用。
14. 增加单元测试、API 测试和短视频集成测试。
15. 更新 README。
16. 中文 commit 分步提交。

禁止：
- 重写整个 main_window.py；
- 把 API Key 写入 manifest 或日志；
- 使用 shell=True；
- 直接复制 VideoLingo 的 Streamlit 页面；
- 强制安装 PyTorch、WhisperX 或 TTS。
```

---

# 26. 完整验收清单

## 基础兼容

- [ ] 原有本地视频转写正常
- [ ] 原有 URL 下载转写正常
- [ ] 原有 SRT/VTT/TXT 正常
- [ ] 原有历史记录正常
- [ ] 原有 ChatGPT 包正常
- [ ] 8766 不可用时基础功能正常

## 翻译字幕

- [ ] 可选择目标语言
- [ ] 可生成译文 SRT
- [ ] 可生成双语 SRT
- [ ] 可生成译文 ASS
- [ ] 可生成双语 ASS
- [ ] 术语表生效
- [ ] API 失败可重试
- [ ] 中途取消
- [ ] 断点续跑
- [ ] API Key 不泄露

## 视频渲染

- [ ] 中文路径
- [ ] 空格路径
- [ ] Windows 盘符
- [ ] 单语硬字幕
- [ ] 双语硬字幕
- [ ] 软字幕
- [ ] 不覆盖源视频
- [ ] 可取消 FFmpeg
- [ ] 失败保留日志
- [ ] `.partial` 正确处理

## 高级字幕

- [ ] WhisperX 可选安装
- [ ] 未安装不影响基础功能
- [ ] 单词级时间戳
- [ ] 规则切分回退
- [ ] 语义切分
- [ ] 高质量三步翻译

## 配音

- [ ] Qwen3-TTS 独立环境
- [ ] Qwen3-TTS 0.6B 标准模型
- [ ] 预设音色
- [ ] Voice Design 可选
- [ ] 授权 Voice Clone 可选
- [ ] 参考音频隐私清理
- [ ] 模型加载和卸载
- [ ] GPU OOM 回退
- [ ] 句级缓存
- [ ] 时长适配
- [ ] 原声音量
- [ ] 音频混合
- [ ] 配音成片
- [ ] 失败句独立重试
- [ ] Edge-TTS 轻量回退
- [ ] TTS 未安装不影响字幕翻译

## 工程质量

- [ ] README 更新
- [ ] LICENSE
- [ ] THIRD_PARTY_NOTICES
- [ ] VideoLingo Apache-2.0 副本
- [ ] 单元测试
- [ ] API 测试
- [ ] 集成测试
- [ ] 中文提交
- [ ] 工作树干净
- [ ] 无密钥
- [ ] 无模型
- [ ] 无媒体产物
- [ ] 无无关大规模格式化

---

# 27. 最终交付标准

项目最终应做到：

1. 用户下载或导入视频；
2. 选择快速字幕、翻译字幕或配音模式；
3. 选择源语言和目标语言；
4. 生成原文字幕；
5. 使用指定模型翻译；
6. 输出单语或双语字幕；
7. 选择字幕样式；
8. 烧录指定语言字幕；
9. 可选生成指定语言配音；
10. 保留每一步产物；
11. 可编辑字幕后仅重新渲染；
12. 可从失败阶段继续；
13. 基础版和高级组件互不拖累；
14. VideoLingo 相关代码遵循 Apache-2.0；
15. Windows 普通用户可以通过界面完成全部流程。

最终产品定位：

```text
Video2Subtitles
桌面端 AI 视频字幕、翻译、本地化和配音工作台
```

---

# 28. 计划优先级结论

必须优先完成：

```text
阶段 0 → 阶段 1 → 阶段 2 → 阶段 3
```

这四个阶段完成后，项目已经具有实用价值：

```text
下载视频
+ 生成字幕
+ 翻译字幕
+ 双语字幕
+ 烧录指定语言字幕
```

随后再完成：

```text
阶段 4：编辑
阶段 5：WhisperX 和 VideoLingo 高级字幕
阶段 6：配音
阶段 7：安装、打包和稳定性
```

不要把 WhisperX、声源分离、多角色配音、声音克隆放在 MVP 前面。  
先把“翻译字幕＋稳定烧录”做成可靠闭环，再逐步增强。
---

# 29. Qwen3-TTS 阶段可直接复制给 DeepSeek 的执行提示词

```text
继续维护 video2subtitles-desktop 当前功能分支，不要从头重做。

当前字幕翻译、ASS 输出和 FFmpeg 烧录功能必须已经稳定。
本轮执行《Video2Subtitles × VideoLingo 桌面端整合开发计划》的阶段 6，
将 QwenLM/Qwen3-TTS 作为主要本地 TTS 引擎内置到桌面项目。

官方项目：
https://github.com/QwenLM/Qwen3-TTS

核心要求：
1. 不把 Qwen3-TTS、PyTorch 和模型权重装入桌面主程序环境。
2. 新建 qwen3-tts-engine 独立 Sidecar，默认 127.0.0.1:8767。
3. 使用独立虚拟环境，官方 qwen-tts 包，保留 Apache-2.0 声明。
4. 默认模型为 Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice。
5. 提供 0.6B-Base 声音克隆选项。
6. 1.7B CustomVoice、Base、VoiceDesign 作为高级模型。
7. 针对 RTX 4070 12GB 默认推荐 0.6B，不承诺未经实测的 1.7B 显存占用。
8. 加载模型前检测 CUDA 和可用显存；OOM 时卸载并提示改用 0.6B。
9. WhisperX、faster-whisper 和 Qwen3-TTS 不应同时长期占用 GPU，按 Pipeline 阶段释放。
10. 实现 health、models、install、load、unload、voices、custom voice、
    voice design、voice clone、task status 和 cancel API。
11. Localization Engine 通过统一 TTSProvider 调用 8767，不直接 import qwen_tts。
12. 支持预设音色、语言、instruct、批量生成和 segment ID。
13. Voice Clone prompt 对同一任务只计算一次并复用。
14. 每句音频独立缓存，失败只重试失败句。
15. 实现字幕时间窗适配、atempo、警告和短句改写接口。
16. 实现原声音量降低、配音混合和最终视频输出。
17. Edge-TTS 保留为用户允许时的轻量回退，不能静默替换。
18. 做一键安装、模型下载、服务启动、模型管理和试听 UI。
19. 模型可从 ModelScope 或 Hugging Face 下载。
20. FlashAttention 2 只作为可选加速，安装失败必须回退。
21. 参考音频默认不保存；不得写入日志、Git、ChatGPT 包。
22. Voice Clone 必须显示授权确认和一键删除私人音色数据。
23. Qwen3-TTS 未安装或启动失败时，快速字幕、翻译和烧录必须正常。
24. 使用中文 commit 分步提交。
25. 更新 README、THIRD_PARTY_NOTICES.md、LICENSE 清单和测试。

建议提交：
- feat: 新增独立 Qwen3-TTS 本地语音服务
- feat: 完成 Qwen3-TTS 模型管理和语音生成接口
- feat: 增加 Qwen3-TTS 模型分级和 GPU 资源调度
- feat: 将 Qwen3-TTS 接入统一配音 Provider
- feat: 增加 Qwen3-TTS 一键安装和模型管理界面
- feat: 增加 Qwen3-TTS 音色设计和授权声音克隆
- feat: 支持 Qwen3-TTS 句级缓存和配音时长适配
- feat: 完成 Qwen3-TTS 配音音轨混合与视频合成
- feat: 增加 Qwen3-TTS 失败时的可控轻量配音回退

每次提交前运行现有项目测试、新增 Sidecar API 测试和脱敏检查。
完成后报告修改文件、接口、模型支持、GPU 策略、测试结果、
许可证处理、隐私措施、commit 列表和遗留问题。
```

---

# 30. Qwen3-TTS 官方依据

本计划的 Qwen3-TTS 接入依据：

- 官方仓库：`https://github.com/QwenLM/Qwen3-TTS`
- 官方 Python 包：`qwen-tts`
- 官方许可：Apache-2.0
- 官方推荐隔离环境；
- 支持 Python 3.9～3.13；
- 已发布 0.6B 和 1.7B 模型；
- 支持 CustomVoice、VoiceDesign 和 VoiceClone；
- 支持 10 种主要语言；
- 支持本地模型目录、Hugging Face 和 ModelScope 下载；
- FlashAttention 2 为推荐优化，不是基础功能的强制依赖。

“内置”定义为：

```text
桌面程序统一管理
+ 一键安装
+ 一键下载模型
+ 自动启动 Sidecar
+ UI 中直接选择和使用
```

而不是：

```text
把模型权重和 PyTorch 打入主 EXE
```
