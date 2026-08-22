# LLM Debate Room

Gemini, Groq, Mistral이 서로 다른 역할로 독립 분석하고 반박한 뒤 최종 판결문을 만드는 로컬 Streamlit 앱입니다.

## 기본 역할

- Analyst: Gemini
- Contrarian: Groq
- Alternative: Mistral
- Verifier: Groq
- Judge: Mistral

역할별 제공자는 화면에서 자유롭게 바꿀 수 있습니다. 특정 API가 429/413 등으로 실패하면 다른 제공자로 자동 대체합니다.

## 주요 기능

- Gemini + Groq + Mistral 3개 API 지원
- Groq Compound Mini를 이용한 공용 웹조사 1회
- FACT / INFERENCE / HYPOTHESIS / UNKNOWN 구분
- 0~3회 반박 라운드
- 긴 토론을 압축해 무료/저한도 API의 TPM 초과를 완화
- Gemini 호출 예산 설정
- SQLite 장기 Debate 기록
- 과거 Debate 검색 및 현재 토론에 관련 기억 주입
- 전체 토론 JSON 다운로드
- API Key를 저장소에 넣지 않는 구조

## 설치

Windows에서 저장소를 내려받은 뒤 처음 한 번 `RUN_FIRST.cmd`를 실행하세요.

그 다음부터는 `START_APP.cmd`만 실행하면 됩니다.

직접 실행하려면:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## API Key

앱 왼쪽 입력란에 직접 넣거나 환경변수로 설정합니다.

```powershell
$env:GEMINI_API_KEY="..."
$env:GROQ_API_KEY="..."
$env:MISTRAL_API_KEY="..."
```

`.env`와 실제 API Key는 GitHub에 커밋하지 마세요.

## 기본 모델

- Gemini: `gemini-3.5-flash-lite`
- Groq: `openai/gpt-oss-20b`
- Mistral: `mistral-small-latest`

모델 제공자가 모델을 변경하거나 계정별 사용 가능 모델이 다르면 앱 사이드바에서 모델명을 변경할 수 있습니다.

## 토큰/할당량 설계

공용 웹조사는 한 번만 수행한 뒤 모든 Agent가 공유합니다. 반박 라운드에는 앞선 발언 전체를 무제한으로 재전송하지 않고 역할별 핵심 구간만 전달합니다. 413 오류가 발생하면 입력을 추가 축약하고 한 번 재시도한 뒤 다른 제공자로 넘어갑니다.

## 주의

이 프로그램은 연구·토론 도구입니다. LLM 출력과 웹조사 결과는 사실 검증이 필요하며, 투자·의료·법률 등 고위험 의사결정의 자동 실행 도구로 사용하면 안 됩니다.
