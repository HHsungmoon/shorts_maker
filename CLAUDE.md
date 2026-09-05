# shorts_maker

긴 영상에서 **단독으로 성립하는 숏폼 클립**을 뽑아내는 파이프라인. **독립 서비스다.**

원래는 SWYP 관리자 백오피스(admin-web)의 베타 탭이었고 backend(Spring)가 프록시했다.
2026-09-03 에 분리했다 — 그 흔적(프록시 전제, `adminId`, `/admin/shorts/**` 경로)이
남아 있으면 잘못된 것이다.

```
web/       React + Vite. 빌드 결과를 backend 가 같은 오리진에서 서빙한다
backend/   FastAPI + CLI. 파이프라인 본체
```

**세션을 시작하면 `docs/handoff.md` 부터 읽는다** — 지금 상태 · 다음 할 일 · 함정.

설계 문서는 둘이다:
- `docs/tease.md` — **제품(TEASE)·아키텍처·스키마 v9.** 시청자 질문 → 숏폼 → 원본 유입.
  기획과 기술을 한 문서에 담았고, 미결 사항은 §13. **새 기능은 여기서 시작한다.**
- `docs/make_shorts.md` — 파이프라인 내부(STT·분할·rank·cut·render), 비용 기준선, 코딩 규약.
- `docs/update_plan.md` — **실행 계획.** 마일스톤 M0~M9, 완료 조건, 클러스터 상태 전이표, 불변식.
  구현 작업은 여기서 현재 마일스톤을 확인하고 시작한다.
두 문서가 충돌하면 tease.md 가 최신이다. **전제가 바뀌면 문서를 먼저 고친다.**

## 지금 단계

**1단계 = 강연 영상**(`content_type = LECTURE`). 2단계가 영화(`FILM`)다.
강연은 컷이 없고 화면에 정보가 거의 없어서 영상 이해 단계가 통째로 빠진다 — 그래서 싸고 빠르다.
`[2] 샷 검출`·`[4] 영상 해설`은 FILM 에서만 쓴다. 지금 비워둔 자리를 채우는 게 2단계다.

## Stack

- Python 3.12 (`.python-version` 고정) · uv · pyproject
- 실행: **로컬도 `docker compose up`** 이 기본이다(2026-09-04 부터). 운영과 같은 이미지·경로·폰트.
  DB 는 `shorts-data` 볼륨의 SQLite, 원본은 `backend/sources/` 바인드 마운트. CLI 는
  `docker compose exec shorts sm …`. 호스트 `uv run sm serve` 는 테스트·디버깅용으로 남아 있다
- 웹: **FastAPI** (`sm serve`). 빌드된 프론트(`web/dist`)를 같은 오리진에서 서빙한다 —
  그래서 CORS 설정이 없고 세션 쿠키가 그냥 실린다
- 프론트: **React + Vite + TypeScript**, 라우터 없음. 화면이 로그인과 파이프라인 둘뿐이고
  전환은 URL 이 아니라 인증 상태가 결정한다
- 인증: **비밀번호 1개 + 서명된 httpOnly 세션 쿠키**(`auth.py`, stdlib hmac). 회원가입은 없다 —
  운영자 1명이 쓰는 도구다. 사용자 개념을 넣으면 전 테이블에 소유자 스코프와 잡 큐 분리가
  따라온다. 🔴 루프백이 아닌 주소에 바인딩하려면 `SHORTS_ADMIN_PASSWORD` 가 있어야 기동된다
- CLI 는 **argparse**(stdlib). typer/click 은 쓰지 않는다 — 런타임 동작이 같다
- LLM: Gemini (`google-genai`). Files API 는 FILM 단계에서 필요해진다
- 영상: **ffmpeg/ffprobe 바이너리 + subprocess**. moviepy 계열 금지 — 느리고 옵션을 다 못 쓴다
- 자막: ASS + libass. 🔴 Homebrew 기본 ffmpeg 엔 libass 가 없다 — `SHORTS_FFMPEG` 로
  libass 포함 빌드를 지정한다(`brew install ffmpeg-full`). `sm doctor` 가 확인한다
- STT: faster-whisper (`uv sync --extra stt`). 기본 설치에서 빠져 있다
- DB: SQLite → Postgres (C5). 마이그레이션 도구는 안 쓰지만 **통째로 날리는 건 이제 최후수단**이다 —
  STT 한 번에 수 분이 든다. 덧붙이기와 평범한 컬럼 삭제는 `store.MIGRATIONS` 로 제자리 처리한다.
  스키마 제약은 `tests/test_schema.py` 가 지킨다
- 🔴 DB 에 들어가는 파일 경로는 **상대경로**다 — 원본은 `source_dir`, 파생물은 `work_dir` 기준
  (`Config.store_source/store_work`, 읽기는 `source_file/work_file`). 절대경로를 넣었다가 레포를
  옮기자 전 행이 깨졌다. `Path(row["path"])` 를 직접 쓰면 틀린 것이다

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

## 인증 경계 (여기서 틀리면 조용히 뚫린다)

- `/health` 만 무인증이다. **설정값을 담지 않는다** — 도커 네트워크 뒤가 아니라 인터넷에 열려 있다
- 나머지 `/api/**` 는 전부 `Depends(require_auth)`. 새 엔드포인트를 추가하면서 빠뜨리기 쉽다 —
  `tests/test_api_auth.py` 가 이 경계를 지킨다
- 비밀번호 비교는 `hmac.compare_digest`. `==` 는 일치 접두사 길이만큼 시간이 달라진다
- 프론트 catch-all 라우트는 **반드시 API 라우트 뒤에** 등록한다. 앞에 두면 `/api/**` 를 전부 삼킨다

## Workflow

- 커밋 / push / PR 은 **사용자가 명시적으로 요청할 때만** 한다. 자동으로 하지 않는다
- 단계마다 **결과를 눈으로 확인**하고 다음으로 간다. 컴파일/실행 성공 ≠ 완료
- 가장 단순한 동작을 먼저. 최적화는 측정 후 별도로
- 라이브러리 채택은 **런타임 동작 근거로만** 정당화한다 — "코드가 줄어듦"은 근거가 아니다

## Language

- 코드 / 커밋 메시지: 영어. 주석 / 문서 / 대화: 한국어
