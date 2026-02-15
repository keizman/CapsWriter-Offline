import onnxruntime
import time
import os
import math
import numpy as np
from . import logger

"""
ONNX 推理底层工具 - 可配置 Padding 策略

padding_mode:
- auto: DML -> fixed30；CoreML/CPU -> fixed5
- fixed30: 始终至少补齐到 30 秒
- fixed5: 始终至少补齐到 5 秒
- dynamic: 5 秒起步，30 秒内按 5 秒分桶补齐；超过 30 秒按实际长度
"""


def _normalize_padding_mode(padding_mode: str) -> str:
    mode = str(padding_mode or "auto").strip().lower()
    aliases = {
        "legacy": "fixed30",
        "30": "fixed30",
        "fixed_30": "fixed30",
        "fixed-30": "fixed30",
        "5": "fixed5",
        "fixed_5": "fixed5",
        "fixed-5": "fixed5",
    }
    return aliases.get(mode, mode)


def _resolve_padding_mode(provider: str, padding_mode: str) -> str:
    mode = _normalize_padding_mode(padding_mode)
    if mode != "auto":
        return mode
    if provider == "DmlExecutionProvider":
        return "fixed30"
    if provider == "CoreMLExecutionProvider":
        return "fixed5"
    return "fixed5"


def _resolve_padding_secs(actual_samples: int, provider: str, padding_mode: str, padding_secs: int) -> int:
    mode = _resolve_padding_mode(provider, padding_mode)
    base_secs = max(1, int(padding_secs))

    if mode == "fixed30":
        return max(30, base_secs)
    if mode == "fixed5":
        return 5
    if mode == "dynamic":
        actual_secs = max(actual_samples / 16000.0, 0.0)
        if actual_secs <= 5:
            return 5
        if actual_secs <= 30:
            return int(math.ceil(actual_secs / 5.0) * 5)
        return int(math.ceil(actual_secs))

    logger.warning(f"未知 padding_mode={padding_mode}，回退 fixed5")
    return 5


def load_onnx_models(
    encoder_path,
    ctc_path,
    padding_secs=30,
    dml_enable=True,
    coreml_enable=False,
    padding_mode="auto",
):
    """步骤 1: 加载 ONNX 音频编码器和 CTC Head 并进行热身"""
    # print("\n[1] 加载 ONNX Models (Encoder + CTC)...")
    
    t_start = time.perf_counter()
    session_opts = onnxruntime.SessionOptions()
    session_opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    session_opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    session_opts.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    
    providers = ['CPUExecutionProvider']
    if dml_enable and 'DmlExecutionProvider' in onnxruntime.get_available_providers():
        providers.insert(0, 'DmlExecutionProvider') 
    if coreml_enable and 'CoreMLExecutionProvider' in onnxruntime.get_available_providers():
        providers.insert(0, 'CoreMLExecutionProvider')
    logger.info(f"Onnxruntime providers: {providers}")
    
    encoder_sess = onnxruntime.InferenceSession(
        encoder_path, 
        sess_options=session_opts, 
        providers=providers
    )
    
    ctc_sess = onnxruntime.InferenceSession(
        ctc_path, 
        sess_options=session_opts, 
        providers=providers
    )

    encoder_provider = encoder_sess.get_providers()[0] if encoder_sess.get_providers() else "CPUExecutionProvider"
    warmup_padding_secs = _resolve_padding_secs(
        actual_samples=0,
        provider=encoder_provider,
        padding_mode=padding_mode,
        padding_secs=padding_secs,
    )
    resolved_mode = _resolve_padding_mode(encoder_provider, padding_mode)
    logger.info(
        f"ONNX padding 策略: mode={padding_mode} -> {resolved_mode}, "
        f"provider={encoder_provider}, warmup={warmup_padding_secs}s"
    )
    
    # Warmup
    if warmup_padding_secs > 0:
        # print(f"   [Warmup] Warming up with {warmup_secs}s pseudo-audio...")
        SR = 16000
        warmup_samples = int(SR * warmup_padding_secs)  # Ensure int
        
        # Encoder Warmup
        audio_type = encoder_sess.get_inputs()[0].type
        dtype = np.float16 if 'float16' in audio_type else np.float32
        dummy_audio = np.zeros((1, 1, warmup_samples), dtype=dtype)
        dummy_ilens = np.array([warmup_samples], dtype=np.int64)
        
        # New model has ['audio', 'ilens']
        in_names = [x.name for x in encoder_sess.get_inputs()]
        if 'ilens' in in_names:
            encoder_sess.run(None, {in_names[0]: dummy_audio, in_names[1]: dummy_ilens})
        else:
            encoder_sess.run(None, {in_names[0]: dummy_audio})
            
        # CTC Warmup
        ctc_in = ctc_sess.get_inputs()[0]
        ctc_dtype = np.float16 if 'float16' in ctc_in.type else np.float32
        # CTC input shape is [1, T, 512]
        # T_lfr = T_mel // 6, T_mel = audio // 160
        T_warmup = int(warmup_samples // 160 // 6) # Ensure int
        dummy_enc = np.zeros((1, T_warmup, 512), dtype=ctc_dtype)
        ctc_sess.run(None, {ctc_in.name: dummy_enc})

    t_cost = time.perf_counter() - t_start
    return encoder_sess, ctc_sess, t_cost


def encode_audio(audio, encoder_sess, padding_secs=30, padding_mode="auto"):
    """使用 ONNX Encoder 获取 LLM 嵌入和 CTC 特征，支持自动 Padding"""
    
    # Check expected input type
    in_names = [x.name for x in encoder_sess.get_inputs()]
    audio_type = encoder_sess.get_inputs()[0].type
    dtype = np.float16 if 'float16' in audio_type else np.float32

    actual_samples = len(audio)

    encoder_provider = encoder_sess.get_providers()[0] if encoder_sess.get_providers() else "CPUExecutionProvider"
    target_padding_secs = _resolve_padding_secs(
        actual_samples=actual_samples,
        provider=encoder_provider,
        padding_mode=padding_mode,
        padding_secs=padding_secs,
    )
    target_samples = int(target_padding_secs * 16000)
    
    if actual_samples < target_samples:
        padded_audio = np.zeros(target_samples, dtype=audio.dtype)
        padded_audio[:actual_samples] = audio
        audio = padded_audio
    
    audio_input = audio.astype(dtype).reshape(1, 1, -1)
    ilens_input = np.array([actual_samples], dtype=np.int64)
    
    out_names = [x.name for x in encoder_sess.get_outputs()]
    
    # 构造输入 Feed
    if 'ilens' in in_names:
        input_feed = {
            in_names[0]: onnxruntime.OrtValue.ortvalue_from_numpy(audio_input, 'cpu', 0),
            'ilens': onnxruntime.OrtValue.ortvalue_from_numpy(ilens_input, 'cpu', 0)
        }
    else:
        input_feed = {
            in_names[0]: onnxruntime.OrtValue.ortvalue_from_numpy(audio_input, 'cpu', 0)
        }
    
    outputs = encoder_sess.run_with_ort_values(out_names, input_feed)
    
    # Output 0: enc_output [1, T_enc, 512] (For CTC) - 不截断
    enc_output = outputs[0].numpy()
    
    # Output 1: adaptor_output [1, T_llm, 1024] (For LLM) - 需要截断到有效长度
    # 计算有效长度 (llm_target_len)
    T_mel_valid = (actual_samples // 160) + 1
    T_lfr_valid = (T_mel_valid + 5) // 6 # (mel + lfr_n - 1) // lfr_n
    olens_1 = 1 + (T_lfr_valid - 3 + 2) // 2
    target_len = (1 + (olens_1 - 3 + 2) // 2 - 1) // 2 + 1
    
    audio_embd_raw = outputs[1].numpy().squeeze(0)
    # 截断到有效值
    audio_embd = audio_embd_raw[:target_len, :].astype(np.float32)
    
    return audio_embd, enc_output
