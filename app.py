from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import streamlit as st

from memory_store import MemoryStore
from providers import CallResult, PROVIDERS, ProviderState, call_with_fallback, groq_web_research

APP_DIR = Path(__file__).resolve().parent
MEMORY = MemoryStore(APP_DIR / "data" / "memory.sqlite3")

ROLE_RULES = {
    "Analyst": "가장 강한 설명·가설·해결책을 세우고 그것을 지지하는 근거를 제시한다.",
    "Contrarian": "다른 참가자의 주장에 숨어 있는 가정, 반례, 선택편향과 과잉확신을 공격한다.",
    "Alternative": "기존 양쪽 논쟁에 갇히지 말고 제3의 설명, 다른 전략과 누락된 변수를 제시한다.",
    "Verifier": "FACT / INFERENCE / HYPOTHESIS / UNKNOWN을 구분하고 출처와 논리 연결을 검사한다.",
}

COMMON = """
규칙:
- FACT / INFERENCE / HYPOTHESIS / UNKNOWN을 명확히 구분한다.
- 사실처럼 보이는 추측을 만들지 않는다.
- 사용자가 원하는 결론에 맞추지 않는다.
- 조건문만 늘어놓지 말고 현재 증거로 가능한 최선의 판단을 선택한다.
- 반대 근거가 강하면 입장을 수정한다.
- 웹조사 요약도 무조건 신뢰하지 말고 출처와 내부 일관성을 검토한다.
- 내부 추론 과정은 노출하지 말고, 검증 가능한 근거와 결론만 제시한다.
"""

JUDGE_SYSTEM = """
너는 독립적인 최종 판사다. 다수결을 따르지 말고 증거의 질과 논리의 강도를 비교한다.
출력 순서:
1. 문제 정의
2. 확인된 사실
3. 핵심 쟁점
4. 참가자별 강점과 약점
5. 가장 강한 반론
6. UNKNOWN / 추가 확인 사항
7. 최종 판결
8. 신뢰도 0~100
9. 판결을 뒤집을 조건
10. 실행 가능한 다음 행동
내부 추론 과정은 쓰지 말고 판결 이유를 검증 가능한 형태로 설명한다.
"""

DEFAULT_ROLE_PROVIDER = {
    "Analyst": "Gemini",
    "Contrarian": "Groq",
    "Alternative": "Mistral",
    "Verifier": "Cloudflare",
    "Judge": "Cloudflare",
}


def clip(text: str, length: int = 3200) -> str:
    text = text or ""
    if len(text) <= length:
        return text
    half = length // 2
    return text[:half] + "\n...[중간 생략]...\n" + text[-half:]


def result_dict(result: CallResult) -> Dict[str, Any]:
    return result.to_dict()


def response_text(item: Dict[str, Any]) -> str:
    return str(item.get("text", ""))


def debate_digest(responses: Dict[str, Dict[str, Any]]) -> str:
    parts = []
    for role, item in responses.items():
        provider = item.get("provider", item.get("actual_provider", "?"))
        parts.append(f"[{role} / {provider}]\n{clip(response_text(item), 2400)}")
    return "\n\n".join(parts)


def render_result(result: CallResult) -> None:
    status = (
        f"실제: {result.provider} · 모델: {result.model} · 종료: {result.finish_reason or '미표시'} "
        f"· 요청 {result.request_count}회 · 자동 이어쓰기 {result.continuation_count}회"
    )
    if result.truncated:
        status += " · ⚠️ 여전히 길이 제한 가능성"
    st.caption(status)
    st.write(result.text)
    if result.usage or result.attempts:
        with st.expander("호출 진단", expanded=False):
            if result.usage:
                st.json(result.usage)
            if result.attempts:
                st.markdown("**대체·재시도 기록**")
                for attempt in result.attempts:
                    st.write(f"- {attempt}")


def role_system(role: str) -> str:
    return f"너는 Debate Room의 {role}다. 임무: {ROLE_RULES[role]}\n{COMMON}"


def latest_role_text(transcript: Dict[str, Any], role: str) -> str:
    for round_item in reversed(transcript.get("rounds", [])):
        response = (round_item.get("responses") or {}).get(role)
        if response:
            return response_text(response)
    return ""


st.set_page_config(page_title="LLM Debate Room", layout="wide")
st.title("🧠 LLM Debate Room")
st.caption("Gemini + Groq + Mistral + Cloudflare · 공용 웹조사 · 자동 대체 · 자동 이어쓰기 · 후속 질문")

with st.sidebar:
    st.header("API 인증정보")
    credentials = {
        "Gemini": st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password"),
        "Groq": st.text_input("Groq API Key", value=os.getenv("GROQ_API_KEY", ""), type="password"),
        "Mistral": st.text_input("Mistral API Key", value=os.getenv("MISTRAL_API_KEY", ""), type="password"),
        "Cloudflare Token": st.text_input(
            "Cloudflare Workers AI API Token",
            value=os.getenv("CLOUDFLARE_API_TOKEN", ""),
            type="password",
        ),
        "Cloudflare Account ID": st.text_input(
            "Cloudflare Account ID",
            value=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
        ),
    }

    st.header("모델")
    models = {
        "Gemini": st.text_input("Gemini model", value=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")),
        "Groq": st.text_input("Groq model", value=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")),
        "Mistral": st.text_input("Mistral model", value=os.getenv("MISTRAL_MODEL", "mistral-small-latest")),
        "Cloudflare": st.text_input(
            "Cloudflare model",
            value=os.getenv("CLOUDFLARE_MODEL", "@cf/nvidia/nemotron-3-120b-a12b"),
        ),
    }

    st.header("토론 설정")
    web_research = st.toggle("Groq 공용 웹조사", value=True)
    rounds = st.slider("추가 반박 라운드", 0, 3, 1)
    agent_tokens = st.slider("일반 발언 최대 토큰", 600, 3000, 1400, 100)
    judge_tokens = st.slider("판결문 최대 토큰", 1000, 5000, 2400, 100)
    followup_tokens = st.slider("후속 답변 최대 토큰", 600, 4000, 1600, 100)
    auto_continue = st.slider("길이 제한 시 자동 이어쓰기", 0, 2, 1)
    memory_n = st.slider("관련 과거 토론", 0, 8, 4)

    with st.expander("제공자별 이번 토론 호출 예산", expanded=False):
        st.caption("0은 제한 없음. API 자체의 무료 한도는 별도로 적용됩니다.")
        budgets = {
            "Gemini": st.slider("Gemini", 0, 12, 2),
            "Groq": st.slider("Groq", 0, 20, 8),
            "Mistral": st.slider("Mistral", 0, 20, 8),
            "Cloudflare": st.slider("Cloudflare", 0, 16, 5),
        }

    st.header("역할별 제공자")
    role_provider: Dict[str, str] = {}
    options = list(PROVIDERS)
    for role in list(ROLE_RULES) + ["Judge"]:
        default = DEFAULT_ROLE_PROVIDER[role]
        role_provider[role] = st.selectbox(
            role,
            options,
            index=options.index(default),
            key=f"provider_{role}",
        )
    st.caption("429·413·빈 응답·일시 장애가 나면 다른 제공자로 자동 대체합니다.")

topic = st.text_area("토론 주제 / 프롬프트", height=150)
context = st.text_area("추가 정보 / 자료", height=140)

if st.button("🚀 Debate 시작", type="primary", use_container_width=True):
    if not topic.strip():
        st.error("토론 주제를 입력하세요.")
        st.stop()
    if not any(
        [
            credentials.get("Gemini"),
            credentials.get("Groq"),
            credentials.get("Mistral"),
            credentials.get("Cloudflare Token") and credentials.get("Cloudflare Account ID"),
        ]
    ):
        st.error("사용 가능한 API 인증정보가 최소 하나는 필요합니다.")
        st.stop()

    state = ProviderState(budgets=budgets)
    fallback = ["Cloudflare", "Mistral", "Groq", "Gemini"]
    memories = MEMORY.recall(topic, memory_n) if memory_n else []
    memory_text = "\n".join(
        f"- #{row[0]} {row[2]} | 과거 판결: {clip(row[3] or '', 700)}" for row in memories
    ) or "(관련 기록 없음)"

    research = "(웹조사 사용 안 함)"
    if web_research:
        with st.spinner("공용 웹조사 중..."):
            try:
                research = groq_web_research(credentials.get("Groq", ""), topic + "\n" + context)
            except Exception as exc:
                research = f"[공용 웹조사 실패: {exc}]"
                st.warning(f"공용 웹조사는 실패했지만 토론은 계속합니다: {exc}")
    with st.expander("🌐 공용 Evidence Brief", expanded=web_research):
        st.write(research)

    base = f"""
[주제]
{topic}

[사용자 제공 정보]
{context or '(없음)'}

[공용 웹조사]
{clip(research, 6500)}

[관련 과거 Debate Memory]
{memory_text}

{COMMON}
"""
    transcript: Dict[str, Any] = {
        "topic": topic,
        "context": context,
        "research": research,
        "rounds": [],
        "role_provider": role_provider,
        "models": models,
    }

    st.subheader("1. 독립 분석")
    cols = st.columns(4)
    current: Dict[str, Dict[str, Any]] = {}
    for idx, role in enumerate(ROLE_RULES):
        with cols[idx]:
            st.markdown(f"### {role}")
            try:
                result = call_with_fallback(
                    role_provider[role],
                    fallback,
                    credentials,
                    models,
                    role_system(role),
                    base,
                    agent_tokens,
                    state,
                    auto_continue,
                )
                st.caption(f"요청: {role_provider[role]}")
                render_result(result)
                current[role] = result_dict(result)
                current[role]["requested_provider"] = role_provider[role]
            except Exception as exc:
                st.error(str(exc))
                current[role] = {
                    "text": f"[ERROR] {exc}",
                    "provider": "ERROR",
                    "requested_provider": role_provider[role],
                }
    transcript["rounds"].append({"round": 0, "responses": current})

    for round_number in range(1, rounds + 1):
        st.subheader(f"2-{round_number}. 반박 라운드 {round_number}")
        digest = debate_digest(current)
        next_round: Dict[str, Dict[str, Any]] = {}
        for role in ROLE_RULES:
            prompt = f"""
{base}

[직전 라운드 핵심 발언]
{digest}

이번 라운드 임무:
- 다른 참가자의 구체적인 주장 최소 1개를 직접 평가한다.
- 맞는 점과 틀린 점을 분리한다.
- 필요하면 네 기존 입장을 수정한다.
- 새 결론과 그 결론을 뒤집을 조건을 명시한다.
"""
            with st.expander(f"{role} · 요청: {role_provider[role]}", expanded=True):
                try:
                    result = call_with_fallback(
                        role_provider[role],
                        fallback,
                        credentials,
                        models,
                        role_system(role),
                        prompt,
                        agent_tokens,
                        state,
                        auto_continue,
                    )
                    render_result(result)
                    next_round[role] = result_dict(result)
                    next_round[role]["requested_provider"] = role_provider[role]
                except Exception as exc:
                    st.error(str(exc))
                    next_round[role] = {
                        "text": f"[ERROR] {exc}",
                        "provider": "ERROR",
                        "requested_provider": role_provider[role],
                    }
        current = next_round
        transcript["rounds"].append({"round": round_number, "responses": current})

    st.subheader("⚖️ 3. 최종 판결")
    round_digests = [
        f"[Round {item['round']}]\n{debate_digest(item['responses'])}" for item in transcript["rounds"]
    ]
    judge_prompt = f"""
[주제]
{topic}

[사용자 정보]
{clip(context, 3500)}

[웹조사]
{clip(research, 4500)}

[토론 기록 - 구조화 압축]
{clip(chr(10).join(round_digests), 15000)}

전체 기록을 검토하고 독립적으로 판결하라.
"""
    verdict = ""
    judge_result: Dict[str, Any] = {}
    try:
        result = call_with_fallback(
            role_provider["Judge"],
            fallback,
            credentials,
            models,
            JUDGE_SYSTEM,
            judge_prompt,
            judge_tokens,
            state,
            auto_continue,
        )
        st.caption(f"Judge 요청: {role_provider['Judge']}")
        render_result(result)
        verdict = result.text
        judge_result = result_dict(result)
    except Exception as exc:
        verdict = f"[Judge ERROR] {exc}"
        judge_result = {"text": verdict, "provider": "ERROR"}
        st.error(verdict)

    transcript["verdict"] = verdict
    transcript["judge_result"] = judge_result
    transcript["provider_state"] = {
        "disabled": state.disabled,
        "calls": state.calls,
        "budgets": state.budgets,
    }
    row_id = MEMORY.save(topic, context, verdict, transcript)
    st.session_state["last_debate"] = {
        "id": row_id,
        "topic": topic,
        "context": context,
        "verdict": verdict,
        "transcript": transcript,
    }
    st.success(f"Debate #{row_id} 저장 완료")
    st.download_button(
        "전체 기록 JSON 저장",
        data=json.dumps(transcript, ensure_ascii=False, indent=2),
        file_name=f"debate_{row_id:06d}.json",
        mime="application/json",
    )

st.divider()
st.subheader("💬 토론 종료 후 특정 참가자에게 질문")
last_debate = st.session_state.get("last_debate")
if not last_debate:
    st.info("먼저 Debate를 실행하거나 아래 과거 Debate에서 기록을 불러오세요.")
else:
    followup_target = st.selectbox("질문할 참가자", ["Judge"] + list(ROLE_RULES), key="followup_target")
    default_provider = (last_debate["transcript"].get("role_provider") or {}).get(
        followup_target,
        DEFAULT_ROLE_PROVIDER.get(followup_target, "Cloudflare"),
    )
    followup_provider = st.selectbox(
        "답변 제공자",
        list(PROVIDERS),
        index=list(PROVIDERS).index(default_provider),
        key="followup_provider",
    )
    question_type = st.selectbox(
        "질문 유형",
        ["추가 질문", "사실 정정 요청", "판결 재검토", "근거 요구"],
        key="followup_type",
    )
    followup_question = st.text_area(
        "질문 또는 정정 내용",
        height=110,
        key="followup_question",
    )

    if st.button("선택한 참가자에게 질문", use_container_width=True):
        if not followup_question.strip():
            st.error("질문을 입력하세요.")
        else:
            transcript = last_debate["transcript"]
            target_text = (
                last_debate["verdict"]
                if followup_target == "Judge"
                else latest_role_text(transcript, followup_target)
            )
            followup_prompt = f"""
[원래 주제]
{last_debate['topic']}

[원래 사용자 정보]
{clip(last_debate.get('context', ''), 3500)}

[{followup_target}의 기존 발언]
{clip(target_text, 7500)}

[최종 판결]
{clip(last_debate.get('verdict', ''), 5000)}

[질문 유형]
{question_type}

[사용자의 새 질문/정정]
{followup_question}

기존 발언이 틀렸다면 명확히 정정하라. 무엇이 FACT이고 무엇이 추론인지 구분하고,
답을 회피하지 말고 현재 자료로 가능한 최선의 답을 완결된 형태로 제시하라.
"""
            followup_system = (
                JUDGE_SYSTEM
                if followup_target == "Judge"
                else role_system(followup_target)
            )
            followup_state = ProviderState(budgets=budgets)
            try:
                result = call_with_fallback(
                    followup_provider,
                    ["Cloudflare", "Mistral", "Groq", "Gemini"],
                    credentials,
                    models,
                    followup_system,
                    followup_prompt,
                    followup_tokens,
                    followup_state,
                    auto_continue,
                )
                render_result(result)
                followup_id = MEMORY.save_followup(
                    last_debate["id"],
                    followup_target,
                    result.provider,
                    question_type,
                    followup_question,
                    result.text,
                    result_dict(result),
                )
                st.success(f"후속 질문 #{followup_id} 저장 완료")
            except Exception as exc:
                st.error(f"후속 질문 실패: {exc}")

    past_followups = MEMORY.get_followups(last_debate["id"])
    if past_followups:
        with st.expander(f"이 Debate의 기존 후속 질문 {len(past_followups)}건"):
            for item in past_followups:
                st.markdown(f"**{item['target_role']} · {item['question_type']} · {item['provider']}**")
                st.write(f"Q. {item['question']}")
                st.write(item["answer"])
                st.divider()

st.divider()
st.subheader("🗃️ 과거 Debate 검색")
query = st.text_input("검색어")
if query:
    rows = MEMORY.recall(query, 20)
    if not rows:
        st.info("검색 결과가 없습니다.")
    for row in rows:
        with st.expander(f"#{row[0]} · {row[2]}"):
            st.caption(row[1])
            st.write(row[3])
            if st.button("이 Debate를 후속 질문 대상으로 불러오기", key=f"load_{row[0]}"):
                loaded = MEMORY.get(row[0])
                if loaded:
                    st.session_state["last_debate"] = loaded
                    st.rerun()
