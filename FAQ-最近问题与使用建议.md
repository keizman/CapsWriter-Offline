# CapsWriter-Offline 最近问题与使用建议

更新时间：2026-02-13

## 1. 模型与目录

- 本项目里是 `LLM/` 目录，不是 `LM/`。
- `LLM/` 主要用于“角色配置”（翻译、助理、润色等），不是语音模型目录。
- 语音模型在 `models/` 下（Fun-ASR-Nano / SenseVoice / Paraformer / Punct）。

## 2. `hot.txt` / `hot-rule.txt` / `hot-server.txt` / `hot-rectify.txt` 区别

### `hot.txt`（客户端音素热词）
- 作用：对识别文本做音素模糊匹配，达到阈值后替换。
- 特点：偏“智能模糊纠错”，适合专有名词（如 `codex`）。

### `hot-rule.txt`（客户端强制规则）
- 作用：正则替换，强制执行。
- 特点：最确定，适合固定错法。
- 例子：
```txt
[口扣叩]ex = codex
code\\s*x = codex
```

### `hot-server.txt`（服务端 Fun-ASR-Nano 专用）
- 作用：给 Fun-ASR-Nano 解码器提供“热词提示”，是建议性，不是强制替换。
- 仅 `model_type = 'fun_asr_nano'` 时有效。
- 你当前配置是 `sensevoice`，所以它当前不生效。

### `hot-rectify.txt`（LLM 纠错记忆）
- 作用：给 LLM 角色当上下文参考（错句 -> 正句历史）。
- 不是强制替换。
- 如果默认不走 LLM（`LLM/default.py` 里 `process = False`），它基本不会直接影响普通语音上屏。

## 3. 阈值怎么理解

- `hot_thresh`：达到这个分数才会真正替换（高=更保守，低=更激进）。
- `hot_similar`：潜在相似热词阈值，主要用于提示/LLM上下文。
- `hot_rectify`：`hot-rectify.txt` 检索阈值（用于 LLM 参考）。

## 4. 改完文件是否需要重启

### 改 `hot.txt` / `hot-rule.txt` / `hot-rectify.txt`
- 一般不需要重启，客户端有文件监控，会自动重载（有几秒防抖延迟）。
- 若没生效，再重启客户端。

### 改 `hot-server.txt`
- 服务端 `fun_asr_nano` 模式下支持自动热更新。
- 若没生效，再重启服务端。

## 5. RustDesk 窗口里只输入 `020` 的问题

### 现象
- 本机窗口正常。
- 焦点在 RustDesk 窗口时，语音结果异常（如只出 `020`）。

### 原因
- 默认“模拟打字”在远控窗口里可能被错误解释。

### 已做修复
- 已把 `RustDesk` 加入自动兼容名单：检测到 RustDesk 前台窗口时，自动切为“粘贴输出（paste）”。
- 修改文件：`util/client/output/result_processor.py`

### 生效方式
- 只需重启客户端 `start_client.exe`。

## 6. 为什么不全局一直使用 `paste`

可以全局 `paste=True`，但默认不全开，主要因为：

1. 会占用/改写剪贴板（需要恢复，和你手动复制可能冲突）。
2. 并非所有目标应用都稳定支持 `Ctrl+V`。
3. 有额外时序开销（剪贴板读写与恢复）。
4. 某些受控输入框可能禁止粘贴。

结论：  
- 远控场景：自动按窗口切到 paste（当前已做）更稳。  
- 本机场景：保留打字模式通常更自然。

## 7. 快捷键建议（当前已调整）

- 原来：`caps_lock` 容易触发大小写切换。
- 现在建议：主键 `ctrl_r`，保留鼠标 `x2`。
- `caps_lock` 已改为备用关闭，避免误切换。
- 配置文件：`config_client.py`

## 8. 推荐使用策略（最省心）

1. 先用 `hot.txt` 放专有名词。
2. 固定错法用 `hot-rule.txt` 强制修正。
3. 暂不依赖 `hot-rectify.txt`（除非你明确要用 LLM 润色）。
4. 远控（RustDesk）场景保持当前自动 paste 兼容逻辑。
5. 改配置后优先重启客户端验证。



-----

Mac plan

• 目标
  在 macOS 上先实现“可用版”（能按键录音、识别、上屏），再逐步追平 Windows 功能。

  推荐路线（分两阶段）

  1. 阶段 1：MVP（先能用，预计 3-5 天）

  - 去掉对 keyboard 的硬依赖，全部改为 pynput 或现有剪贴板粘贴路径。
    涉及：util/client/output/text_output.py, util/llm/llm_get_selection.py, util/llm/llm_output_typing.py, util/client/output/result_processor.py
  - 新增 macOS 快捷键监听实现，不走 win32_event_filter。
    涉及：util/client/shortcut/shortcut_manager.py, util/client/shortcut/key_mapper.py
  - macOS 默认强制 paste 输出（先保证稳定输入，不追求逐字打字）。
    涉及：config_client.py, util/client/output/result_processor.py
  - 关闭托盘能力（先不做 Mac 托盘），避免 ctypes.windll 路径。
    涉及：util/client/startup.py, util/ui/tray.py
  - 修正错误的 sudo 提示，改成权限引导（辅助功能/输入监控）。
    涉及：core_client.py

  2. 阶段 2：功能补齐（预计 5-10 天）

  - 做统一平台抽象层（Windows/macOS 各自 backend），避免后续到处写 if Darwin。
    新增建议：util/platform/input_backend.py, util/platform/windows_backend.py, util/platform/macos_backend.py
  - 加回“模拟打字”模式（非 paste）并做应用兼容白名单策略。
  - 实现 macOS 托盘（可用 rumps 或 rumps+pyobjc）并替换当前 Windows 托盘路径。
  - 完善窗口检测在 macOS 的稳定性（现在 AppleScript 分支较弱）。
  - 打包分发（start_client.command / start_server.command，后续再考虑 .app）。

  验收标准

  1. 在 macOS 上按快捷键可开始/结束录音，能稳定上屏英文文本。
  2. 不要求 sudo 启动；首次运行给出权限缺失提示并可定位设置项。
  3. 关闭托盘后程序仍可正常运行与退出。
  4. 现有 Windows 行为不回归。

  主要风险

  1. macOS 权限（Accessibility/Input Monitoring）未授权时会“启动成功但无输入”。
  2. 全局按键“阻塞/抑制”在 macOS 不一定等价于 Windows。
  3. 远控软件窗口（RustDesk/VM）仍可能需要强制 paste。

  我建议现在就执行的最小落地版本

  1. 先做阶段 1。
  2. 先不做托盘、不做按键阻塞、不做逐字输入。
  3. 以“全局快捷键 + paste 上屏”作为第一版交付。

  如果你同意，我下一步可以直接按阶段 1 开工并逐文件提交改动。