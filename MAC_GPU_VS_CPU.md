# Mac GPU vs CPU Benchmark (Fun-ASR-Nano)

Date: February 15, 2026  
Platform: macOS (CoreML + Metal path enabled)  
Model: `fun_asr_nano`  
Test Tool: `util/tools/bench_server_device.py`  
Audio Type: Real microphone recordings (`2026/02/assets/*.wav`)

## Test Goal

Compare latency and CPU usage under different ONNX padding strategies:

- GPU + `fixed30` (legacy behavior)
- GPU + `fixed5`
- GPU + `dynamic`
- CPU + `fixed5` (baseline)

## Result Summary

### Sample A

Audio: `2026/02/assets/(20260214-233711)hello hello hello he.wav`  
Length: 4.75s

| Mode | Padding | Provider | Latency Avg (s) | CPU Avg (%) |
|---|---|---|---:|---:|
| GPU | fixed30 | CoreML+CPU | 1.149 | 376.30 |
| GPU | fixed5 | CoreML+CPU | 0.318 | 216.39 |
| GPU | dynamic | CoreML+CPU | 0.314 | 219.87 |
| CPU | fixed5 | CPU | 0.319 | 213.34 |

### Sample B

Audio: `2026/02/assets/(20260214-233721)你好吗？我很好。你好吗？我很好。你好吗？.wav`  
Length: 4.35s

| Mode | Padding | Provider | Latency Avg (s) | CPU Avg (%) |
|---|---|---|---:|---:|
| GPU | fixed30 | CoreML+CPU | 1.266 | 347.52 |
| GPU | fixed5 | CoreML+CPU | 0.404 | 179.25 |
| GPU | dynamic | CoreML+CPU | 0.394 | 183.12 |
| CPU | fixed5 | CPU | 0.403 | 182.22 |

## Conclusions

1. `fixed30` is the main bottleneck for short speech on Mac.
2. Switching to `fixed5` or `dynamic` removes most of the gap.
3. After optimization, GPU path is no longer clearly worse than CPU for short speech.

## Current Recommended Config (Mac)

Use:

- `device_mode = gpu`
- `onnx_padding_mode = fixed5`

Reference file:

- `config_server.local.json`

## Reproduce Commands

```bash
export PYTHONPATH="$PWD"
export DYLD_LIBRARY_PATH="$PWD/util/fun_asr_gguf/bin${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"

python util/tools/bench_server_device.py \
  --mode gpu \
  --audio-file "2026/02/assets/(20260214-233711)hello hello hello he.wav" \
  --iterations 5 \
  --padding-mode fixed5

python util/tools/bench_server_device.py \
  --mode cpu \
  --audio-file "2026/02/assets/(20260214-233711)hello hello hello he.wav" \
  --iterations 5 \
  --padding-mode fixed5
```
