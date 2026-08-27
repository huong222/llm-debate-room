from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import requests
from google import genai
from google.genai import types
from groq import Groq
from mistralai.client import Mistral

PROVIDERS = ("Gemini", "Groq", "Mistral", "Cloudflare")


@dataclass
class CallResult:
    text: str
    provider: str
    model: str
    finish_reason: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    continuation_count: int = 0
    request_count: int = 1
    truncated: bool = False
    attempts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderState:
    disabled: Dict[str, str] = field(default_factory=dict)
    calls: Dict[str, int] = field(default_factory=dict)
    budgets: Dict[str, int] = field(default_factory=dict)

    def available(self, provider: str) -> bool:
        return provider not in self.disabled

    def block(self, provider: str, reason: str) -> None:
        self.disabled[provider] = reason

    def consume(self, provider: str) -> None:
        used = self.calls.get(provider, 0)
        limit = self.budgets.get(provider, 0)
        if limit > 0 and used >= limit:
            raise RuntimeError(f"이번 토론의 {provider} 호출 예산 {limit}회를 모두 사용함")
        self.calls[provider] = used + 1


class EmptyModelResponse(RuntimeError):
    pass


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _finish_value(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    return str(name or value)


def _usage_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    result: Dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
    ):
        item = getattr(value, key, None)
        if item is not None:
            result[key] = item
    return result


def _looks_like_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "429",
        "quota",
        "resource_exhausted",
        "too many requests",
        "rate limit",
        "credit_balance_exhausted",
        "neurons per day",
    )
    return any(marker in text for marker in markers)


def _looks_like_auth(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication")
    return any(marker in text for marker in markers)


def _looks_too_large(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "413",
        "request too large",
        "context length",
        "context_length",
        "tokens per minute",
        "maximum context",
        "input too long",
    )
    return any(marker in text for marker in markers)


def _looks_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("500", "502", "503", "504", "timeout", "temporarily unavailable", "capacity"))


def _is_truncated(finish_reason: str) -> bool:
    value = (finish_reason or "").lower()
    return any(marker in value for marker in ("length", "max_token", "token_limit"))


def compact_prompt(text: str, max_chars: int = 18000) -> str:
    text = text or ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...중간 내용은 입력 한도 때문에 생략됨...]\n\n" + text[-half:]


def has_credentials(provider: str, credentials: Dict[str, str]) -> bool:
    if provider == "Cloudflare":
        return bool(credentials.get("Cloudflare Token") and credentials.get("Cloudflare Account ID"))
    return bool(credentials.get(provider))


def call_gemini(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> CallResult:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model.strip(),
        contents=f"SYSTEM:\n{system}\n\nTASK:\n{prompt}",
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.35,
        ),
    )
    text = _plain(getattr(response, "text", "")).strip()
    candidates = getattr(response, "candidates", None) or []
    finish = ""
    if candidates:
        finish = _finish_value(getattr(candidates[0], "finish_reason", ""))
    return CallResult(
        text=text,
        provider="Gemini",
        model=model,
        finish_reason=finish,
        usage=_usage_dict(getattr(response, "usage_metadata", None)),
    )


def call_groq(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> CallResult:
    client = Groq(api_key=api_key)
    kwargs: Dict[str, Any] = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_tokens,
        "temperature": 0.3,
    }
    if "gpt-oss" in model.lower():
        kwargs["reasoning_effort"] = "low"

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        if "reasoning_effort" in str(exc).lower():
            kwargs.pop("reasoning_effort", None)
            response = client.chat.completions.create(**kwargs)
        else:
            raise

    choice = response.choices[0]
    text = _plain(getattr(choice.message, "content", "")).strip()
    return CallResult(
        text=text,
        provider="Groq",
        model=model,
        finish_reason=_finish_value(getattr(choice, "finish_reason", "")),
        usage=_usage_dict(getattr(response, "usage", None)),
    )


def call_mistral(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> CallResult:
    client = Mistral(api_key=api_key)
    response = client.chat.complete(
        model=model.strip(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    choice = response.choices[0]
    text = _plain(getattr(choice.message, "content", "")).strip()
    return CallResult(
        text=text,
        provider="Mistral",
        model=model,
        finish_reason=_finish_value(getattr(choice, "finish_reason", "")),
        usage=_usage_dict(getattr(response, "usage", None)),
    )


def call_cloudflare(
    api_token: str,
    account_id: str,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int,
) -> CallResult:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id.strip()}/ai/v1/chat/completions"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_token.strip()}",
            "Content-Type": "application/json",
        },
        json={
            "model": model.strip(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        },
        timeout=180,
    )
    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Cloudflare HTTP {response.status_code}: {detail}")

    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise EmptyModelResponse(f"Cloudflare 응답에 choices가 없음: {payload}")
    choice = choices[0]
    message = choice.get("message") or {}
    text = _plain(message.get("content", "")).strip()
    return CallResult(
        text=text,
        provider="Cloudflare",
        model=model,
        finish_reason=_finish_value(choice.get("finish_reason", "")),
        usage=_usage_dict(payload.get("usage")),
    )


def provider_call(
    provider: str,
    credentials: Dict[str, str],
    models: Dict[str, str],
    system: str,
    prompt: str,
    max_tokens: int,
    state: ProviderState,
) -> CallResult:
    state.consume(provider)
    if provider == "Gemini":
        return call_gemini(credentials.get("Gemini", ""), models[provider], system, prompt, max_tokens)
    if provider == "Groq":
        return call_groq(credentials.get("Groq", ""), models[provider], system, prompt, max_tokens)
    if provider == "Mistral":
        return call_mistral(credentials.get("Mistral", ""), models[provider], system, prompt, max_tokens)
    if provider == "Cloudflare":
        return call_cloudflare(
            credentials.get("Cloudflare Token", ""),
            credentials.get("Cloudflare Account ID", ""),
            models[provider],
            system,
            prompt,
            max_tokens,
        )
    raise ValueError(f"알 수 없는 provider: {provider}")


def _repair_empty_prompt(original_prompt: str) -> str:
    return f"""
{compact_prompt(original_prompt, 12000)}

[중요]
앞선 요청에서 최종 답변 본문이 비어 있었다. 내부 추론 과정은 출력하지 말고,
사용자에게 보여줄 완성된 최종 답변만 한국어로 작성하라.
"""


def _continuation_prompt(original_prompt: str, text_so_far: str) -> str:
    return f"""
[원래 과제 요약]
{compact_prompt(original_prompt, 6000)}

[이미 출력된 답변의 마지막 부분]
{compact_prompt(text_so_far, 8000)}

위 답변은 출력 길이 제한으로 중간에 끊겼다.
이미 쓴 내용을 반복하지 말고 정확히 이어지는 나머지 부분만 작성하라.
'계속' 같은 머리말 없이 본문부터 이어서 출력하라.
"""


def call_provider_complete(
    provider: str,
    credentials: Dict[str, str],
    models: Dict[str, str],
    system: str,
    prompt: str,
    max_tokens: int,
    state: ProviderState,
    auto_continue: int,
) -> CallResult:
    result = provider_call(provider, credentials, models, system, prompt, max_tokens, state)

    if not result.text:
        result.attempts.append(f"{provider}: 빈 최종 응답 감지, 같은 모델로 1회 재시도")
        repaired = provider_call(
            provider,
            credentials,
            models,
            system,
            _repair_empty_prompt(prompt),
            max_tokens,
            state,
        )
        repaired.request_count += result.request_count
        repaired.attempts = result.attempts + repaired.attempts
        result = repaired

    if not result.text:
        raise EmptyModelResponse(f"{provider}가 최종 답변 본문을 반환하지 않음")

    combined = result.text
    finish = result.finish_reason
    usage = dict(result.usage)
    request_count = result.request_count
    continuations = 0
    attempts = list(result.attempts)

    while _is_truncated(finish) and continuations < auto_continue:
        try:
            continuation = provider_call(
                provider,
                credentials,
                models,
                system,
                _continuation_prompt(prompt, combined),
                max_tokens,
                state,
            )
            request_count += continuation.request_count
            if not continuation.text:
                attempts.append(f"{provider}: 이어쓰기 응답이 비어 있어 중단")
                break
            combined = combined.rstrip() + "\n\n" + continuation.text.lstrip()
            finish = continuation.finish_reason
            continuations += 1
            for key, value in continuation.usage.items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
            attempts.extend(continuation.attempts)
        except Exception as exc:
            attempts.append(f"{provider}: 자동 이어쓰기 실패 - {exc}")
            break

    return CallResult(
        text=combined,
        provider=provider,
        model=models[provider],
        finish_reason=finish,
        usage=usage,
        continuation_count=continuations,
        request_count=request_count,
        truncated=_is_truncated(finish),
        attempts=attempts,
    )


def call_with_fallback(
    requested: str,
    fallback_order: List[str],
    credentials: Dict[str, str],
    models: Dict[str, str],
    system: str,
    prompt: str,
    max_tokens: int,
    state: ProviderState,
    auto_continue: int = 1,
) -> CallResult:
    order = [requested] + [provider for provider in fallback_order if provider != requested]
    errors: List[str] = []

    for provider in order:
        if provider not in PROVIDERS:
            continue
        if not state.available(provider):
            errors.append(f"{provider}: 현재 토론에서 차단됨({state.disabled[provider]})")
            continue
        if not has_credentials(provider, credentials):
            errors.append(f"{provider}: API 인증정보 없음")
            continue

        try:
            result = call_provider_complete(
                provider,
                credentials,
                models,
                system,
                prompt,
                max_tokens,
                state,
                auto_continue,
            )
            result.attempts = errors + result.attempts
            return result
        except Exception as exc:
            first_error = str(exc)

            if _looks_too_large(exc):
                try:
                    shorter = compact_prompt(prompt, 11000)
                    result = call_provider_complete(
                        provider,
                        credentials,
                        models,
                        system,
                        shorter,
                        min(max_tokens, 1000),
                        state,
                        auto_continue,
                    )
                    result.attempts = errors + [f"{provider}: 입력 축약 후 재시도 성공"] + result.attempts
                    return result
                except Exception as retry_exc:
                    first_error += f" | 입력 축약 재시도 실패: {retry_exc}"
                    exc = retry_exc

            elif _looks_retryable(exc):
                try:
                    time.sleep(2)
                    result = call_provider_complete(
                        provider,
                        credentials,
                        models,
                        system,
                        prompt,
                        max_tokens,
                        state,
                        auto_continue,
                    )
                    result.attempts = errors + [f"{provider}: 일시 오류 후 재시도 성공"] + result.attempts
                    return result
                except Exception as retry_exc:
                    first_error += f" | 일시 오류 재시도 실패: {retry_exc}"
                    exc = retry_exc

            if _looks_like_quota(exc):
                state.block(provider, "할당량 또는 요청 한도 초과")
            elif _looks_like_auth(exc):
                state.block(provider, "인증 또는 권한 오류")

            errors.append(f"{provider}: {first_error[:700]}")

    raise RuntimeError("모든 LLM 호출 실패 | " + " | ".join(errors))


def groq_web_research(api_key: str, query: str, max_tokens: int = 1100) -> str:
    if not api_key:
        return "[웹조사 생략: GROQ_API_KEY 없음]"
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="groq/compound-mini",
        messages=[
            {
                "role": "user",
                "content": (
                    "다음 주제를 웹에서 조사하라. 핵심 사실, 서로 충돌하는 정보, 날짜와 출처 URL을 "
                    "간결히 정리하라. 확인되지 않은 내용은 추측하지 마라.\n\n주제:\n" + query
                ),
            }
        ],
        max_completion_tokens=max_tokens,
    )
    text = _plain(response.choices[0].message.content).strip()
    if not text:
        raise EmptyModelResponse("Groq 공용 웹조사가 빈 응답을 반환함")
    return text
