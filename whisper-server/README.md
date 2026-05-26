# 内置 Whisper 服务

这是 Video2Subtitles 桌面端内置的轻量 sidecar 服务，用来处理在线链接下载、文件上传转写和任务状态查询。

## 启动方式

通常不需要手动启动。运行项目根目录的 `start.bat` 或 `python app.py` 时，客户端会自动拉起本服务。

手动启动：

```bash
cd whisper-server
python main.py
```

默认监听：

```text
http://127.0.0.1:8765
```

## 模型目录

服务端和客户端本地 fallback 共用同一组环境变量：

| 变量 | 说明 |
|---|---|
| `WHISPER_MODEL_DIR` | 模型缓存目录，默认项目根目录 `models/` |
| `WHISPER_MODEL_PATH` | 指定具体 CTranslate2/faster-whisper 模型目录 |
| `MODEL_SIZE` | 模型名，如 `base`、`small`、`large-v3-turbo` |
| `DEVICE` | `cpu` 或 `cuda`，默认 `cpu` |
| `COMPUTE_TYPE` | 默认 `int8` |

## 在线链接

在线链接下载依赖 `yt-dlp`。如果遇到 YouTube 风控，可以把 `cookies.txt` 放在本目录下，服务会自动使用它。

## API

- `GET /health`
- `POST /transcribe`
- `POST /upload`
- `GET /status/{task_id}`
- `GET /task/{task_id}`
