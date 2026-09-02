from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

VERSION = "프로토-1.1234571-fix1"


@dataclass(frozen=True)
class EngineConfig:
    engine_id: str
    label: str
    group: str
    kind: str
    model: str
    api_key: str
    base_url: str = ""
    input_char_limit: int = 24000
    temperature: float = 0.35
    timeout: int = 75


@dataclass
class CallResult:
    text: str
    requested_engine: str
    actual_engine: str
    provider_group: str
    model: str
    finish_reason: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    attempts: int = 1
    continuations: int = 0
    warnings: List[str] = field(default_factory=list)
    fallback_history: List[str] = field(default_factory=list)
    prompt_chars: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderState:
    budgets: Dict[str, int]
    calls: Dict[str, int] = field(default_factory=dict)
    disabled_groups: Dict[str, str] = field(default_factory=dict)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock, repr=False)

    def can_call(self, engine: EngineConfig) -> Tuple[bool, str]:
        with self.lock:
            if engine.group in self.disabled_groups:
                return False, f"{engine.group} 차단됨: {self.disabled_groups[engine.group]}"
            if not engine.api_key:
                return False, f"{engine.label} API 키 없음"
            used = self.calls.get(engine.group, 0)
            limit = self.budgets.get(engine.group, 0)
            if limit > 0 and used >= limit:
                return False, f"{engine.group} 호출 예산 소진({used}/{limit})"
            return True, ""

    def consume(self, engine: EngineConfig) -> None:
        with self.lock:
            used = self.calls.get(engine.group, 0)
            limit = self.budgets.get(engine.group, 0)
            if limit > 0 and used >= limit:
                raise RuntimeError(f"{engine.group} 호출 예산 소진({used}/{limit})")
            self.calls[engine.group] = used + 1

    def block(self, engine: EngineConfig, reason: str) -> None:
        with self.lock:
            self.disabled_groups[engine.group] = reason

    def record(self, result: CallResult) -> None:
        with self.lock:
            self.diagnostics.append(result.to_dict())


class ProviderFailure(RuntimeError):
    def __init__(self, message: str, *, category: str = "other", status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


def compact_text(text: str, max_chars: int) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    marker = "\n\n[...입력 한도 때문에 중간 부분 생략...]\n\n"
    remain = max(1000, max_chars - len(marker))
    head = remain // 2
    return text[:head] + marker + text[-(remain - head):]


def _error_category(status: int, body: str) -> str:
    lower = body.lower()
    if status in (401, 403) or any(x in lower for x in ("invalid api key", "unauthorized", "authentication")):
        return "auth"
    if status == 413 or any(x in lower for x in ("context length", "request too large", "input too long")):
        return "too_large"
    if status == 429 or any(x in lower for x in ("quota", "rate limit", "too many requests")):
        return "quota"
    if status >= 500:
        return "retryable"
    return "other"


def _usage(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    usage = payload.get("usage") or payload.get("usageMetadata") or {}
    inp = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("promptTokenCount")
    out = usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("candidatesTokenCount")
    total = usage.get("total_tokens") or usage.get("totalTokenCount")
    return inp, out, total


def _post(
    url: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    *,
    timeout: int = 75,
) -> Dict[str, Any]:
    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.Timeout as exc:
        raise ProviderFailure(f"응답 시간 초과({timeout}초)", category="retryable") from exc
    except requests.RequestException as exc:
        # Gemini는 URL에 key가 들어가므로 예외 원문의 URL을 그대로 노출하지 않는다.
        raise ProviderFailure(f"네트워크 요청 실패: {type(exc).__name__}", category="retryable") from exc

    if not response.ok:
        text = response.text[:1800]
        raise ProviderFailure(
            f"HTTP {response.status_code}: {text}",
            category=_error_category(response.status_code, text),
            status_code=response.status_code,
        )
    try:
        return response.json()
    except Exception as exc:
        raise ProviderFailure("JSON 응답 해석 실패", category="other") from exc


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value:
                    parts.append(str(value))
            elif item:
                parts.append(str(item))
        if parts:
            return "\n".join(parts).strip()
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return ""


def _call_openai_compatible(engine: EngineConfig, system: str, prompt: str, max_tokens: int) -> CallResult:
    headers = {
        "Authorization": f"Bearer {engine.api_key.strip()}",
        "Content-Type": "application/json",
    }
    body: Dict[str, Any] = {
        "model": engine.model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": engine.temperature,
        "stream": False,
    }
    if engine.group == "Groq" and "gpt-oss" in engine.model.lower():
        body["reasoning_effort"] = "low"

    payload = _post(
        f"{engine.base_url}/chat/completions",
        headers,
        body,
        timeout=engine.timeout,
    )
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderFailure("빈 choices 응답", category="empty")
    choice = choices[0]
    message = choice.get("message") or {}
    text = _message_text(message)
    if not text:
        raise ProviderFailure("모델이 빈 응답을 반환함", category="empty")
    inp, out, total = _usage(payload)
    return CallResult(
        text=text,
        requested_engine=engine.engine_id,
        actual_engine=engine.engine_id,
        provider_group=engine.group,
        model=engine.model,
        finish_reason=str(choice.get("finish_reason") or ""),
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )


def _call_gemini(engine: EngineConfig, system: str, prompt: str, max_tokens: int) -> CallResult:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{engine.model.strip()}:generateContent?key={engine.api_key.strip()}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": engine.temperature},
    }
    payload = _post(url, {"Content-Type": "application/json"}, body, timeout=engine.timeout)
    candidates = payload.get("candidates") or []
    if not candidates:
        raise ProviderFailure("Gemini 후보 응답 없음", category="empty")
    candidate = candidates[0]
    parts = ((candidate.get("content") or {}).get("parts") or [])
    text = "\n".join(str(p.get("text") or "") for p in parts).strip()
    if not text:
        raise ProviderFailure("Gemini 빈 응답", category="empty")
    inp, out, total = _usage(payload)
    return CallResult(
        text=text,
        requested_engine=engine.engine_id,
        actual_engine=engine.engine_id,
        provider_group=engine.group,
        model=engine.model,
        finish_reason=str(candidate.get("finishReason") or ""),
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )


def _call_engine(engine: EngineConfig, system: str, prompt: str, max_tokens: int) -> CallResult:
    prompt = compact_text(prompt, engine.input_char_limit)
    if engine.kind == "gemini":
        result = _call_gemini(engine, system, prompt, max_tokens)
    else:
        result = _call_openai_compatible(engine, system, prompt, max_tokens)
    result.prompt_chars = len(prompt)
    return result


def _looks_truncated(finish_reason: str) -> bool:
    value = (finish_reason or "").lower()
    return any(x in value for x in ("length", "max_token", "token_limit"))


def call_with_fallback(
    requested_engine: str,
    fallback_ids: List[str],
    engines: Dict[str, EngineConfig],
    system: str,
    prompt: str,
    max_tokens: int,
    continuations: int,
    state: ProviderState,
) -> CallResult:
    order = [requested_engine] + [x for x in fallback_ids if x != requested_engine]
    history: List[str] = []
    last_error: Optional[Exception] = None

    for engine_id in order:
        engine = engines.get(engine_id)
        if not engine:
            continue
        ok, reason = state.can_call(engine)
        if not ok:
            history.append(f"{engine.label}: {reason}")
            continue

        try:
            state.consume(engine)
            result = _call_engine(engine, system, prompt, max_tokens)
            result.requested_engine = requested_engine
            result.fallback_history = list(history)

            full_text = result.text
            for _ in range(max(0, continuations)):
                if not _looks_truncated(result.finish_reason):
                    break
                follow_prompt = (
                    prompt
                    + "\n\n[이전 출력]\n"
                    + compact_text(full_text, 7000)
                    + "\n\n중복 없이 바로 이어서 작성하라."
                )
                state.consume(engine)
                more = _call_engine(engine, system, follow_prompt, max_tokens)
                full_text += "\n" + more.text
                result.finish_reason = more.finish_reason
                result.continuations += 1
                result.output_tokens = (result.output_tokens or 0) + (more.output_tokens or 0)
                result.total_tokens = (result.total_tokens or 0) + (more.total_tokens or 0)

            result.text = full_text.strip()
            state.record(result)
            return result

        except ProviderFailure as exc:
            last_error = exc
            history.append(f"{engine.label}: {exc}")
            if exc.category in ("auth", "quota"):
                state.block(engine, str(exc))
            if exc.category == "retryable":
                time.sleep(0.15)
            continue
        except Exception as exc:
            last_error = exc
            history.append(f"{engine.label}: {type(exc).__name__}")
            continue

    raise RuntimeError(
        "모든 엔진 호출 실패: "
        + " | ".join(history or [type(last_error).__name__ if last_error else "원인 없음"])
    )


def _collect_urls(value: Any) -> List[str]:
    found: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key.lower() in {"url", "link", "source_url"} and isinstance(item, str):
                    if item.startswith("http://") or item.startswith("https://"):
                        found.append(item)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.extend(re.findall(r"https?://[^\s)\]>\"']+", node))

    walk(value)
    return list(dict.fromkeys(found))


def validate_research(text: str, extra_urls: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    urls = re.findall(r"https?://[^\s)\]>]+", text or "")
    if extra_urls:
        urls.extend(extra_urls)
    urls = list(dict.fromkeys(urls))
    refusal_markers = (
        "실시간 검색을 할 수 없",
        "웹 검색을 할 수 없",
        "인터넷에 접속할 수 없",
        "cannot browse",
        "can't browse",
        "no browsing access",
    )
    reasons: List[str] = []
    lower = (text or "").lower()
    if not urls:
        reasons.append("검색 출처 URL을 확인하지 못함")
    if any(marker.lower() in lower for marker in refusal_markers):
        reasons.append("웹검색 거부 문구 감지")
    return {"usable": bool(text.strip()) and bool(urls) and not reasons, "urls": urls, "reasons": reasons}


def research_with_groq(
    api_key: str,
    query: str,
    model: str = "groq/compound-mini",
) -> Tuple[str, Dict[str, Any]]:
    if not api_key:
        raise RuntimeError("Groq API 키가 없어 웹조사를 실행할 수 없음")

    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "공용 Evidence Researcher다. 실제 웹검색을 사용해 핵심 사실을 짧게 조사하라. "
                    "가능하면 서로 다른 출처를 사용하고, 추측을 사실처럼 쓰지 마라."
                ),
            },
            {"role": "user", "content": compact_text(query, 12000)},
        ],
        "max_tokens": 1200,
        "temperature": 0.15,
        "stream": False,
        "citation_options": "enabled",
    }
    payload = _post(
        "https://api.groq.com/openai/v1/chat/completions",
        {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        body,
        timeout=60,
    )
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderFailure("Groq 웹조사 응답에 choices가 없음", category="empty")
    message = (choices[0] or {}).get("message") or {}
    text = _message_text(message)
    if not text:
        raise ProviderFailure("Groq 웹조사가 빈 응답을 반환함", category="empty")

    urls = _collect_urls(message.get("executed_tools") or [])
    urls.extend(_collect_urls(payload.get("citations") or []))
    urls = list(dict.fromkeys(urls))
    if urls:
        missing = [url for url in urls if url not in text]
        if missing:
            text = text.rstrip() + "\n\n[검색 출처]\n" + "\n".join(f"- {url}" for url in missing[:10])

    return text, validate_research(text, urls)


def build_engines(keys: Dict[str, str], models: Dict[str, str]) -> Dict[str, EngineConfig]:
    return {
        "gemini": EngineConfig(
            engine_id="gemini",
            label="Gemini",
            group="Gemini",
            kind="gemini",
            model=models["gemini"],
            api_key=keys.get("Gemini", ""),
            input_char_limit=24000,
            timeout=60,
        ),
        "groq": EngineConfig(
            engine_id="groq",
            label="Groq GPT-OSS",
            group="Groq",
            kind="openai",
            model=models["groq"],
            api_key=keys.get("Groq", ""),
            base_url="https://api.groq.com/openai/v1",
            input_char_limit=17000,
            timeout=60,
        ),
        "cerebras": EngineConfig(
            engine_id="cerebras",
            label="Cerebras GPT-OSS",
            group="Cerebras",
            kind="openai",
            model=models["cerebras"],
            api_key=keys.get("Cerebras", ""),
            base_url="https://api.cerebras.ai/v1",
            input_char_limit=24000,
            timeout=60,
        ),
        "nvidia_super": EngineConfig(
            engine_id="nvidia_super",
            label="NVIDIA Nemotron Super",
            group="NVIDIA",
            kind="openai",
            model=models["nvidia_super"],
            api_key=keys.get("NVIDIA", ""),
            base_url="https://integrate.api.nvidia.com/v1",
            input_char_limit=28000,
            timeout=75,
        ),
        "nvidia_ultra": EngineConfig(
            engine_id="nvidia_ultra",
            label="NVIDIA Nemotron Ultra",
            group="NVIDIA",
            kind="openai",
            model=models["nvidia_ultra"],
            api_key=keys.get("NVIDIA", ""),
            base_url="https://integrate.api.nvidia.com/v1",
            input_char_limit=28000,
            timeout=90,
        ),
    }
