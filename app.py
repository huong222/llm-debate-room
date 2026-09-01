from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import streamlit as st
from dotenv import load_dotenv

from memory_store import MemoryStore
from prompts import (
    AUDITOR_SYSTEM,
    CROSS_EXAM_SYSTEM_TEMPLATE,
    FOLLOWUP_SYSTEM_TEMPLATE,
    INDEPENDENT_SYSTEM_TEMPLATE,
    JUDGE_SYSTEM,
    REASONING_CONSTITUTION,
    ROLE_RULES,
    VERIFIER_SYSTEM,
)
from providers import (
    VERSION,
    CallResult,
    ProviderState,
    build_engines,
    call_with_fallback,
    compact_text,
    research_with_groq,
)


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")
MEMORY = MemoryStore(APP_DIR / "data" / "memory.sqlite3")

# 실제로 준비한 API만 사용한다.
# NVIDIA API 키 하나로 Nemotron Super / Ultra 두 모델을 사용한다.
ROLE_ENGINE_DEFAULTS = {
    "Analyst": "gemini",
    "Contrarian": "groq",
    "Alternative": "cerebras",
    "Red Team": "nvidia_super",
    "Verifier": "nvidia_super",
    "Auditor": "cerebras",
    "Judge": "nvidia_ultra",
}

FALLBACKS = {
    "Analyst": ["cerebras", "nvidia_super", "groq", "nvidia_ultra"],
    "Contrarian": ["cerebras", "nvidia_super", "gemini", "nvidia_ultra"],
    "Alternative": ["nvidia_super", "groq", "gemini", "nvidia_ultra"],
    "Red Team": ["cerebras", "groq", "gemini", "nvidia_ultra"],
    "Verifier": ["cerebras", "groq", "gemini", "nvidia_ultra"],
    "Auditor": ["nvidia_super", "groq", "gemini", "nvidia_ultra"],
    "Judge": ["nvidia_super", "cerebras", "gemini", "groq"],
}

ENGINE_LABELS = {
    "gemini": "Gemini",
    "groq": "Groq GPT-OSS",
    "cerebras": "Cerebras GPT-OSS",
    "nvidia_super": "NVIDIA Nemotron Super",
    "nvidia_ultra": "NVIDIA Nemotron Ultra",
}

st.set_page_config(page_title=f"LLM Debate Room {VERSION}", layout="wide")

if "current_debate" not in st.session_state:
    st.session_state.current_debate = None


def as_result_dict(result: CallResult) -> Dict[str, Any]:
    return result.to_dict()


def get_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("text") or "")
    return str(result or "")


def clip(text: str, chars: int) -> str:
    return compact_text(text or "", chars)


def digest_results(results: Dict[str, Dict[str, Any]], each_chars: int = 2200) -> str:
    parts: List[str] = []
    for role, result in results.items():
        parts.append(
            f"[{role} | {result.get('actual_engine', '?')} | {result.get('model', '?')}]\n"
            f"{clip(get_text(result), each_chars)}"
        )
    return "\n\n".join(parts)


def memory_digest(rows: Iterable[tuple]) -> str:
    rows = list(rows)
    if not rows:
        return "(관련 과거 토론 없음)"
    return "\n".join(
        f"- Debate #{row[0]} | {row[2]} | 과거 판결 일부: {clip(row[3] or '', 650)}"
        for row in rows
    )


def build_user_packet(
    topic: str,
    target: str,
    constraints: str,
    goal: str,
    context: str,
    memories: str,
) -> str:
    return f"""
[토론 주제]
{topic}

[정확한 대상·제품·모델명]
{target or '(지정 없음)'}

[USER-CONSTRAINT: 변경 금지 조건·필수 구성·사용자가 확정한 사실]
{constraints or '(지정 없음)'}

[목표·평가 기준]
{goal or '(지정 없음)'}

[추가 정보]
{context or '(없음)'}

[관련 과거 Debate Memory: 참고자료이며 현재 사실을 자동으로 증명하지 않음]
{memories}
"""


def run_role(
    *,
    role: str,
    requested_engine: str,
    engines: Dict[str, Any],
    state: ProviderState,
    system: str,
    prompt: str,
    max_tokens: int,
    continuations: int,
) -> Dict[str, Any]:
    result = call_with_fallback(
        requested_engine,
        FALLBACKS[role],
        engines,
        system,
        prompt,
        max_tokens,
        continuations,
        state,
    )
    return as_result_dict(result)


def error_result(exc: Exception, label: str = "ERROR") -> Dict[str, Any]:
    return {
        "text": f"[{label}] {exc}",
        "actual_engine": "ERROR",
        "model": "-",
        "finish_reason": "error",
    }


def render_result(title: str, result: Dict[str, Any]) -> None:
    st.markdown(f"### {title}")
    st.caption(
        f"실제 엔진: {result.get('actual_engine', '?')} · 모델: {result.get('model', '?')} · "
        f"종료: {result.get('finish_reason', '?')}"
    )
    st.markdown(get_text(result))
    with st.expander("호출 진단", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("입력 토큰", result.get("input_tokens") or "-")
        c2.metric("출력 토큰", result.get("output_tokens") or "-")
        c3.metric("이어쓰기", result.get("continuations") or 0)
        c4.metric("프롬프트 문자", result.get("prompt_chars") or 0)
        fallbacks = result.get("fallback_history") or []
        if fallbacks:
            st.info("대체 경로\n" + "\n".join(f"- {item}" for item in fallbacks))


def render_role_tabs(results: Dict[str, Dict[str, Any]], prefix: str) -> None:
    roles = list(results)
    if not roles:
        st.info("표시할 역할 응답이 없습니다.")
        return
    tabs = st.tabs(roles)
    for tab, role in zip(tabs, roles):
        with tab:
            render_result(f"{prefix} · {role}", results[role])


def render_debate(debate: Dict[str, Any]) -> None:
    st.header(f"토론 결과 · {debate.get('version', VERSION)}")
    st.caption(
        f"주제: {debate.get('topic', '')}"
        + (f" · Debate #{debate.get('debate_id')}" if debate.get("debate_id") else "")
    )

    research = debate.get("research") or {}
    with st.expander("공용 웹조사 / Evidence Brief", expanded=False):
        validation = research.get("validation") or {}
        if validation.get("usable"):
            st.success(f"검증 통과 · URL {len(validation.get('urls') or [])}개")
        else:
            st.warning("검증 실패 또는 미사용: " + ", ".join(validation.get("reasons") or ["사유 없음"]))
        st.markdown(research.get("text") or "(없음)")

    st.subheader("1. 독립 분석")
    render_role_tabs(debate.get("initial") or {}, "독립 분석")

    if debate.get("verifier"):
        st.subheader("2. Evidence Verifier")
        render_result("Verifier", debate["verifier"])

    for idx, round_item in enumerate(debate.get("rounds") or [], start=1):
        with st.expander(f"3-{idx}. 반박 라운드 {idx}", expanded=True):
            render_role_tabs(round_item.get("responses") or {}, f"Round {idx}")

    if debate.get("auditor"):
        st.subheader("4. Consistency & Constraint Audit")
        render_result("Auditor", debate["auditor"])

    st.subheader("⚖️ 5. 최종 판결")
    verdict_result = debate.get("verdict_result") or {}
    if verdict_result:
        render_result("Judge", verdict_result)
    else:
        st.markdown(debate.get("verdict") or "(판결 없음)")

    diagnostics = debate.get("diagnostics") or []
    with st.expander(f"전체 호출 진단 {len(diagnostics)}건", expanded=False):
        table = []
        for item in diagnostics:
            table.append(
                {
                    "요청": item.get("requested_engine"),
                    "실제": item.get("actual_engine"),
                    "모델": item.get("model"),
                    "종료": item.get("finish_reason"),
                    "입력": item.get("input_tokens"),
                    "출력": item.get("output_tokens"),
                    "이어쓰기": item.get("continuations"),
                }
            )
        if table:
            st.dataframe(table, use_container_width=True, hide_index=True)
        else:
            st.info("진단 기록이 없습니다.")

    st.download_button(
        "전체 토론 JSON 저장",
        data=json.dumps(debate, ensure_ascii=False, indent=2),
        file_name=f"debate_{debate.get('debate_id', 'unsaved')}_{VERSION}.json",
        mime="application/json",
        key=f"download_{debate.get('debate_id', 'unsaved')}",
    )


def result_for_target(debate: Dict[str, Any], target: str) -> Dict[str, Any]:
    if target == "Judge":
        return debate.get("verdict_result") or {}
    if target == "Verifier":
        return debate.get("verifier") or {}
    if target == "Auditor":
        return debate.get("auditor") or {}
    rounds = debate.get("rounds") or []
    if rounds:
        last = rounds[-1].get("responses") or {}
        if target in last:
            return last[target]
    return (debate.get("initial") or {}).get(target) or {}


st.title(f"🧠 LLM Debate Room — {VERSION}")
st.caption(
    "Gemini · Groq · Cerebras · NVIDIA NIM (Nemotron Super + Ultra) | "
    "독립 분석 → 증거 검증 → 반박 → 일관성 감사 → 판결"
)

with st.sidebar:
    st.header("API 키 · 실제 준비한 4개만")
    keys = {
        "Gemini": st.text_input("Gemini", value=os.getenv("GEMINI_API_KEY", ""), type="password"),
        "Groq": st.text_input("Groq", value=os.getenv("GROQ_API_KEY", ""), type="password"),
        "Cerebras": st.text_input("Cerebras", value=os.getenv("CEREBRAS_API_KEY", ""), type="password"),
        "NVIDIA": st.text_input("NVIDIA NIM", value=os.getenv("NVIDIA_API_KEY", ""), type="password"),
    }

    ready = [name for name, value in keys.items() if value]
    st.caption("입력됨: " + (" · ".join(ready) if ready else "없음"))

    with st.expander("모델 이름", expanded=False):
        models = {
            "gemini": st.text_input("Gemini model", value=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")),
            "groq": st.text_input("Groq model", value=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")),
            "cerebras": st.text_input("Cerebras model", value=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")),
            "nvidia_super": st.text_input(
                "NVIDIA Nemotron Super",
                value=os.getenv("NVIDIA_SUPER_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
            ),
            "nvidia_ultra": st.text_input(
                "NVIDIA Nemotron Ultra",
                value=os.getenv("NVIDIA_ULTRA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
            ),
        }

    st.header("기본 역할 배치")
    st.caption(
        "Analyst → Gemini\n\n"
        "Contrarian → Groq\n\n"
        "Alternative → Cerebras\n\n"
        "Red Team / Verifier → NVIDIA Super\n\n"
        "Auditor → Cerebras\n\n"
        "Judge → NVIDIA Ultra"
    )

    st.header("토론 설정")
    web_research = st.toggle("Groq 공용 웹조사", value=True)
    cross_rounds = st.slider("반박 라운드", 0, 2, 1)
    general_tokens = st.slider("일반 발언 최대 토큰", 600, 2600, 1300, 100)
    verifier_tokens = st.slider("Verifier 최대 토큰", 600, 2600, 1300, 100)
    auditor_tokens = st.slider("Auditor 최대 토큰", 600, 3000, 1500, 100)
    judge_tokens = st.slider("Judge 최대 토큰", 800, 5000, 2400, 100)
    followup_tokens = st.slider("후속 답변 최대 토큰", 600, 3000, 1500, 100)
    continuations = st.slider("출력 잘림 자동 이어쓰기", 0, 2, 1)
    memory_n = st.slider("관련 과거 토론", 0, 10, 4)

    with st.expander("고급 · 역할별 모델 바꾸기", expanded=False):
        engine_options = list(ENGINE_LABELS)
        role_engine: Dict[str, str] = {}
        for role in [*ROLE_RULES, "Verifier", "Auditor", "Judge"]:
            default_id = ROLE_ENGINE_DEFAULTS[role]
            role_engine[role] = st.selectbox(
                role,
                engine_options,
                index=engine_options.index(default_id),
                format_func=lambda x: ENGINE_LABELS[x],
                key=f"role_engine_{role}",
            )
    # expander를 닫아도 변수는 매 rerun마다 생성된다.
    if "role_engine" not in locals():
        role_engine = dict(ROLE_ENGINE_DEFAULTS)

    with st.expander("고급 · 이번 토론 호출 예산", expanded=False):
        budgets = {
            "Gemini": st.number_input("Gemini", min_value=0, max_value=30, value=3),
            "Groq": st.number_input("Groq", min_value=0, max_value=40, value=10),
            "Cerebras": st.number_input("Cerebras", min_value=0, max_value=30, value=8),
            "NVIDIA": st.number_input("NVIDIA", min_value=0, max_value=30, value=8),
        }

engines = build_engines(keys, models)

with st.form("debate_form"):
    topic = st.text_area("토론 주제 / 질문", height=120)
    target = st.text_area(
        "정확한 대상·제품·모델명",
        height=80,
        placeholder="정확한 모델명, 버전, 대상이 있으면 여기에 고정",
    )
    constraints = st.text_area(
        "변경 금지 조건 / 필수 구성 / 사용자가 확정한 사실",
        height=110,
        placeholder="토론 중 임의 변경하면 안 되는 조건",
    )
    goal = st.text_area(
        "목표·평가 기준",
        height=90,
        placeholder="무엇을 최적화하거나 판정해야 하는지",
    )
    context = st.text_area("추가 자료·설명", height=150)
    submitted = st.form_submit_button("🚀 토론 시작", type="primary", use_container_width=True)

if submitted:
    if not topic.strip():
        st.error("토론 주제를 입력하세요.")
    elif not any(keys.values()):
        st.error("API 키가 최소 하나는 필요합니다.")
    else:
        state = ProviderState(budgets={key: int(value) for key, value in budgets.items()})
        memories = MEMORY.recall(topic + " " + target, memory_n) if memory_n else []
        user_packet = build_user_packet(
            topic,
            target,
            constraints,
            goal,
            context,
            memory_digest(memories),
        )

        research_text = "(웹조사 사용 안 함)"
        research_validation: Dict[str, Any] = {
            "usable": False,
            "urls": [],
            "reasons": ["웹조사 사용 안 함"],
        }
        if web_research:
            with st.spinner("Groq 공용 웹조사 중..."):
                try:
                    research_text, research_validation = research_with_groq(keys.get("Groq", ""), user_packet)
                except Exception as exc:
                    research_text = f"[공용 웹조사 실패: {exc}]"
                    research_validation = {"usable": False, "urls": [], "reasons": [str(exc)]}
                    st.warning("공용 웹조사는 실패했지만 토론은 계속합니다.")

        initial: Dict[str, Dict[str, Any]] = {}
        with st.status("독립 분석 실행 중...", expanded=True) as status:
            for role, role_rule in ROLE_RULES.items():
                st.write(f"{role} 호출")
                system = INDEPENDENT_SYSTEM_TEMPLATE.format(
                    role=role,
                    role_rule=role_rule,
                    constitution=REASONING_CONSTITUTION,
                )
                prompt = user_packet + "\n\n다른 모델이나 웹조사 결과를 보지 않고 독립적으로 분석하라."
                try:
                    initial[role] = run_role(
                        role=role,
                        requested_engine=role_engine[role],
                        engines=engines,
                        state=state,
                        system=system,
                        prompt=prompt,
                        max_tokens=general_tokens,
                        continuations=continuations,
                    )
                except Exception as exc:
                    initial[role] = error_result(exc)
            status.update(label="독립 분석 완료", state="complete")

        evidence_label = "VERIFIED-EVIDENCE" if research_validation.get("usable") else "UNVERIFIED-RESEARCH"
        verifier_prompt = f"""
{user_packet}

[{evidence_label}]
{clip(research_text, 7500)}

[독립 분석 주장]
{digest_results(initial, 2100)}

각 핵심 주장을 검증하라. 자료가 검증 실패 상태면 그것을 사실로 승인하지 마라.
"""
        try:
            verifier = run_role(
                role="Verifier",
                requested_engine=role_engine["Verifier"],
                engines=engines,
                state=state,
                system=VERIFIER_SYSTEM,
                prompt=verifier_prompt,
                max_tokens=verifier_tokens,
                continuations=continuations,
            )
        except Exception as exc:
            verifier = error_result(exc)

        rounds: List[Dict[str, Any]] = []
        current = initial
        for round_no in range(1, cross_rounds + 1):
            next_round: Dict[str, Dict[str, Any]] = {}
            digest = digest_results(current, 1700)
            for role, role_rule in ROLE_RULES.items():
                system = CROSS_EXAM_SYSTEM_TEMPLATE.format(
                    role=role,
                    role_rule=role_rule,
                    constitution=REASONING_CONSTITUTION,
                )
                prompt = f"""
{user_packet}

[{evidence_label}]
{clip(research_text, 5000)}

[Evidence Verifier]
{clip(get_text(verifier), 3000)}

[직전 라운드 핵심 발언]
{digest}

반박 라운드 {round_no}를 수행하라.
"""
                try:
                    next_round[role] = run_role(
                        role=role,
                        requested_engine=role_engine[role],
                        engines=engines,
                        state=state,
                        system=system,
                        prompt=prompt,
                        max_tokens=general_tokens,
                        continuations=continuations,
                    )
                except Exception as exc:
                    next_round[role] = error_result(exc)
            rounds.append({"round": round_no, "responses": next_round})
            current = next_round

        all_debate_parts = ["[독립 분석]\n" + digest_results(initial, 1800)]
        for item in rounds:
            all_debate_parts.append(
                f"[반박 라운드 {item['round']}]\n" + digest_results(item["responses"], 1700)
            )
        all_debate_digest = "\n\n".join(all_debate_parts)

        auditor_prompt = f"""
{user_packet}

[Evidence Verifier]
{clip(get_text(verifier), 3200)}

[전체 토론 압축]
{clip(all_debate_digest, 16000)}

조건 위반, 자기모순, 회피성 문장을 감사하라.
"""
        try:
            auditor = run_role(
                role="Auditor",
                requested_engine=role_engine["Auditor"],
                engines=engines,
                state=state,
                system=AUDITOR_SYSTEM,
                prompt=auditor_prompt,
                max_tokens=auditor_tokens,
                continuations=continuations,
            )
        except Exception as exc:
            auditor = error_result(exc)

        judge_prompt = f"""
{user_packet}

[공용 조사 상태]
{evidence_label}: {', '.join(research_validation.get('reasons') or ['검증 통과'])}

[Evidence Verifier]
{clip(get_text(verifier), 3500)}

[전체 토론 압축]
{clip(all_debate_digest, 19000)}

[Consistency Auditor]
{clip(get_text(auditor), 4200)}

위 자료를 종합해 간결한 최종 판결을 작성하라.
"""
        try:
            verdict_result = run_role(
                role="Judge",
                requested_engine=role_engine["Judge"],
                engines=engines,
                state=state,
                system=JUDGE_SYSTEM,
                prompt=judge_prompt,
                max_tokens=judge_tokens,
                continuations=continuations,
            )
        except Exception as exc:
            verdict_result = error_result(exc, "Judge ERROR")

        debate: Dict[str, Any] = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "topic": topic,
            "target": target,
            "constraints": constraints,
            "goal": goal,
            "context": context,
            "memory_used": [
                {"id": row[0], "created_at": row[1], "topic": row[2], "verdict": row[3]}
                for row in memories
            ],
            "research": {"text": research_text, "validation": research_validation},
            "settings": {
                "cross_rounds": cross_rounds,
                "tokens": {
                    "general": general_tokens,
                    "verifier": verifier_tokens,
                    "auditor": auditor_tokens,
                    "judge": judge_tokens,
                    "followup": followup_tokens,
                },
                "role_engine": role_engine,
                "models": models,
                "budgets": {key: int(value) for key, value in budgets.items()},
            },
            "initial": initial,
            "verifier": verifier,
            "rounds": rounds,
            "auditor": auditor,
            "verdict_result": verdict_result,
            "verdict": get_text(verdict_result),
            "diagnostics": state.diagnostics,
            "followups": [],
        }

        debate_id = MEMORY.save_debate(
            version=VERSION,
            topic=topic,
            target=target,
            constraints_text=constraints,
            goal=goal,
            context=context,
            research=research_text,
            verdict=debate["verdict"],
            transcript=debate,
        )
        debate["debate_id"] = debate_id
        st.session_state.current_debate = debate
        st.success(f"Debate #{debate_id} 저장 완료")

if st.session_state.current_debate:
    render_debate(st.session_state.current_debate)

    st.divider()
    st.header("💬 토론 종료 후 특정 모델에게 질문")
    current = st.session_state.current_debate
    target_options = ["Judge", "Analyst", "Contrarian", "Alternative", "Red Team", "Verifier", "Auditor"]
    with st.form("followup_form", clear_on_submit=True):
        follow_target = st.selectbox("질문 대상", target_options)
        question_type = st.radio(
            "질문 유형",
            ["추가 질문", "사실 정정", "반론 제기", "판결 재검토", "근거 요구"],
            horizontal=True,
        )
        question = st.text_area("질문 / 정정 내용", height=110)
        follow_submit = st.form_submit_button("질문하기", type="primary")

    if follow_submit:
        if not question.strip():
            st.warning("질문을 입력하세요.")
        else:
            target_result = result_for_target(current, follow_target)
            requested = target_result.get("actual_engine")
            if requested not in engines:
                requested = role_engine.get(follow_target, role_engine["Judge"])
            follow_state = ProviderState(
                budgets={key: max(2, int(value)) for key, value in budgets.items()}
            )
            system = FOLLOWUP_SYSTEM_TEMPLATE.format(
                target=follow_target,
                question_type=question_type,
                constitution=REASONING_CONSTITUTION,
            )
            prompt = f"""
[토론 주제]
{current.get('topic', '')}

[USER-CONSTRAINT]
{current.get('constraints', '')}

[대상 역할의 마지막 답변]
{clip(get_text(target_result), 7000)}

[최종 판결]
{clip(current.get('verdict', ''), 5000)}

[Consistency Audit]
{clip(get_text(current.get('auditor') or {}), 2800)}

[사용자 질문]
{question}
"""
            try:
                result = call_with_fallback(
                    requested,
                    FALLBACKS.get(follow_target, FALLBACKS["Judge"]),
                    engines,
                    system,
                    prompt,
                    followup_tokens,
                    continuations,
                    follow_state,
                )
                item = {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "target_role": follow_target,
                    "question_type": question_type,
                    "question": question,
                    "answer": result.text,
                    "engine": result.actual_engine,
                    "model": result.model,
                    "diagnostic": result.to_dict(),
                }
                current.setdefault("followups", []).append(item)
                st.session_state.current_debate = current
                if current.get("debate_id"):
                    MEMORY.add_followup(
                        debate_id=int(current["debate_id"]),
                        target_role=follow_target,
                        question_type=question_type,
                        question=question,
                        answer=result.text,
                        engine=result.actual_engine,
                        model=result.model,
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"후속 질문 실패: {exc}")

    followups = current.get("followups") or []
    if followups:
        st.subheader("후속 대화 기록")
        for index, item in enumerate(followups, start=1):
            with st.expander(
                f"#{index} {item.get('target_role')} · {item.get('question_type')}",
                expanded=index == len(followups),
            ):
                st.markdown(f"**질문**\n\n{item.get('question', '')}")
                st.markdown(f"**답변**\n\n{item.get('answer', '')}")
                st.caption(f"{item.get('engine', '?')} · {item.get('model', '?')}")

st.divider()
st.header("🗃️ 과거 Debate 검색")
search_query = st.text_input("검색어", key="memory_search")
if search_query:
    rows = MEMORY.recall(search_query, 20)
    if not rows:
        st.info("검색 결과가 없습니다.")
    for row in rows:
        with st.expander(f"#{row[0]} · {row[2]}"):
            st.caption(row[1])
            st.markdown(clip(row[3], 1500))
            if st.button("이 토론 불러오기", key=f"load_debate_{row[0]}"):
                loaded = MEMORY.get_debate(row[0])
                if loaded:
                    st.session_state.current_debate = loaded
                    st.rerun()
