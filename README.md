# LLM Debate Room

Gemini, Groq, Mistral, Cloudflare Workers AI가 서로 다른 역할로 독립 분석하고 반박한 뒤 최종 판결문을 만드는 로컬 Streamlit 앱입니다.

## 기본 역할

- Analyst: Gemini
- Contrarian: Groq
- Alternative: Mistral
- Verifier: Cloudflare Workers AI
- Judge: Cloudflare Workers AI

역할별 제공자는 화면에서 자유롭게 바꿀 수 있습니다. 429, 413, 빈 응답, 일시 장애가 발생하면 다른 제공자로 자동 대체합니다.

## 주요 기능

- Gemini + Groq + Mistral + Cloudflare Workers AI 4개 독립 할당량 사용
- Groq Compound Mini 공용 웹조사 1회
- FACT / INFERENCE / HYPOTHESIS / UNKNOWN 구분
- 0~3회 반박 라운드
- 긴 토론 컨텍스트 압축
- 출력 길이 제한 감지 및 자동 이어쓰기
- 빈 최종 답변 감지 후 재시도
- 실제 사용 모델, 종료 사유, 사용량, fallback 기록 표시
- SQLite Debate Memory
- 토론 종료 후 Judge 또는 특정 Agent에게 추가 질문·사실 정정·판결 재검토
- 후속 질문도 SQLite에 저장
- API Key를 저장소에 올리지 않는 구조

## 설치 및 실행

Windows에서 저장소를 내려받은 뒤 처음 한 번 `RUN_FIRST.cmd`를 실행합니다.

이후에는 `START_APP.cmd`만 실행하면 됩니다.

직접 실행:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Cloudflare Workers AI 준비

Cloudflare Dashboard에서 Workers AI 페이지로 이동한 뒤 `Use REST API`를 선택합니다.

1. `Create a Workers AI API Token`으로 토큰을 발급합니다.
2. 같은 화면의 `Account ID`를 복사합니다.
3. 앱 사이드바에 두 값을 입력합니다.

기본 Cloudflare 모델은 다음입니다.

```text
@cf/nvidia/nemotron-3-120b-a12b
```

Cloudflare 무료 플랜에는 일일 무료 Neuron 할당량이 있지만 무제한은 아닙니다. 할당량을 넘으면 현재 토론에서 다른 제공자로 자동 대체됩니다.

## 환경변수

앱 입력칸 대신 환경변수를 사용할 수 있습니다.

```powershell
$env:GEMINI_API_KEY="..."
$env:GROQ_API_KEY="..."
$env:MISTRAL_API_KEY="..."
$env:CLOUDFLARE_API_TOKEN="..."
$env:CLOUDFLARE_ACCOUNT_ID="..."
```

`.env`와 실제 API Key는 공개 GitHub 저장소에 커밋하지 마세요.

## 토큰·할당량 설계

- 공용 웹조사는 한 번만 실행하고 모든 Agent가 공유합니다.
- 반박 라운드는 역할별 발언을 압축해서 전달합니다.
- 입력이 너무 크면 자동 축약 후 재시도합니다.
- 출력이 길이 제한으로 끝나면 같은 모델에 이어쓰기를 요청합니다.
- 한 제공자가 죽으면 Cloudflare → Mistral → Groq → Gemini 순서로 fallback합니다.
- 사이드바에서 제공자별 이번 토론 호출 예산을 설정할 수 있습니다. 0은 앱 내부 제한 없음입니다.

## 주의

이 프로그램은 연구·토론 도구입니다. LLM 출력과 웹조사 결과는 별도로 사실 검증해야 합니다. 투자·의료·법률 등 고위험 의사결정을 자동 실행하는 용도로 사용하지 마세요.
