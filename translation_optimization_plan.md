# 字幕翻译优化开发计划

> 目标：减少翻译 token 消耗（输入与输出）、提升大批量字幕翻译速度、降低 API 请求次数。
> 范围：仅优化 `localization-engine/translation/` 与相关配置/测试，不改 TTS、渲染、字幕生成等其它管线阶段。
> 兼容性底线：翻译结果与现有产物（SRT/ASS）保持一致；旧 checkpoint 仍可用；现有测试不得回归。

---

## 0. 给执行模型的上下文

- 平台：Windows PowerShell 5.1，工作目录 `D:\software\video_2_subtitles`
- Python 依赖见 `requirements.txt`；测试使用标准库 `unittest`（无 pytest）
- 运行测试命令（在仓库根目录执行）：
  ```powershell
  python -m unittest discover -s tests -p "test_*.py" -v
  ```
  重点回归测试文件：
  - `tests/test_translation_parser.py`
  - `tests/test_localization_engine.py`
  - `tests/test_provider_presets.py`
- 严禁：未经要求不要 `git commit`/`git push`；不要新增依赖；不要添加注释（除非用户要求）；不要改动与本次优化无关的文件。
- 编辑前必须先 `Read` 目标文件；遵循现有代码风格（`from __future__ import annotations`、类型注解、4 空格缩进）。

### 现状关键文件与行号
- `job_models.py:350-365` —— `TranslationConfig` 默认值
- `localization-engine/translation/batching.py:14-58` —— `batch_segments`
- `localization-engine/translation/prompts.py:7-30` —— 系统提示与翻译提示
- `localization-engine/translation/openai_compatible.py:182-207` —— `_build_payload`（payload 构造）
- `localization-engine/translation/openai_compatible.py:261-436` —— `translate_batch`
- `localization-engine/translation/response_parser.py:9-98` —— 响应解析
- `localization-engine/engine/pipeline.py:486-746` —— `_run_translation`（批调度、并发、二分重试）
- `client_settings.py:46-47` —— 默认并发/批条数
- `ui/localization_dialog.py:576-579,666-669,960-989` —— UI 并发/批条数 spinbox
- `ui/provider_presets_dialog.py:226-242,269-271,360-403` —— 预设 UI 并发/批条数

---

## 阶段 1：低风险参数与提示优化（不改解析器，可独立合入）

预计收益：输入 token -10~15%、请求次数 -50~80%、速度 +2~3 倍。风险极低。

### 任务 1.1 提高默认批次上限
**文件**：
- `job_models.py:361-362` —— `max_batch_chars: int = 4000` → `16000`；`max_batch_items: int = 10` → `50`
- `client_settings.py:47` —— `"translation_max_batch_items": "10"` → `"50"`
- `provider_presets.py:399` —— `_int(settings.get("translation_max_batch_items"), 10)` 把兜底 `10` 改为 `50`
- `localization-engine/translation/batching.py:17` —— 函数签名默认 `max_items: int = 30` → `50`；`max_chars: int = 4000` → `16000`

**验收**：`TranslationConfig()` 实例的 `max_batch_items == 50`、`max_batch_chars == 16000`；`batch_segments` 在 50 条以内、总字符 ≤16000 时返回单批。

### 任务 1.2 提高 Anthropic 分支 max_tokens 上限
**文件**：`localization-engine/translation/openai_compatible.py:193`
- `"max_tokens": 4096` → 动态计算 `max(4096, min(8192, sum(len(s["text"]) for s in segments) * 4))`
- 把该计算放在 `_build_payload` 内，仅 `anthropic_messages` 分支生效。注意 `segments` 此时不在作用域，需将 `len(segments)` 或估算值作为新参数传入 `_build_payload`。

**实现要点**：扩展 `_build_payload` 签名为 `_build_payload(self, endpoint, model, system_prompt, user_prompt, temperature, estimated_output_chars: int = 4096)`；调用处 `openai_compatible.py:304` 传入 `estimated_output_chars = sum(len(s["text"]) for s in segments)`。

**验收**：50 条平均 40 字符的字幕，Anthropic 请求 payload 中 `max_tokens ≥ 4096` 且 ≤ 8192；现有 `test_claude_auto_uses_anthropic_messages_first` 等测试仍通过。

### 任务 1.3 精简 SYSTEM_PROMPT
**文件**：`localization-engine/translation/prompts.py:7-21`
- 将 12 条规则压缩为 5 条核心规则，保留：JSON 数组格式、id 不变、目标语言自然化、简体中文/专有名词规则、不要源语言残留。
- 目标长度从 ~700 字符降到 ~300 字符。
- 保留 `{source_lang}` / `{target_lang}` 占位符，`build_system_prompt` 签名不变。

**验收**：`build_system_prompt("en", "zh-CN")` 返回字符串长度 < 500 字符；包含 "JSON array"、"id"、"Simplified Chinese" 关键词；现有测试通过。

### 任务 1.4 提高并发默认值与 UI 上限
**文件**：
- `job_models.py:364` —— `concurrency: int = 2` → `4`
- `client_settings.py:46` —— `"translation_concurrency": "2"` → `"4"`
- `provider_presets.py:400` —— 兜底 `2` 改 `4`
- `ui/localization_dialog.py:577` —— `self.trans_concurrency.setRange(1, 8)` → `setRange(1, 16)`；`578` 行默认值仍读 settings（无需改）
- `ui/provider_presets_dialog.py:227` —— `setRange(1, 16)` → `setRange(1, 32)`

**验收**：`TranslationConfig().concurrency == 4`；UI spinbox 最大值显示为 16/32；`pipeline.py:531-532` 的 `min(concurrency, total)` 自动收敛仍生效。

### 任务 1.5 阶段 1 联调验证
- 运行：`python -m unittest tests.test_translation_parser tests.test_localization_engine tests.test_provider_presets -v`
- 全部通过后进入阶段 2。

---

## 阶段 2：输入/输出格式紧凑化（核心收益，需改解析器）

预计收益：输出 token -30~50%、输入 token -10~20%。中等风险，必须同步改解析器与测试。

### 设计决策：输出格式
采用 **顺序对齐的纯文本行格式**（省 id、省 JSON 括号引号）：
```
译文第一行
译文第二行
译文第三行
```
解析时按 `expected_ids` 顺序逐行对齐；空行视为空译文（保留现有 "Empty translations" 错误路径）。同时**保留 JSON 解析作为兼容回退**，当响应以 `[` 开头时走旧路径，确保对老模型/不同 provider 的鲁棒性。

### 设计决策：输入格式
采用 **TSV**（id\ttext）替代 JSON：
```
1\tHello world
2\tGood morning
```
比 JSON `[{"id":1,"text":"Hello world"}]` 省括号、引号、逗号 token。

### 任务 2.1 新增紧凑格式 prompt 模板
**文件**：`localization-engine/translation/prompts.py`
- 新增常量 `TRANSLATE_PROMPT_COMPACT`，要求模型：
  - 输入为 TSV（每行 `id<TAB>text`）
  - 输出为纯文本，每行一条译文，**顺序与输入一致**，不要 id、不要引号、不要 JSON
  - 保持原行数；若某条无法翻译则输出空行占位
- 新增 `build_translate_prompt_compact(segments_tsv, source_lang, target_lang, count, glossary_text)` 函数
- 同步精简 `SYSTEM_PROMPT` 中 "Return ONLY a valid JSON array" 一句，改为支持两种格式描述（或新增 `SYSTEM_PROMPT_COMPACT`）。**注意：系统提示由两个 prompt 共用，改 SYSTEM_PROMPT 会影响 JSON 路径**——稳妥做法是新增 `SYSTEM_PROMPT_COMPACT` 独立常量，由紧凑路径使用。

**验收**：新函数返回字符串包含 "TSV"、"one translation per line"、"exactly {count} lines"。

### 任务 2.2 扩展响应解析器支持行格式
**文件**：`localization-engine/translation/response_parser.py`
- 新增函数 `parse_translation_response_compact(response_text: str, expected_ids: List[int]) -> Tuple[List[Dict], List[str]]`
- 逻辑：
  1. `text.strip()`；若以 `[` 开头则委托给原 `parse_translation_response`（兼容回退）
  2. 按 `\n` 拆行，去除每行首尾空白；丢弃完全空行仅当其数量 > expected 时记为 error
  3. 行数 < `len(expected_ids)` → error "missing lines"；行数 > → 多余行截断并记 error
  4. 按 `expected_ids` 顺序配对生成 `[{"id": id, "text": line}]`
  5. 空行 → 记 "Empty translations for IDs"
- 不修改原 `parse_translation_response`，保证旧测试不回归

**验收**：
- `parse_translation_response_compact("你好\n世界", [1,2])` → `[{"id":1,"text":"你好"},{"id":2,"text":"世界"}], []`
- `parse_translation_response_compact('[{"id":1,"text":"你好"}]', [1])` → 走 JSON 回退，结果同上
- `parse_translation_response_compact("你好\n\n", [1,2,3])` → errors 含 "missing lines"

### 任务 2.3 新增 TSV 输入构造
**文件**：`localization-engine/translation/batching.py`
- 新增函数 `batch_to_tsv(batch: List[SubtitleSegment]) -> str`，返回 `id\ttext` 多行字符串
- 保留原 `batch_to_request`（JSON 路径仍需）

**验收**：`batch_to_tsv([seg(index=1,text="a"), seg(index=2,text="b")])` → `"1\ta\n2\tb"`

### 任务 2.4 在 provider 中接入紧凑路径
**文件**：`localization-engine/translation/openai_compatible.py`
- `TranslationConfig` 新增字段 `output_format: Literal["json", "compact"] = "compact"`（默认紧凑，可在 UI 切换回 json 兜底）。**需要改 `job_models.py:350-365` 加该字段**。
- `translate_batch`（`openai_compatible.py:261`）分支：
  - `output_format == "compact"`：用 `batch_to_tsv` 生成输入、`build_translate_prompt_compact` 生成 user_prompt、`build_system_prompt_compact` 生成 system_prompt、调用 `parse_translation_response_compact` 解析
  - `output_format == "json"`：走现有逻辑
- `expected_ids` 仍为 `[s["id"] for s in segments]`，传入解析器
- compact 路径的 `fallback`/二分重试（`pipeline.py:548`）天然兼容，因为返回结构仍是 `[{"id","text"}]`

**验收**：新增单元测试：mock provider 返回行格式响应，`translate_batch` 返回正确 dict 列表；现有 JSON 路径测试（`test_translation_parser.py` 全部用例）不回归。

### 任务 2.5 UI 增加"输出格式"开关（可选，但建议）
**文件**：`ui/localization_dialog.py`（翻译设置区，约 `576-579` 附近）
- 新增 `QComboBox`：选项 "紧凑文本（省 token，推荐）" / "JSON（兼容）"
- 保存到 `client_settings` 的 `translation_output_format`，默认 `compact`
- `provider_presets.py:399-403` 映射时带上 `output_format`
- `job_models.TranslationConfig.from_dict` 已自动吸收新字段（`job_models.py:372-374` 过滤已知字段）

**验收**：切换后 `TranslationConfig.from_dict({"output_format":"json"}).output_format == "json"`；未设置时默认 `compact`。

### 任务 2.6 阶段 2 测试与回归
- 在 `tests/test_translation_parser.py` 新增 `TestCompactParser` 测试类，覆盖：正常行格式、空行、行数不匹配、JSON 回退、markdown 代码块包裹的纯文本
- 在 `tests/test_localization_engine.py` 找一个现有 mock provider 用例，复制一份用 compact 格式验证端到端
- 运行：`python -m unittest discover -s tests -p "test_*.py" -v`
- 全绿后进入阶段 3。

---

## 阶段 3：进阶加速（可选，按需启用）

预计收益：首 token 延迟 -50~80%（流式）、prompt cache 命中后输入 token 近乎免费。需要 provider 支持，需加能力探测。

### 任务 3.1 流式响应（stream）
**文件**：`localization-engine/translation/openai_compatible.py`
- `_build_payload` 对 `chat_completions`/`anthropic_messages` 分支加 `"stream": True`
- `_post_endpoint` 改用 `self.client.stream("POST", url, json=payload, headers=..., timeout=timeout)`，迭代 `response.iter_lines()` 累积 content
- 解析 SSE：`chat_completions` 取 `delta.content`；`anthropic_messages` 取 `content_block_delta.text`
- 累积完成后传给现有 `parse_translation_response[_compact]`
- **流式失败回退**：若 provider 不支持 stream（返回 400 含 "stream"），关闭 stream 重试一次

**验收**：新增 mock SSE 测试；非流式路径保留；`test_responses_api_translation` 等仍通过（responses 端点不强加 stream）。

### 任务 3.2 Prompt Caching（Anthropic / OpenAI 官方）
**文件**：`localization-engine/translation/openai_compatible.py`
- 仅 `anthropic_messages` 分支：在 system prompt 块加 `{"type":"text","text": system_prompt, "cache_control":{"type":"ephemeral"}}`
- OpenAI 官方（非兼容）自动缓存长前缀，无需改 payload，但确保 system_prompt 跨批次完全相同（已是常量，天然满足）
- 自建 vLLM/Ollama 等兼容服务器多数不支持 cache_control，需 try/except 忽略 400 中含 "cache_control" 的错误，回退到普通 system 字符串

**验收**：Anthropic mock 测试 payload 含 `cache_control`；不支持的 provider 不报错。

### 任务 3.3 阶段 3 验证
- 仅当任务 3.1/3.2 实施时运行全量测试
- 注意：流式与缓存对 mock 框架（`httpx.MockTransport`）要求较高，测试可只验证 payload 构造，不强求端到端流式 mock

---

## 验收清单（全部完成后）

- [ ] `python -m unittest discover -s tests -p "test_*.py" -v` 全绿
- [ ] `TranslationConfig` 默认：`max_batch_items=50, max_batch_chars=16000, concurrency=4, output_format="compact"`
- [ ] 用一个 100 条字幕的 SRT 实测：请求数从 ~10 降到 ≤2；输出 token 肉眼可见减少（可在 `openai_compatible.py:352` 后临时打印 `len(content)` 对比）
- [ ] 切换 `output_format="json"` 后行为与优化前完全一致（兼容回退有效）
- [ ] 旧 checkpoint（`completed_ids.json`）仍能跳过已翻译段
- [ ] 未新增第三方依赖；未改动 TTS/渲染/字幕导出代码

## 不在本次范围
- TTS 合并发翻译并行（属于管线调度改造，另行立项）
- 多模型路由/故障转移
- 翻译质量反思（reflect/adapt）prompt 的 token 优化（当前管线默认 `quality_mode="fast"` 未启用反思，收益小）

## 风险与回滚
- 阶段 1 各任务相互独立，可单独回滚
- 阶段 2 若紧凑格式在某个 provider 上表现差，用户可在 UI 切回 `json`，无需回滚代码
- 阶段 3 每个特性失败不影响阶段 1/2 收益
- 若批条数过大导致单批超时/截断，`pipeline.py:548` 的二分重试会自动收敛，但建议在 `provider_presets.py` 的预设里为弱模型保留小批条数覆盖
