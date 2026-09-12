# shorts_maker

긴 영상에서 **단독으로 성립하는 숏폼 클립**을 뽑아내는 파이프라인. **독립 서비스다.**

원래는 SWYP 관리자 백오피스(admin-web)의 베타 탭이었고 backend(Spring)가 프록시했다.
2026-09-03 에 분리했다 — 그 흔적(프록시 전제, `adminId`, `/admin/shorts/**` 경로)이
남아 있으면 잘못된 것이다.

```
web/       React + Vite. 빌드 결과를 backend 가 같은 오리진에서 서빙한다
backend/   FastAPI + CLI. 파이프라인 본체
  src/shorts_maker/
    http/        FastAPI 앱·라우터·인증 (server · deps · studio · auth · debug_page). 도메인 로직 없음
    pipeline/    ingest → stt → segmentation → ranking → cutting → render (+ media · subtitles · orchestrate)
                 🔴 긴 영상은 **청크 여러 개**로 나뉜다 — 메모리 상한 때문이다(아래 Stack). 화면·CLI 는
                 소스 단위로만 말한다(`add_chunks` · `stt.run_for_source` · `segmentation.run_for_source`)
    answers/     시청자 질문 → 답 클립. 제품명 TEASE 는 코드에 안 쓴다 — 여기가 그 기능이다
                 viewers(익명 쿠키·레이트리밋) · events(퍼널). M3 부터 embeddings · clusters · judge
    adapters/    프로세스 밖과 말하는 것만: ffmpeg · gemini · ytdlp
    db/          store(풀·마이그레이션 적용) · migrations/NNN_*.sql
    cli/         `sm`
    config · jobs · doctor · pricing   (횡단 관심사)
  tests/         src 구조를 그대로 미러링(http/ pipeline/ adapters/ db/). support.py 가 테스트 DB 를 준다
```
층 사이 의존 방향은 한쪽이다: http → pipeline/answers → adapters·db. 거꾸로 import 하면 틀린 것이다.

**세션을 시작하면 `docs/handoff.md` 부터 읽는다** — 지금 상태 · 다음 할 일 · 함정.

설계 문서는 둘이다:
- `docs/tease.md` — **제품(TEASE)·아키텍처·스키마 v9.** 시청자 질문 → 숏폼 → 원본 유입.
  기획과 기술을 한 문서에 담았고, 미결 사항은 §13. **새 기능은 여기서 시작한다.**
- `docs/make_shorts.md` — 파이프라인 내부(STT·분할·rank·cut·render), 비용 기준선, 코딩 규약.
- `docs/update_plan.md` — **지나온 기록.** 마일스톤 M0~M9, 완료 조건, 실측, 결정 로그, 불변식.
  "왜 이렇게 됐나" 는 여기 있다.
- `docs/upgrade_plan.md` — **앞으로 할 일.** 남은 작업을 한 곳에 모으고 우선순위를 매겼다.
  제출 전 / 제출 후로 갈라 둔다. **새 작업은 여기서 고른다.**
- `docs/기획서.md` — PM 이 준 기획서(CLIPQ). `docs/기획서_리뷰.md` 가 구현과의 차이를 정리한다.
  🔴 기획서와 코드가 다른 곳이 있다 — 가장 큰 것은 **질문을 라이브 채팅이 아니라 우리 페이지에서
  받는다**는 점이다.
두 문서가 충돌하면 tease.md 가 최신이다. **전제가 바뀌면 문서를 먼저 고친다.**

## 지금 단계

**1단계 = 강연 영상**(`content_type = LECTURE`). 2단계가 영화(`FILM`)다.
강연은 컷이 없고 화면에 정보가 거의 없어서 영상 이해 단계가 통째로 빠진다 — 그래서 싸고 빠르다.
`[2] 샷 검출`·`[4] 영상 해설`은 FILM 에서만 쓴다. 지금 비워둔 자리를 채우는 게 2단계다.

## Stack

- Python 3.12 (`.python-version` 고정) · uv · pyproject
- 실행: **로컬도 `docker compose up`** 이 기본이다(2026-09-04 부터). 운영과 같은 이미지·경로·폰트.
  서비스 둘 — `db`(Postgres 17, `shorts-pg` 볼륨, 127.0.0.1:5432) 와 `shorts`(앱, 산출물은 `shorts-data`
  볼륨). 원본은 `backend/sources/` 바인드 마운트. CLI 는 `docker compose exec shorts sm …`.
  호스트 `uv run sm …`·테스트는 그 `db` 컨테이너에 붙는다(`SHORTS_DB_HOST=127.0.0.1`)
- 웹: **FastAPI** (`sm serve`). 빌드된 프론트(`web/dist`)를 같은 오리진에서 서빙한다 —
  그래서 CORS 설정이 없고 세션 쿠키가 그냥 실린다
- 프론트: **React + Vite + TypeScript**, 라우터 없음. 화면이 로그인과 파이프라인 둘뿐이고
  전환은 URL 이 아니라 인증 상태가 결정한다
- 인증: **비밀번호 1개 + 서명된 httpOnly 세션 쿠키**(`http/auth.py`, stdlib hmac). 회원가입은 없다 —
  운영자 1명이 쓰는 도구다. 사용자 개념을 넣으면 전 테이블에 소유자 스코프와 잡 큐 분리가
  따라온다. 🔴 루프백이 아닌 주소에 바인딩하려면 `SHORTS_ADMIN_PASSWORD` 가 있어야 기동된다
- CLI 는 **argparse**(stdlib). typer/click 은 쓰지 않는다 — 런타임 동작이 같다
- LLM: Gemini (`google-genai`). Files API 는 FILM 단계에서 필요해진다
- 영상: **ffmpeg/ffprobe 바이너리 + subprocess**. moviepy 계열 금지 — 느리고 옵션을 다 못 쓴다
- 자막: ASS + libass. 🔴 Homebrew 기본 ffmpeg 엔 libass 가 없다 — `SHORTS_FFMPEG` 로
  libass 포함 빌드를 지정한다(`brew install ffmpeg-full`). `sm doctor` 가 확인한다
- STT: faster-whisper (`uv sync --extra stt`). 기본 설치에서 빠져 있다.
  🔴 **메모리가 전사 길이에 비례한다.** 실측(2026-09-06): 30분 = 1.4GB, 95분을 한 번에 돌리자 컨테이너
  한도 2.93GB 를 넘겨 OOM(exit 137). 그래서 `SHORTS_CHUNK_MAX_SEC`(기본 25분) 로 잘라 청크마다 전사한다 —
  최대 사용량이 영상 길이가 아니라 청크 길이에 묶인다. **한도를 올리는 건 답이 아니다**: 운영은 4GB VM 이다.
  청크 경계는 무음 지점으로 당긴다(`ingest.plan_chunks`) — 고정 길이로 자르면 문장 한복판에서 끊긴다.
  🔴 나누는 규칙은 둘이다: ① 상한을 넘지 않는 **최소 개수**로 ② **균등 분할**. 95분/30분 상한이면
  30·30·30·5 가 아니라 24×4 다 — 마지막만 짧으면 그 조각만 빨리 끝나 진행률이 거짓말을 한다.
  사용자가 정하는 건 **범위**(어디부터 어디까지 분석할까)뿐이고, 조각 수는 코드가 정한다
- 🔴 같은 원본에 긴 작업(분할·전사·구간 분할)은 **한 번에 하나만** 돈다 — `store.source_lock`
  (Postgres 어드바이저리 락). 전사 중에 재분할이 들어오면 청크가 사라져 그 전사가 외래키 위반으로
  죽는다(2026-09-06에 당했다). API 는 잡 큐가 워커 하나라 안전하지만 **CLI 가 그 큐를 우회**해서 DB 에 건다.
  화면은 `GET /api/sources/{id}` 의 `busy` 로 버튼을 미리 잠근다
- 🔴 **구간 번호(`segments.idx`)는 소스 안에서 연속이다.** rank 프롬프트가 `[번호]` 로 지목하는데 청크마다
  0부터 다시 시작하면 같은 번호가 둘이 된다. `segmentation.run_for_source` 가 이어 붙인다 —
  청크 하나만 다시 나누는 경로는 두지 않는다
- DB: **Postgres 17** (2026-09-06, SQLite 에서 전환 — 이유는 `db/store.py` 머리 주석). psycopg 3 + 풀,
  **SQL 은 직접 쓴다. ORM 없음.** 마이그레이션은 `db/migrations/NNN_*.sql` 번호 순, 파일 하나가 트랜잭션
  하나. 🔴 적용된 파일은 고치지 않고 새 번호로 덧붙인다. 스키마 제약은 `tests/db/test_schema.py` 가 지킨다.
  행은 dict(`row["col"]`), 자리표시자는 `%s`, JSON 컬럼은 jsonb(`Jsonb(obj)` 로 쓰고 dict 로 읽는다),
  참/거짓은 boolean. `store.connect(url)` 은 컨텍스트 매니저 — 정상 종료 commit, 예외 rollback.
  🔴 몇 분 도는 계산(STT·렌더·지문) 앞에서는 `conn.commit()` 으로 트랜잭션을 끊는다
- 테스트는 **진짜 Postgres** 에 돈다(`shorts_test` DB, 테스트마다 truncate). `db` 컨테이너가 없으면 DB
  테스트는 skip 되고 요약에 `skipped=N` 으로 보인다 — 0 이 아니면 테스트가 안 돈 것이다
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
- `/api/**` 는 전부 `http/studio.py` 의 라우터에 등록한다. 인증은 그 라우터가 **라우터 레벨**로 건다
  (`http/deps.py::require_auth`) — 엔드포인트마다 `Depends` 를 붙이지 않고, 그래서 빠뜨릴 수 없다.
  `http/server.py` 에 `/api/...` 를 직접 등록하면 인증이 빠진다. `tests/http/test_api_auth.py` 가 라우트
  테이블을 순회해 이 경계를 지킨다(모든 라우트×메서드 401 · 앱의 `/api/**` 는 전부 스튜디오 라우터 소속)
- 시청자용 공개 API 는 `http/watch.py` 라우터다. **인증이 없다 — 인터넷에 그대로 열린다.** 규칙 셋:
  ① 읽기는 발행된 것만(`sources.published` · `clips.published_at`), 미발행은 403 이 아니라 **404**
  ② 🔴 **외부 API 를 부르지 않는다** — 질문 등록은 insert 만이고 묶기는 크리에이터의 [집계]가 한다
  ③ 쓰기는 레이트리밋(`answers/viewers.check_rate`). `tests/http/test_watch.py` 가 셋 다 지킨다
- **시청자에게 로그인은 없다.** 익명 쿠키(`sm_viewer`, UUID)가 전부다. email 로그인을 검토했다가
  물렀다(2026-09-06) — 검증하지 않는 email 은 쿠키보다 위조가 쉬워 중복 방지가 오히려 약해지고,
  첫 관문의 마찰이 이 제품의 핵심 지표(질문 유입)를 직접 깎는다. 알림이 필요해지면 그때 **질문을 남긴
  뒤** 선택적으로 받는다
- `cfg`·`queue` 는 `http/deps.py` 에 있다. **`deps.cfg` 로 속성 접근** — `from .deps import cfg` 로 값을
  복사하면 테스트의 교체가 반영되지 않는다
- 비밀번호 비교는 `hmac.compare_digest`. `==` 는 일치 접두사 길이만큼 시간이 달라진다
- 프론트 catch-all 라우트는 **반드시 API 라우트 뒤에** 등록한다. 앞에 두면 `/api/**` 를 전부 삼킨다

## Workflow

- 커밋 / push / PR 은 **사용자가 명시적으로 요청할 때만** 한다. 자동으로 하지 않는다.
  커밋은 사용자가 VS Code "커밋 플랜" 패널에서 한다 — `/commit-plan` 으로 `.claude/commit-plan.json` 을 쓴다
- **코드를 먼저 끝까지 쓰고, 전체 테스트는 사용자 허락을 받고 돌린다.** 단계마다 스위트를 돌리지 않는다
- 단계마다 **결과를 눈으로 확인**하고 다음으로 간다. 컴파일/실행 성공 ≠ 완료
- 가장 단순한 동작을 먼저. 최적화는 측정 후 별도로
- 라이브러리 채택은 **런타임 동작 근거로만** 정당화한다 — "코드가 줄어듦"은 근거가 아니다

## Language

- 코드 / 커밋 메시지: 영어. 주석 / 문서 / 대화: 한국어
