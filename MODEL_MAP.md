# 모델 배치 한눈에 보기

현재 앱 버전: `프로토-1.1234571`
현재 작업 브랜치: `proto-1.1234569`

## 실제 사용하는 공급자

| 공급자 | API 키 | 사용 모델 |
|---|---|---|
| Gemini | Gemini 키 | `gemini-3.5-flash-lite` |
| Groq | Groq 키 | `openai/gpt-oss-120b` + 웹조사 `groq/compound-mini` |
| Cerebras | Cerebras 키 | `gpt-oss-120b` |
| NVIDIA NIM | NVIDIA 키 1개 | Nemotron Super + Nemotron Ultra |

OpenRouter / Cohere / Mistral / Cloudflare는 사용하지 않습니다.

## 역할 배치

| 역할 | 기본 엔진 | 목적 |
|---|---|---|
| Analyst | Gemini | 기본 가설과 실행 가능한 해법 |
| Contrarian | Groq GPT-OSS | 반례·숨은 전제·과장 공격 |
| Alternative | Cerebras GPT-OSS | 제3의 설명과 누락 변수 |
| Red Team | NVIDIA Nemotron Super | 실패 시나리오와 오판 비용 |
| Evidence Verifier | NVIDIA Nemotron Super | 주장과 자료의 연결 검증 |
| Consistency Auditor | Cerebras GPT-OSS | 조건 위반·자기모순 감사 |
| Judge | NVIDIA Nemotron Ultra | 최종 판결 |

## 모델 ID

```text
Gemini         gemini-3.5-flash-lite
Groq           openai/gpt-oss-120b
Cerebras       gpt-oss-120b
NVIDIA Super   nvidia/nemotron-3-super-120b-a12b
NVIDIA Ultra   nvidia/nemotron-3-ultra-550b-a55b
```

NVIDIA는 API 키를 두 개 쓰는 것이 아니라 **NVIDIA NIM 키 하나로 Super와 Ultra를 모두 호출**합니다.

모델 ID는 공급자 측에서 바뀔 수 있으므로 실행 화면의 `모델 이름`에서 수정할 수 있습니다.

## 토론 순서

```text
질문 입력
  ↓
4개 독립 분석
  ├─ Analyst / Gemini
  ├─ Contrarian / Groq
  ├─ Alternative / Cerebras
  └─ Red Team / NVIDIA Super
  ↓
Groq 공용 웹조사
  ↓
Evidence Verifier / NVIDIA Super
  ↓
상호 반박
  ↓
Consistency Auditor / Cerebras
  ↓
Judge / NVIDIA Ultra
  ↓
SQLite 저장
  ↓
Judge/각 Agent 후속 질문
```

## 추론 규칙 핵심

- 증거 부재를 반증으로 취급하지 않음
- 정확한 모델명이 있으면 제품군 일반론보다 그 대상을 우선
- 사용자가 고정한 조건을 임의로 변경하지 않음
- 현실 변수가 있다는 이유만으로 상대적 성능 차이를 무효화하지 않음
- 자기모순과 가짜 최적화를 Auditor가 검사
- Judge는 다수결이 아니라 근거와 논리로 판정

전체 규칙은 `prompts.py`의 `REASONING_CONSTITUTION`에 있습니다.
