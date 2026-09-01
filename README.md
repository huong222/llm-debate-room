# LLM Debate Room — proto-1.1234571

여러 LLM이 같은 질문을 독립 분석한 뒤, 증거 검증 → 반박 → 자기모순 감사 → 최종 판결까지 수행하는 로컬 Streamlit 앱입니다.

이번 버전은 **실제로 준비한 API만 사용**하도록 다시 정리했습니다.

## 사용하는 API는 딱 4개

- Google Gemini
- Groq
- Cerebras
- NVIDIA NIM

NVIDIA NIM API 키 하나로 Nemotron Super와 Nemotron Ultra 두 모델을 사용합니다.

OpenRouter, Cohere, Mistral, Cloudflare Workers AI는 이 버전에서 전부 제외했습니다.

## 기본 역할

| 역할 | 기본 엔진 |
|---|---|
| Analyst | Gemini |
| Contrarian | Groq GPT-OSS |
| Alternative | Cerebras GPT-OSS |
| Red Team | NVIDIA Nemotron Super |
| Evidence Verifier | NVIDIA Nemotron Super |
| Consistency Auditor | Cerebras GPT-OSS |
| Judge | NVIDIA Nemotron Ultra |

자세한 모델 배치는 `MODEL_MAP.md`에 있습니다.

## 실행

처음 한 번:

```text
00_RUN_FIRST.cmd
```

또는 기존 이름:

```text
RUN_FIRST.cmd
```

그 다음부터:

```text
START_APP.cmd
```

## API 키

앱 왼쪽에는 아래 4칸만 나옵니다.

```text
Gemini
Groq
Cerebras
NVIDIA NIM
```

`.env`를 쓰고 싶다면 `.env.example`을 복사해 만들면 됩니다.

```dotenv
GEMINI_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=
NVIDIA_API_KEY=
```

실제 API 키가 들어간 `.env`는 GitHub에 올리지 마세요.

## 토론 흐름

```text
질문
 ↓
독립 분석 4개
 ↓
Groq 공용 웹조사 + Evidence Verifier
 ↓
상호 반박
 ↓
Consistency Auditor
 ↓
NVIDIA Nemotron Ultra Judge
 ↓
SQLite 저장
 ↓
Judge 또는 특정 Agent에게 후속 질문
```

## 핵심 추론 규칙

- `자료가 없다 = 반대가 참` 식 오류 금지
- 정확한 제품·모델명이 있으면 제품군 일반론으로 회피하지 않음
- 사용자가 고정한 조건을 임의 변경하지 않음
- 필수 구성품 제거 후 다시 필요하다고 주장하는 자기모순 검사
- 현실 잡음이 있다는 이유만으로 상대적 성능 차이를 무효화하지 않음
- Judge가 문제 정의와 사용자 질문을 장황하게 반복하지 않음

규칙 전문은 `prompts.py`에 있습니다.

## 데이터

토론과 후속 질문은 기본적으로 `data/memory.sqlite3`에 저장됩니다. 이 DB는 `.gitignore` 대상입니다.

프로젝트 폴더에 실수로 들어온 `*.hwp`, `*.exe`, `*.zip`도 Git에 올라가지 않도록 `.gitignore`에서 차단합니다.

## 주의

이 프로젝트는 연구·토론 도구입니다. 여러 모델이 동의한다고 사실이 되는 것은 아니며, 특히 투자·의료·법률·안전 관련 결론은 별도로 검증해야 합니다.
