# Phase 3：交互体验与本地资源智能管理

## 1. 修改前架构结论

- 桌面端是 PyQt5 单体应用；浏览器前端不存在。桌面端通过 `requests` 访问三个仅绑定回环地址的 FastAPI sidecar：Whisper、Localization Engine、Qwen3-TTS。
- Localization Engine 已有任务创建、状态、取消、重试、历史、详情和日志 API；桌面端按秒轮询。实时进度原本只在 `ProgressTracker` 内存中，任务、阶段运行、产物和事件由 SQLite 持久化。
- 真实 Pipeline 顺序为 `prepare → normalize → translate → subtitle_export → tts → audio_mix → render → finalize`。
- Phase 2 基础设施已真实存在并被复用：`WorkspaceManager`、统一 `PipelineError`、`SQLiteTaskRepository`、`ArtifactManager`、Stage Registry、`RetryPlanner`、运行租约及异常退出恢复。
- Whisper sidecar 原本缓存一个 `faster-whisper` 模型并支持卸载；Qwen3-TTS 原本使用 singleton 模型管理器；两者都缺少“使用中禁止卸载”的租约语义。当前翻译仅有远端 OpenAI-compatible Provider，不存在可接入的本地翻译实现。
- TTS Registry 原本只声明构造和 voice list；字幕编辑核心原本使用浮点秒 `SubtitleSegment`，没有稳定 cue ID、草稿和不可变修订。
- SQLite migration 机制是幂等建表加 `schema_migrations` 版本记录，而不是独立 migration 工具。

## 2. Phase 3 架构

### Runtime 与 Preflight

- `engine.runtime.capabilities`：CPU、内存、磁盘、FFmpeg、FFprobe、CUDA/GPU 能力检测；所有外部命令都有超时。
- `engine.runtime.gpu`：`GPUMonitor` 抽象、`NvidiaSmiMonitor` 和 `NullGPUMonitor`。未安装 NVIDIA 驱动或查询失败时返回空指标和诊断文本，不阻止应用启动。
- `RuntimeMonitor`：活动任务 2 秒、空闲 10 秒采样；最多保留 300 个内存快照，不逐秒写 SQLite；阶段切换事件保存资源摘要；shutdown 主动停止线程。
- `PreflightChecker`：结构化检查输入、工作区写权限、磁盘、FFmpeg/FFprobe、翻译/TTS 配置、设备/显存兼容性和输出目录安全性。`errors` 阻止启动，`warnings` 由 PyQt 主线程明确确认后再提交。

### 本地模型生命周期

- `ModelDefinition`、`ModelLease`、`ModelResourceManager`、`ModelResourcePolicy` 和 `ModelState` 提供模型复用、引用计数、模型级锁、加载超时、失败状态、immediate/idle/keep_loaded 策略、空闲卸载及显存压力卸载。
- 同一 sidecar 使用 `resource_group` 互斥；另一个模型不能在旧模型仍有 lease 时切换。重复 release 幂等；超时后迟到的 loader 结果会调用 unloader 清理。
- Qwen 正式 TTS 与试听通过远端模型定义进入统一 manager；低显存任务使用 immediate 策略。Whisper 新增显式 warm-load API，实际转写期间持有 sidecar 内部 lease；Qwen Synthesizer 同样在每次真实推理期间持有 lease。
- 当前无本地翻译 Provider；Pipeline 已支持 Provider 暴露 `model_definition()` 后自动 acquire/release。远端 API Provider 没有该声明，因此不会进入本地模型管理器。

### TTS capability、试听与预设

- Registry 为每个现有 Provider 声明 voice list、speed、pitch、emotion、language、streaming、试听字符上限、输出格式和允许参数。
- Pipeline 在调用 Provider 前过滤参数，注册 Provider 不会收到不支持的参数；第三方测试注入的非注册 Provider 保留兼容路径。
- `TTSPreviewService` 使用独立临时缓存，不创建正式视频任务；支持字符限制、voice 校验、超时、取消、TTL 清理和相同参数缓存。API key/secret/token/access key 不进入缓存键、文件名、metadata 或预设。
- `voice_presets` CRUD 支持默认预设。创建正式任务时把预设与显式覆盖项合并，并把完整实际参数快照写入任务 request payload；以后修改预设不会改变已有任务。

### 字幕文档、编辑和修订

- 编辑核心为 `SubtitleCue` 和 `SubtitleDocument`；时间只用整数毫秒。原始 cue ID 根据任务和原始内容生成稳定 UUID，新 cue 使用 UUID4，从不使用数组下标。
- `SubtitleEditor` 命令支持原文/译文/时间更新、插入、删除、拆分、合并、批量平移、查找替换、撤销和重做。
- `SubtitleValidator` 返回 `cue_id/severity/code/message/suggestion`，覆盖负时间、结束早于开始、重复 ID、空字幕、顺序、重叠、时长、字符数、阅读速度及疑似未翻译。
- 草稿和正式修订分开：草稿原子写入 task `work` 目录；正式 JSON revision 通过 `ArtifactManager` 写入 task artifact 目录，带 SHA-256。SQLite 只保存文档/修订 metadata。
- 正式保存使用 `base_version` 乐观锁；冲突返回 `SUBTITLE_VERSION_CONFLICT`。历史恢复会创建新的正式版本，不改写历史文件。
- 原始 transcription/translation 文件始终只读。下游重试通过任务 payload 内部的 `current_subtitle_path` 读取正式编辑版本。

### 产物失效和重新生成

- 新正式修订生成新的 `current_subtitle` artifact，旧 current artifact 自动变成非当前；所有 immutable revision 保留。
- 只失效 `tts/audio_mix/render/finalize` 阶段产物，保留 normalize/translate 上游结果。
- “仅保存字幕”只保存和失效；“保存并重新生成”调用既有 `RetryPlanner` 从 `tts` 规划，沿用原有并发 run lease，并在 Pipeline 结束时释放 lease。

### 事件、错误指导和 UI

- `PipelineEvent` 覆盖 task/stage/model/artifact/warning。进度发布器按时间、增量和内容去重；阶段切换时保存资源摘要。
- `ErrorGuidanceRegistry` 只返回固定白名单 action ID；错误文本不能决定任意前端动作。
- PyQt 新增：资源 Dashboard、TTS 试听/播放器/预设、任务运行详情、字幕视频预览与单轨时间轴。时间轴支持播放同步高亮、点击跳转、拖动、左右边界、表格编辑、撤销/重做、自动保存、未保存提示、校验定位和修订历史。第一版不包含波形或多轨编辑。

## 3. SQLite migration

`SCHEMA_VERSION = 3`，全部 migration 保持幂等：

1. `v1 initial_task_repository`：既有 tasks、stage runs、artifacts、events。
2. `v2 voice_presets`：`id/name/provider/voice_id/language/parameters_json/is_default/created_at/updated_at`；name 大小写不敏感唯一，默认预设原子切换。
3. `v3 subtitle_documents_and_revisions`：
   - `subtitle_documents`：task 一对一、current version/revision。
   - `subtitle_revisions`：version、base version、artifact path、checksum、draft 标识和时间。
   - partial unique indexes 保证正式版本唯一、每文档只有一个草稿。

完整字幕 JSON 不存入 SQLite。

## 4. Phase 3 主要文件

- Runtime：`localization-engine/engine/runtime/*`
- Preflight/API：`localization-engine/main.py`
- 事件：`localization-engine/engine/events.py`、`engine/progress.py`
- 模型接入：`engine/pipeline.py`、`whisper-server/main.py`、`qwen3-tts-engine/engine/model_manager.py`、`qwen3-tts-engine/engine/synthesis.py`
- TTS：`localization-engine/tts/base.py`、`registry.py`、`preview.py`、`__init__.py`
- 数据库：`localization-engine/engine/sqlite_repository.py`
- 字幕：`localization-engine/subtitles/document.py`、`document_service.py`、`document_validator.py`、`commands.py`
- 桌面端：`localization_client.py`、`main_window.py`、`ui/runtime_dashboard.py`、`ui/tts_preview_dialog.py`、`ui/subtitle_timeline_dialog.py`、`ui/task_runtime_dialog.py`
- 测试：`tests/test_phase3_*.py` 及 Whisper/Qwen lease 回归测试。

## 5. 提交说明

- `3c55333` runtime/GPU capability detection
- `ba258de` adaptive resource monitor
- `dbfbc19` structured preflight
- `56aea8c` model lifecycle manager and leases
- `4a6215d` TTS capability and preview core
- `e78b97f` voice preset persistence
- `a88e776` subtitle documents and immutable revisions
- `6e493c0` subtitle validator/edit commands
- `524eecd` local model/pipeline/Whisper/Qwen lease integration
- `a67b5fb` capability filtering and preview model leases
- `4e017ee` downstream regeneration source handoff
- `3507f1b` Phase 3 API、events 和 runtime visualization backend
- `9449249` timeline、TTS preview、resource/task runtime PyQt views
- 最后提交记录 tests/docs。

## 6. 验证命令和结果

- `python -m pytest -q`：`779 passed, 1 warning`。
- Phase 3 focused suite：`111 passed`。
- `python tools/check_project.py`：语法检查通过，轻量 gate `471 tests OK`。
- `python -m compileall -q .`：通过。
- Phase 3 文件 `python -m ruff check ...`：通过。
- `git diff --check`：通过（只有 Git 的 LF→CRLF 提示）。
- `python -m mypy --version`：未执行类型检查，当前环境没有安装 mypy（`No module named mypy`）；项目也没有既有 mypy 配置。
- repository-wide `python -m ruff check .`：未通过，报告 252 个已有 lint 问题，主要是旧代码动态调整 `sys.path` 导致 E402 以及既有未使用 import；Phase 3 scoped gate 为绿色。
- `python -m pip check`：当前共享 Python 环境有 Chainlit/Gradio/LangChain/Qwen 等跨项目版本冲突；这些不是 Phase 3 新依赖造成。新增 `psutil>=5.9` 自身可用。

## 7. 性能与资源占用对比

CPU-only 微基准（同一机器、`NullGPUMonitor`）：

- 500 次采样耗时 2.832 秒，平均 5.664 ms/次。
- 滚动窗口只保留 300/500 个快照；进程 RSS 增量约 1,228,800 bytes。
- 活动任务 2 秒采样时，CPU-only 采样约占单核 0.28%；空闲 10 秒采样约占单核 0.06%。NVIDIA 查询成本取决于驱动和 `nvidia-smi`。
- 修改前无统一资源采样；修改后内存固定上限，SQLite 仍为 0 条逐秒 resource 写入。
- 1,001 个完全相同的 progress 更新只持久化 1 个 progress event（另有 1 个 stage-start event），避免字符级/帧级数据库写入。
- 模型复用测试证明同 model ID 并发 acquire 只调用一次 loader；复用避免重复模型显存/内存占用。真实 Qwen/Whisper 显存数值依模型、设备和 dtype，需在目标 GPU 验收。

## 8. 未解决风险

- 没有在本机执行真实 NVIDIA/Qwen/Whisper 长任务显存压力端到端测试；已用成功、失败、超时、并发和 pressure fake 覆盖控制逻辑。
- 项目没有本地翻译 Provider；仅实现统一生命周期接入协议，远端翻译保持在 manager 外。以后加入本地 Provider 时必须实现 `model_definition()`。
- Qt Multimedia 的实际解码能力依 Windows codec/Qt 安装；无 multimedia 时编辑器会降级为无视频预览模式。
- 直接调用旧 `/jobs` API 默认保持兼容；新桌面客户端显式启用 strict preflight。第三方调用方应迁移到 `enforce_preflight=true`。
- 共享 Python 环境的 `pip check` 冲突和 repository-wide lint debt 仍需独立环境/后续清理。

## 9. 人工验收步骤

1. CPU-only 机器启动桌面端和 Localization Engine，打开“资源”，确认无 GPU 时 UI 正常、CPU/内存/磁盘更新。
2. 临时移除 FFmpeg PATH，创建任务，确认 Preflight error 阻止启动；构造低磁盘 warning，确认必须点击“是”才继续。
3. 启动 Qwen sidecar，连续提交相同模型任务，确认 model 只加载一次、引用数变化；任务运行中请求 unload 应返回 `MODEL_IN_USE`/失败。
4. 打开“语音试听”，切换 Provider，确认参数动态显示、字符限制、播放、取消、缓存命中和预设 CRUD；检查正式任务历史没有试听记录。
5. 完成一个本地化任务后右键“编辑字幕时间轴”，验证视频同步、列表、拖动/边界、全部编辑命令、撤销/重做和校验定位。
6. 修改后等待自动保存，强制关闭并重启应用，确认草稿恢复；保存正式版本后检查历史和 SHA-256。
7. 用两个编辑窗口基于同一 version 保存，确认后者收到 `SUBTITLE_VERSION_CONFLICT`。
8. 选择“仅保存字幕”，确认旧 TTS/混音/最终视频 artifact 非当前而上游保留；选择“保存并重新生成”，确认从 TTS 阶段开始并使用编辑后的文本。
9. 打开“任务运行详情”，确认总进度、阶段进度/耗时、资源、模型、事件、错误码、白名单建议和完整日志入口。
