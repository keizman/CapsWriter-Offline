# HTTP Transcript API

服务端在启动后会同时提供：
- WebSocket 语音流接口（原有）
- HTTP 文件转录接口（新增）

默认监听：
- WS: `0.0.0.0:6016`
- HTTP: `0.0.0.0:6017`

可在 `config_server.local.json` 的 `server` 字段覆盖：

```json
{
  "server": {
    "http_enable": true,
    "http_addr": "0.0.0.0",
    "http_port": "6017",
    "http_max_upload_mb": 200,
    "http_timeout_secs": 600,
    "http_seg_duration": 60,
    "http_seg_overlap": 4
  }
}
```

## Endpoint

`POST /api/transcript`

- Content-Type: `multipart/form-data`
- 必填字段: `file`（音频文件）
- 可选字段:
  - `context`: 识别上下文
  - `seg_duration`: 分段时长（秒）
  - `seg_overlap`: 分段重叠（秒）
  - `timeout_secs`: 单次请求超时（秒）

如果服务端配置了 `secret`，请求头需要带：
- `X-CapsWriter-Secret: <your-secret>`

## Health Check

`GET /api/healthz`

返回：

```json
{"ok": true, "status": "running"}
```

## curl 示例

### 1) 最简请求

```bash
curl -X POST "http://127.0.0.1:6017/api/transcript" \
  -F "file=@/path/to/audio.wav"
```

### 2) 带 secret + 自定义分段

```bash
curl -X POST "http://127.0.0.1:6017/api/transcript" \
  -H "X-CapsWriter-Secret: replace-with-your-secret" \
  -F "file=@/path/to/audio.wav" \
  -F "context=会议纪要" \
  -F "seg_duration=45" \
  -F "seg_overlap=3" \
  -F "timeout_secs=900"
```

## 返回格式

成功时返回（示例）：

```json
{
  "ok": true,
  "task_id": "...",
  "filename": "audio.wav",
  "duration": 8.92,
  "time_start": 1739740000.1,
  "time_submit": 1739740000.7,
  "time_complete": 1739740001.3,
  "text": "this is a test",
  "text_accu": "this is a test",
  "tokens": [],
  "timestamps": [],
  "is_final": true
}
```

失败时返回：

```json
{"ok": false, "error": "..."}
```

常见状态码：
- `400`: 请求格式错误（例如未上传 `file`）
- `403`: secret 不匹配
- `500`: 转码/服务内部错误
- `504`: 排队或识别超时

## 依赖

优先使用 `ffmpeg` 对上传文件进行转码（统一转成 `f32le/16k/mono`）。  
若系统未安装 `ffmpeg`，当前仅支持上传 `.wav`（服务端会走内置 WAV 回退解码）。
