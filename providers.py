from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

VERSION = "프로토-1.1234571"


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

    def can_call(self, engine: EngineConfig) -> Tuple[bool, str]:
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
        used = self.calls.get(engine.group, 0)
        limit = self.budgets.get(engine.group, 0)
        if limit > 0 and used >= limit:
            raise RuntimeError(f"{engine.group} 호출 예산 소진({used}/{limit})")
        self.calls[engine.group] = used + 1

    def block(self, engine: EngineConfig, reason: str) -> None:
        self.disabled_groups[engine.group] = reason

    def record(self, result: CallResult) -> None:
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


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        # URL에 Gemini key가 포함될 수 있으므로 원문 exception/URL을 그대로 출력하지 않는다.
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

    payload = _post(f"{engine.base_url}/chat/completions", headers, body)
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderFailure("빈 choices 응답", category="empty")
    choice = choices[0]
    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()
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
    payload = _post(url, {"Content-Type": "application/json"}, body)
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
                time.sleep(1.0)
            continue
        except Exception as exc:
            last_error = exc
            history.append(f"{engine.label}: {type(exc).__name__}")
            continue

    raise RuntimeError("모든 엔진 호출 실패: " + " | ".join(history or [type(last_error).__name__ if last_error else "원인 없음"]))


def validate_research(text: str) -> Dict[str, Any]:
    urls = re.findall(r"https?://[^\s)\]>]+", text or "")
    refusal_markers = (
        "실시간 검색을 할 수 없",
        "웹 검색을 할 수 없",
        "인터넷에 접속할 수 없",
        "cannot browse",
        "can't browse",
        "no browsing access",
    )
    reasons: List[str] = []
    if len(set(urls)) < 2:
        reasons.append("실제 URL 2개 미만")
    lower = (text or "").lower()
    if any(marker.lower() in lower for marker in refusal_markers):
        reasons.append("웹검색 거부 문구 감지")
    return {"usable": not reasons, "urls": list(dict.fromkeys(urls)), "reasons": reasons}


def research_with_groq(api_key: str, query: str, model: str = "groq/compound-mini") -> Tuple[str, Dict[str, Any]]:
    if not api_key:
        raise RuntimeError("Groq API 키가 없어 웹조사를 실행할 수 없음")
    engine = EngineConfig(
        engine_id="groq_research",
        label="Groq Compound Mini",
        group="Groq",
        kind="openai",
        model=model,
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        input_char_limit=16000,
        temperature=0.2,
    )
    system = (
        "공용 Evidence Researcher다. 반드시 실제 웹검색 결과를 사용하고 핵심 주장마다 출처 URL을 붙여라. "
        "최소 2개의 실제 URL을 포함하라. 검색하지 못했다면 추측으로 메우지 말고 실패를 명시하라."
    )
    result = _call_engine(engine, system, compact_text(query, 14000), 1800)
    return result.text, validate_research(result.text)


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
        ),
    }
