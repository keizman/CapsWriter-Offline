# coding: utf-8
"""
服务端前缀翻译指令处理。

仅在文本开头命中以下模式时触发：
1) 请翻译为...
2) 请翻译...
3) Please translate to ...
4) Please translate ...
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib import error, request

from config_server import ServerConfig as Config
from . import logger


_CN_PREFIXES = ("请翻译为", "请翻译")
_EN_PREFIXES = ("please translate to", "please translate")
_LEADING_SEPARATORS = " \t\r\n:：,，。.;；!?！？、…"
_BRACKET_OPEN = "([{（【《<"
_BRACKET_CLOSE = ")]}）】》>"
_LEADING_PUNCT_CHARS = set(
    string.punctuation + "，。！？；：、…·“”‘’「」『』（）【】《》〈〉"
)


_LANG_ALIASES_CN = {
    "英语": "en",
    "英文": "en",
    "中文": "zh",
    "汉语": "zh",
    "简体中文": "zh-CN",
    "繁体中文": "zh-TW",
    "日语": "ja",
    "日文": "ja",
    "西班牙语": "es",
    "西语": "es",
    "法语": "fr",
    "法文": "fr",
    "德语": "de",
    "德文": "de",
    "俄语": "ru",
    "俄文": "ru",
    "韩语": "ko",
    "朝鲜语": "ko",
    "葡萄牙语": "pt",
    "葡语": "pt",
    "意大利语": "it",
    "意语": "it",
    "阿拉伯语": "ar",
    "印地语": "hi",
    "泰语": "th",
    "越南语": "vi",
    "土耳其语": "tr",
    "印尼语": "id",
    "印度尼西亚语": "id",
    "马来语": "ms",
}

_LANG_ALIASES_EN = {
    "english": "en",
    "chinese": "zh",
    "simplified chinese": "zh-CN",
    "traditional chinese": "zh-TW",
    "japanese": "ja",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "russian": "ru",
    "korean": "ko",
    "portuguese": "pt",
    "italian": "it",
    "arabic": "ar",
    "hindi": "hi",
    "thai": "th",
    "vietnamese": "vi",
    "turkish": "tr",
    "indonesian": "id",
    "malay": "ms",
}

_CN_ALIASES_SORTED = sorted(_LANG_ALIASES_CN.items(), key=lambda item: len(item[0]), reverse=True)
_EN_ALIASES_SORTED = sorted(_LANG_ALIASES_EN.items(), key=lambda item: len(item[0]), reverse=True)


@dataclass
class TranslateCommand:
    target_lang: str
    content: str
    trigger: str


def _trim_leading_separators(text: str) -> str:
    return text.lstrip(_LEADING_SEPARATORS)


def _strip_leading_punctuation(text: str) -> str:
    """
    清理翻译结果前导标点，避免输出以标点开头。
    """
    value = str(text or "")
    idx = 0
    length = len(value)
    while idx < length:
        ch = value[idx]
        if ch.isspace() or ch in _LEADING_PUNCT_CHARS:
            idx += 1
            continue
        break
    return value[idx:].lstrip()


def _strip_optional_brackets_prefix(text: str) -> str:
    value = text.lstrip()
    if not value:
        return value
    if value[0] not in _BRACKET_OPEN:
        return value
    return value[1:].lstrip()


def _strip_optional_brackets_after_lang(text: str) -> str:
    value = text.lstrip()
    if value and value[0] in _BRACKET_CLOSE:
        value = value[1:]
    return _trim_leading_separators(value)


def _match_cn_alias(rest: str) -> Optional[Tuple[str, str]]:
    for alias, lang_code in _CN_ALIASES_SORTED:
        if rest.startswith(alias):
            tail = _strip_optional_brackets_after_lang(rest[len(alias):])
            return lang_code, tail
    return None


def _match_en_alias(rest: str) -> Optional[Tuple[str, str]]:
    lower = rest.lower()
    for alias, lang_code in _EN_ALIASES_SORTED:
        if not lower.startswith(alias):
            continue
        tail = rest[len(alias):]
        # 英文匹配要求别名后是边界符，避免把 "englishman" 当语言。
        if tail and (tail[0].isalnum() or tail[0] == "_"):
            continue
        tail = _strip_optional_brackets_after_lang(tail)
        return lang_code, tail
    return None


def _match_iso_code(rest: str) -> Optional[Tuple[str, str]]:
    # 支持任意语言代码，如 en / zh-CN / pt-BR
    m = re.match(r"^([A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?)(.*)$", rest)
    if not m:
        return None

    code = m.group(1).replace("_", "-")
    tail = m.group(2)
    # 代码后必须有分隔；否则如 "english..." 不按代码处理。
    if tail and not tail[0].isspace() and tail[0] not in ":：,，。;；!?！？、)]}）】》>":
        return None

    return code, _strip_optional_brackets_after_lang(tail)


def _parse_target_and_content(rest: str) -> Tuple[str, str]:
    """
    解析目标语言与待翻译内容。

    未识别目标语言时，默认英文。
    """
    text = _trim_leading_separators(rest)
    if not text:
        return "en", ""

    candidates = [text, _strip_optional_brackets_prefix(text)]
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        for matcher in (_match_cn_alias, _match_en_alias, _match_iso_code):
            matched = matcher(candidate)
            if matched:
                lang_code, content = matched
                return lang_code, content.strip()

    return "en", text.strip()


def parse_translate_command(text: str) -> Optional[TranslateCommand]:
    """
    仅在句首命中翻译指令时返回解析结果。
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    for prefix in _CN_PREFIXES:
        if raw.startswith(prefix):
            target_lang, content = _parse_target_and_content(raw[len(prefix):])
            return TranslateCommand(target_lang=target_lang, content=content, trigger=prefix)

    lower = raw.lower()
    for prefix in _EN_PREFIXES:
        if lower.startswith(prefix):
            target_lang, content = _parse_target_and_content(raw[len(prefix):])
            return TranslateCommand(target_lang=target_lang, content=content, trigger=prefix)

    return None


def _http_json(
    method: str,
    url: str,
    payload: dict,
    timeout_sec: float,
    headers: Optional[dict] = None,
) -> Optional[dict]:
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = request.Request(url=url, data=body, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"翻译请求失败: {url} ({e})")
    except Exception as e:
        logger.warning(f"翻译请求异常: {url} ({e})")
    return None


def _translate_via_mtran(text: str, target_lang: str) -> Optional[str]:
    base = str(getattr(Config, "translate_server_url", "")).strip().rstrip("/")
    if not base:
        return None

    timeout_ms = max(1, int(getattr(Config, "translate_timeout_ms", 5000)))
    timeout_sec = timeout_ms / 1000.0
    source_lang = str(getattr(Config, "translate_source_lang", "auto")).strip() or "auto"
    token = str(getattr(Config, "translate_api_token", "")).strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # 优先 Google v2 兼容接口
    payload_v2 = {
        "q": text,
        "target": target_lang,
        "source": source_lang,
        "format": "text",
    }
    data = _http_json("POST", f"{base}/google/language/translate/v2", payload_v2, timeout_sec, headers)
    if isinstance(data, dict):
        translated = (
            data.get("data", {})
            .get("translations", [{}])[0]
            .get("translatedText")
        )
        if translated:
            cleaned = _strip_leading_punctuation(str(translated).strip())
            return cleaned or str(translated).strip()

    # 兼容 MTran 原生接口（兜底）
    payload_native = {"from": source_lang, "to": target_lang, "text": text, "html": False}
    data = _http_json("POST", f"{base}/translate", payload_native, timeout_sec, headers)
    if isinstance(data, dict):
        translated = data.get("result") or data.get("translation") or data.get("translatedText")
        if translated:
            cleaned = _strip_leading_punctuation(str(translated).strip())
            return cleaned or str(translated).strip()

    return None


def maybe_translate_prefixed_text(text: str) -> Optional[str]:
    """
    命中前缀翻译指令时返回翻译结果；否则返回 None。
    """
    if not bool(getattr(Config, "translate_command_enable", True)):
        return None

    command = parse_translate_command(text)
    if command is None:
        return None
    if not command.content:
        return None

    translated = _translate_via_mtran(command.content, command.target_lang)
    if translated:
        logger.info(
            "前缀翻译命中: trigger=%s target=%s len=%s",
            command.trigger,
            command.target_lang,
            len(command.content),
        )
        return translated

    logger.warning("前缀翻译失败，保留原文输出")
    return None
