# LLM Debate Room — proto-1.1234569-ready

여러 LLM이 같은 질문을 독립 분석한 뒤, 증거 검증 → 반박 → 자기모순 감사 → 최종 판결까지 수행하는 로컬 Streamlit 앱입니다.

## 제일 먼저 볼 것

- `MODEL_MAP.md` — 어떤 모델이 어떤 역할인지 한눈에 보기
- `START_APP.cmd` — 평소 실행
- `RUN_FIRST.cmd` — 처음 한 번 패키지 설치 + 실행
- `prompts.py` — 추론 헌법과 역할별 규칙
- `app.py` — 화면과 전체 토론 흐름
- `providers.py` — Gemini/Groq/Cerebras/NVIDIA/OpenRouter/Cohere 호출 및 fallback
- `memory_store.py` — SQLite 토론 기록과 후속 질문 저장

## 현재 기본 역할

| 역할 | 기본 엔진 |
|---|---|
| Analyst | Gemini |
| Contrarian | Groq GPT-OSS |
| Alternative | Cerebras GPT-OSS |
| Red Team | OpenRouter |
| Evidence Verifier | Cohere |
| Consistency Auditor | NVIDIA Nemotron Super |
| Judge | NVIDIA Nemotron Ultra |

자세한 모델 ID와 흐름은 `MODEL_MAP.md`를 보세요.

## 실행

처음 한 번:

```text
RUN_FIRST.cmd
```

그 다음부터:

```text
START_APP.cmd
```

`START_APP.cmd`는 자신과 같은 폴더의 `app.py`를 실행하도록 되어 있습니다.

## API 키

앱 왼쪽 사이드바에 직접 넣거나, `.env.example`을 복사해 `.env`를 만들고 다음 키를 넣을 수 있습니다.

```dotenv
GEMINI_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=
NVIDIA_API_KEY=
OPENROUTER_API_KEY=
COHERE_API_KEY=
```

`.env`는 GitHub에 올리지 마세요.

## 핵심 설계

```text
질문
 ↓
독립 분석 4개
 ↓
공용 웹조사 + Evidence Verifier
 ↓
상호 반박
 ↓
Consistency Auditor
 ↓
Judge
 ↓
SQLite 저장
 ↓
Judge 또는 특정 Agent에게 후속 질문
```

추론 규칙은 다음 문제를 막는 데 초점을 둡니다.

- `자료가 없다 = 반대가 참` 식 오류
- 정확한 제품명을 줬는데 제품군 일반론으로 회피
- 사용자가 고정한 조건을 중간에 변경
- 필수 구성품을 제거한 뒤 다시 필요하다고 주장하는 자기모순
- 현실 잡음이 있다는 이유만으로 상대적 성능 차이를 무효화
- Judge가 문제 정의와 사용자 질문을 장황하게 반복

## 현재 공급자

- Google Gemini
- Groq
- Cerebras
- NVIDIA NIM
- OpenRouter
- Cohere

Mistral과 Cloudflare Workers AI는 이 브랜치에서 제거했습니다.

## 데이터

토론과 후속 질문은 기본적으로 `data/memory.sqlite3`에 저장됩니다. 이 DB 파일은 `.gitignore` 대상입니다.

## 주의

이 프로젝트는 연구·토론용입니다. 여러 모델이 동의한다고 사실이 되는 것은 아니며, 특히 투자·의료·법률·안전 관련 결론은 사람이 별도로 검증해야 합니다.
