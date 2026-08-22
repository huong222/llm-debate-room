import json
import os
from pathlib import Path

import streamlit as st

from memory_store import MemoryStore
from providers import ProviderState, call_with_fallback, groq_web_research

APP_DIR = Path(__file__).resolve().parent
MEMORY = MemoryStore(APP_DIR / "data" / "memory.sqlite3")

ROLE_RULES = {
    "Analyst": "가장 강한 설명·가설·해결책을 세우고 그것을 지지하는 근거를 제시한다.",
    "Contrarian": "Analyst와 다른 참가자의 주장에 숨어 있는 가정, 반례, 과잉확신을 공격한다.",
    "Alternative": "기존 양쪽 논쟁에 갇히지 말고 제3의 설명, 다른 전략, 누락된 변수를 제시한다.",
    "Verifier": "FACT / INFERENCE / HYPOTHESIS / UNKNOWN을 구분하고 출처와 논리 연결을 검사한다.",
}

COMMON = """
규칙:
- FACT / INFERENCE / HYPOTHESIS / UNKNOWN을 구분한다.
- 사실처럼 보이는 추측을 만들지 않는다.
- 사용자가 원하는 결론에 맞추지 않는다.
- 조건문만 늘어놓지 말고 현재 증거로 가장 합리적인 판단을 선택한다.
- 반대 근거가 강하면 입장을 수정한다.
- 웹조사 요약이 주어졌다면 그 내용도 무조건 신뢰하지 말고 내부 일관성을 검토한다.
"""

JUDGE_SYSTEM = """
너는 독립적인 최종 판사다. 다수결을 따르지 말고 증거의 질과 논리의 강도를 비교하라.
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
"""

DEFAULT_ROLE_PROVIDER = {
    "Analyst": "Gemini",
    "Contrarian": "Groq",
    "Alternative": "Mistral",
    "Verifier": "Groq",
    "Judge": "Mistral",
}


def clip(text: str, n: int = 3200) -> str:
    text = text or ""
    if len(text) <= n:
        return text
    return text[: n // 2] + "\n...[중간 생략]...\n" + text[-n // 2 :]


def debate_digest(responses: dict) -> str:
    parts = []
    for role, item in responses.items():
        parts.append(f"[{role} / {item.get('actual_provider','?')}]\n{clip(item.get('text',''), 2600)}")
    return "\n\n".join(parts)


st.set_page_config(page_title="LLM Debate Room", layout="wide")
st.title("🧠 LLM Debate Room")
st.caption("Gemini + Groq + Mistral · 공용 웹조사 · 다중 모델 반론 · 장기 기록")

with st.sidebar:
    st.header("API Keys")
    keys = {
        "Gemini": st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password"),
        "Groq": st.text_input("Groq API Key", value=os.getenv("GROQ_API_KEY", ""), type="password"),
        "Mistral": st.text_input("Mistral API Key", value=os.getenv("MISTRAL_API_KEY", ""), type="password"),
    }
    st.header("모델")
    models = {
        "Gemini": st.text_input("Gemini model", value=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")),
        "Groq": st.text_input("Groq model", value=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")),
        "Mistral": st.text_input("Mistral model", value=os.getenv("MISTRAL_MODEL", "mistral-small-latest")),
    }
    st.header("토론 설정")
    web_research = st.toggle("공용 웹조사", value=True)
    rounds = st.slider("추가 반박 라운드", 0, 3, 1)
    max_tokens = st.slider("발언 최대 토큰", 400, 1800, 900, 100)
    memory_n = st.slider("관련 과거 토론", 0, 8, 4)
    gemini_budget = st.slider("Gemini 최대 호출", 0, 4, 1)
    st.header("역할별 제공자")
    role_provider = {}
    for role in list(ROLE_RULES) + ["Judge"]:
        options = ["Gemini", "Groq", "Mistral"]
        default = DEFAULT_ROLE_PROVIDER[role]
        role_provider[role] = st.selectbox(role, options, index=options.index(default), key=f"provider_{role}")
    st.caption("429/413 등 장애가 나면 다른 제공자로 자동 대체합니다.")

topic = st.text_area("토론 주제 / 프롬프트", height=150)
context = st.text_area("추가 정보 / 자료", height=140)

if st.button("🚀 Debate 시작", type="primary", use_container_width=True):
    if not topic.strip():
        st.error("토론 주제를 입력하세요.")
        st.stop()
    if not any(keys.values()):
        st.error("API Key가 최소 하나는 필요합니다.")
        st.stop()

    state = ProviderState(gemini_budget=gemini_budget)
    fallback = ["Groq", "Mistral", "Gemini"]
    memories = MEMORY.recall(topic, memory_n) if memory_n else []
    memory_text = "\n".join(f"- #{r[0]} {r[2]} | 과거 판결: {clip(r[3] or '', 700)}" for r in memories) or "(관련 기록 없음)"

    research = "(웹조사 사용 안 함)"
    if web_research:
        with st.spinner("공용 웹조사 중..."):
            try:
                research = groq_web_research(keys.get("Groq", ""), topic + "\n" + context)
            except Exception as exc:
                research = f"[공용 웹조사 실패: {exc}]"
                st.warning("공용 웹조사는 실패했지만 토론은 계속합니다.")
    with st.expander("🌐 공용 Evidence Brief", expanded=web_research):
        st.write(research)

    base = f"""
[주제]
{topic}

[사용자 제공 정보]
{context or '(없음)'}

[공용 웹조사]
{clip(research, 7000)}

[관련 과거 Debate Memory]
{memory_text}

{COMMON}
"""
    transcript = {"topic": topic, "context": context, "research": research, "rounds": []}

    st.subheader("1. 독립 분석")
    cols = st.columns(4)
    current = {}
    for idx, role in enumerate(ROLE_RULES):
        system = f"너는 Debate Room의 {role}다. 임무: {ROLE_RULES[role]}\n{COMMON}"
        with cols[idx]:
            st.markdown(f"### {role}")
            try:
                text, actual = call_with_fallback(role_provider[role], fallback, keys, models, system, base, max_tokens, state)
                st.caption(f"요청: {role_provider[role]} · 실제: {actual}")
                st.write(text)
                current[role] = {"text": text, "actual_provider": actual, "requested_provider": role_provider[role]}
            except Exception as exc:
                st.error(str(exc))
                current[role] = {"text": f"[ERROR] {exc}", "actual_provider": "ERROR", "requested_provider": role_provider[role]}
    transcript["rounds"].append({"round": 0, "responses": current})

    for rnd in range(1, rounds + 1):
        st.subheader(f"2-{rnd}. 반박 라운드 {rnd}")
        digest = debate_digest(current)
        next_round = {}
        for role in ROLE_RULES:
            system = f"너는 Debate Room의 {role}다. 임무: {ROLE_RULES[role]}\n{COMMON}"
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
                    text, actual = call_with_fallback(role_provider[role], fallback, keys, models, system, prompt, max_tokens, state)
                    st.caption(f"실제: {actual}")
                    st.write(text)
                    next_round[role] = {"text": text, "actual_provider": actual, "requested_provider": role_provider[role]}
                except Exception as exc:
                    st.error(str(exc))
                    next_round[role] = {"text": f"[ERROR] {exc}", "actual_provider": "ERROR", "requested_provider": role_provider[role]}
        current = next_round
        transcript["rounds"].append({"round": rnd, "responses": current})

    st.subheader("⚖️ 3. 최종 판결")
    all_round_digests = []
    for rr in transcript["rounds"]:
        all_round_digests.append(f"[Round {rr['round']}]\n{debate_digest(rr['responses'])}")
    judge_prompt = f"""
[주제]
{topic}

[사용자 정보]
{clip(context, 3500)}

[웹조사]
{clip(research, 5000)}

[토론 기록 - 압축]
{clip(chr(10).join(all_round_digests), 14500)}

전체 기록을 검토하고 독립적으로 판결하라.
"""
    verdict = ""
    try:
        verdict, actual = call_with_fallback(role_provider["Judge"], fallback, keys, models, JUDGE_SYSTEM, judge_prompt, max(max_tokens, 1000), state)
        st.caption(f"Judge 요청: {role_provider['Judge']} · 실제: {actual}")
        st.write(verdict)
    except Exception as exc:
        verdict = f"[Judge ERROR] {exc}"
        st.error(verdict)

    transcript["verdict"] = verdict
    transcript["provider_state"] = {"disabled": state.disabled, "gemini_calls": state.gemini_calls}
    row_id = MEMORY.save(topic, context, verdict, transcript)
    st.success(f"Debate #{row_id} 저장 완료")
    st.download_button("전체 기록 JSON 저장", data=json.dumps(transcript, ensure_ascii=False, indent=2), file_name=f"debate_{row_id:06d}.json", mime="application/json")

st.divider()
st.subheader("🗃️ 과거 Debate 검색")
q = st.text_input("검색어")
if q:
    rows = MEMORY.recall(q, 20)
    if not rows:
        st.info("검색 결과가 없습니다.")
    for row in rows:
        with st.expander(f"#{row[0]} · {row[2]}"):
            st.caption(row[1])
            st.write(row[3])
