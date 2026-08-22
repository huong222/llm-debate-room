import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from google import genai
from google.genai import types
from groq import Groq
from mistralai.client import Mistral


@dataclass
class ProviderState:
    disabled: Dict[str, str] = field(default_factory=dict)
    gemini_calls: int = 0
    gemini_budget: int = 1

    def block(self, provider: str, reason: str) -> None:
        self.disabled[provider] = reason

    def available(self, provider: str) -> bool:
        return provider not in self.disabled


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _looks_like_quota(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(x in s for x in ["429", "quota", "resource_exhausted", "too many requests", "credit_balance_exhausted"])


def _looks_too_large(exc: Exception) -> bool:
    s = str(exc).lower()
    return "413" in s or "request too large" in s or "context length" in s or "tokens per minute" in s


def compact_prompt(text: str, max_chars: int = 18000) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return head + "\n\n[...중간 내용은 토큰 한도 때문에 생략됨...]\n\n" + tail


def call_gemini(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> str:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model.strip(),
        contents=f"SYSTEM:\n{system}\n\nTASK:\n{prompt}",
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return _text(response.text).strip()


def call_groq(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> str:
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model.strip(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.35,
    )
    return _text(response.choices[0].message.content).strip()


def call_mistral(api_key: str, model: str, system: str, prompt: str, max_tokens: int) -> str:
    client = Mistral(api_key=api_key)
    response = client.chat.complete(
        model=model.strip(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.35,
    )
    return _text(response.choices[0].message.content).strip()


def provider_call(provider: str, keys: Dict[str, str], models: Dict[str, str], system: str, prompt: str, max_tokens: int, state: ProviderState) -> str:
    if provider == "Gemini":
        if state.gemini_calls >= state.gemini_budget:
            raise RuntimeError("이번 토론의 Gemini 호출 예산을 모두 사용함")
        state.gemini_calls += 1
        return call_gemini(keys.get("Gemini", ""), models["Gemini"], system, prompt, max_tokens)
    if provider == "Groq":
        return call_groq(keys.get("Groq", ""), models["Groq"], system, prompt, max_tokens)
    if provider == "Mistral":
        return call_mistral(keys.get("Mistral", ""), models["Mistral"], system, prompt, max_tokens)
    raise ValueError(f"알 수 없는 provider: {provider}")


def call_with_fallback(requested: str, fallback_order: List[str], keys: Dict[str, str], models: Dict[str, str], system: str, prompt: str, max_tokens: int, state: ProviderState) -> Tuple[str, str]:
    order = [requested] + [p for p in fallback_order if p != requested]
    errors = []
    for provider in order:
        if not state.available(provider):
            errors.append(f"{provider}: 차단됨({state.disabled[provider]})")
            continue
        if not keys.get(provider):
            errors.append(f"{provider}: API 키 없음")
            continue
        try:
            return provider_call(provider, keys, models, system, prompt, max_tokens, state), provider
        except Exception as exc:
            first_error = str(exc)
            if _looks_too_large(exc):
                try:
                    shorter = compact_prompt(prompt, 11000)
                    return provider_call(provider, keys, models, system, shorter, min(max_tokens, 700), state), provider + " (축약 재시도)"
                except Exception as exc2:
                    first_error += f" | 축약 재시도 실패: {exc2}"
                    exc = exc2
            if _looks_like_quota(exc):
                state.block(provider, "할당량/요청 한도 초과")
            errors.append(f"{provider}: {first_error[:500]}")
    raise RuntimeError("모든 LLM 호출 실패 | " + " | ".join(errors))


def groq_web_research(api_key: str, query: str, max_tokens: int = 1100) -> str:
    if not api_key:
        return "[웹조사 생략: GROQ_API_KEY 없음]"
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model="groq/compound-mini",
        messages=[{"role": "user", "content": "다음 주제를 웹에서 조사하라. 핵심 사실, 서로 충돌하는 정보, 날짜, 출처 URL을 간결히 정리하라. 확인되지 않은 내용은 추측하지 마라.\n\n주제:\n" + query}],
        max_tokens=max_tokens,
    )
    return _text(response.choices[0].message.content).strip()
