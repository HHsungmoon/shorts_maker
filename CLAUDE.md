# shorts_maker

긴 영상에서 **단독으로 성립하는 숏폼 클립**을 뽑아내는 파이프라인.
SWYP 관리자 백오피스의 베타 기능으로 붙지만 **backend 레포와 별개의 서비스**다.

설계 문서(전제·파이프라인·데이터 모델·미해결 과제)는 `SWYP-APP-S6/backend/docs/make_shorts.md`.
**전제가 바뀌면 그 문서의 §1 부터 고친다.**

## 지금 단계

**1단계 = 강연 영상**(`content_type = LECTURE`). 2단계가 영화(`FILM`)다.
강연은 컷이 없고 화면에 정보가 거의 없어서 영상 이해 단계가 통째로 빠진다 — 그래서 싸고 빠르다.
`[2] 샷 검출`·`[4] 영상 해설`은 FILM 에서만 쓴다. 지금 비워둔 자리를 채우는 게 2단계다.

## Stack

- Python 3.12 (`.python-version` 고정) · uv · pyproject
- 웹: **FastAPI** (`sm serve`). 관리자 인증은 하지 않는다 — **backend(Spring)가 프록시**하며
  이미 인증했다. 🔴 루프백이 아닌 주소에 바인딩하려면 `SHORTS_API_TOKEN` 이 있어야 기동된다
- CLI 는 **argparse**(stdlib). typer/click 은 쓰지 않는다 — 런타임 동작이 같다
- LLM: Gemini (`google-genai`). Files API 는 FILM 단계에서 필요해진다
- 영상: **ffmpeg/ffprobe 바이너리 + subprocess**. moviepy 계열 금지 — 느리고 옵션을 다 못 쓴다
- 자막: ASS + libass. 🔴 Homebrew 기본 ffmpeg 엔 libass 가 없다 — `SHORTS_FFMPEG` 로
  libass 포함 빌드를 지정한다(`brew install ffmpeg-full`). `sm doctor` 가 확인한다
- STT: faster-whisper (`uv sync --extra stt`). 기본 설치에서 빠져 있다
- DB: SQLite → Postgres (C5). A 단계에서는 마이그레이션 도구 없이 통째로 날린다
  (`sm db reset --yes`). 스키마 제약은 `tests/test_schema.py` 가 지킨다 — `uv run python -m unittest discover -s tests -t .`

## 규약

- **주석을 적극적으로 남긴다.** 특히 **실측 근거** — 왜 이 숫자인지, 어떤 판단이 있었는지.
  `🔴` 치명적 주의사항 / `TODO:` 향후 개선 여지.
  (backend 레포는 Java 주석 금지지만 **여기는 반대다**. 문서 §12 를 따른다.)
- **초 단위 정밀도가 필요한 값은 파일명 또는 자막/발화 타임스탬프에서.** LLM 이 말한 초를 그대로 쓰지 않는다
- **LLM 출력은 항상 검증한다.** 세그먼트 ID 화이트리스트, 점수 범위, 파싱 실패 시 재시도
- **모델에게 "하지 마라"고 지시하는 대신 입력에서 뺀다**
- **모든 단계는 `stage_calls` 에 기록한다.** 예외 없이 — LLM 호출뿐 아니라 stt/render 도.
  이 파이프라인의 실제 제약은 비용이 아니라 시간이다(문서 §7)
- **잡은 워커 1개.** 동시 1건은 정책이 아니라 구조다 — 영상 처리와 STT 가 겹치면 API 가 죽는다
- **중립/주관을 섞지 않는다.** Segment(사실)는 캐시해 재사용하고 Run(기준)만 갈아끼운다.
  세그먼트 해설/요약에 특정 기준을 넣는 순간 그건 중립 자산이 아니다

## Workflow

- 커밋 / push / PR 은 **사용자가 명시적으로 요청할 때만** 한다. 자동으로 하지 않는다
- 단계마다 **결과를 눈으로 확인**하고 다음으로 간다. 컴파일/실행 성공 ≠ 완료
- 가장 단순한 동작을 먼저. 최적화는 측정 후 별도로
- 라이브러리 채택은 **런타임 동작 근거로만** 정당화한다 — "코드가 줄어듦"은 근거가 아니다

## Language

- 코드 / 커밋 메시지: 영어. 주석 / 문서 / 대화: 한국어
