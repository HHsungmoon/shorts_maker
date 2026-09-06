# 인수인계 — shorts_maker

**2026-09-06 기준.** 이 레포를 처음 여는 사람(사람이든 새 Claude 세션이든)이 **10분 안에
"지금 뭐가 있고, 다음에 뭘 하나"를 알게** 하는 문서다. 세부는 다른 문서로 보낸다.

| 알고 싶은 것 | 문서 |
|---|---|
| 지금 상태 · 다음 할 일 · 함정 | **이 문서** |
| 제품(TEASE) · 아키텍처 · 스키마 v9 · 미결 사항 | [`tease.md`](tease.md) |
| **개발 순서 · 마일스톤별 완료 조건 · 상태 관리 방식** | [`update_plan.md`](update_plan.md) |
| 파이프라인 내부 · 비용 기준선 · 코딩 규약 | [`make_shorts.md`](make_shorts.md) |
| 실행 · 테스트 · 배포 명령 | [`../README.md`](../README.md) |
| 작업 규약(커밋 정책, 주석, 언어) | [`../CLAUDE.md`](../CLAUDE.md) — 새 세션이 자동으로 읽는다 |

---

## 1. 한 줄 현황

**SWYP 조직의 3개 레포(backend · admin-web · shorts_maker)에서 shorts_maker 를 완전히 떼어내
독립 서비스로 만들었다.** 자체 프론트(`web/`)와 로그인이 붙었고, 테스트 128개 · 프론트 빌드 ·
실제 DB 마이그레이션(v7→v8)까지 통과했다. **다음 단계는 제품 피벗(TEASE)** — 시청자 질문 기반
숏폼. 설계는 끝났고(`tease.md`) 구현은 시작 전이다.

**2026-09-04 (2차):** 로컬 실행을 **docker compose 로 통일**했다. DB 의 파일 경로가 절대경로여서
레포 이동으로 전 행이 깨진 것을 발견 → **상대경로 저장**으로 바꾸고 DB 는 새로 만들었다(옛 DB 삭제).
v8 잔재로 죽던 `sm rank run` 도 고쳤다. §4-7.

**2026-09-06 (3차): M0 완료.** `update_plan.md` 의 첫 마일스톤. tease §13 의 미결 9개를 권고대로 확정했고,
`/api/**` 를 `studio_api.py` 라우터로 빼 **라우터 레벨 인증**으로 바꿨다(라우트 테이블 순회 테스트가 지킨다).
"다시 추출" 은 청크 **교체**가 됐고, rank 가 `segments.excluded_by` 에 쓰던 것을 없앴으며, 다운로드·등록 잡이
`sources.status` 를 RUNNING→DONE/FAILED 로 남긴다. 화면 제목은 TEASE(`web/src/shared/brand.ts`). numpy 추가.
테스트 151개 · 컨테이너에서 등록→청크(409·교체)→STT→분할→rank→cut→render 전부 확인. §4-8.

**2026-09-06 (4차): 구조 재편 + Postgres 전환.** 평평하던 20개 모듈을 `http/ pipeline/ answers/ adapters/ db/ cli/`
로 갈랐고(§3), SQLite 를 **Postgres 17** 로 바꿨다(compose 의 `db` 서비스, psycopg 3, ORM 없음). 스키마는
`db/migrations/001_baseline.sql` 하나로 시작한다(= 옛 SQLite v9 내용, CHECK 전부 포함). 테스트는 진짜 Postgres
(`shorts_test`)에 돈다. 이유는 §4-9, 결정 번복은 update_plan D5.

**2026-09-06 (5차): M2 — 시청자 면.** 계획 순서를 바꿨다(§4-10). 공개 API(`http/watch.py`, 인증 없음) ·
익명 쿠키와 레이트리밋(`answers/viewers.py`) · 퍼널 이벤트(`answers/events.py`) · 스튜디오 발행 토글 ·
프론트를 `shared/ studio/ watch/` 로 가르고 react-router 도입 · `/watch` 목록과 상세(임베드·질문·좋아요).
테스트 194개 통과, 컨테이너에서 비공개 404 → 발행 → 질문·좋아요·이벤트까지 확인.
**2026-09-06 (6차): M3 — 질문 묶기.** `answers/embeddings.py`(임베딩 저장·코사인) · `answers/clusters.py`
([집계] · 36칸 상태 기계) · 스튜디오 클러스터 패널 · 평가 CLI. 마이그레이션 002 로 `embeddings.task_type` 을
유니크 키에 넣었다. 실제 Gemini 로 θ 를 0.60~0.92 재서 **0.85 확정**(근거는 update_plan M3 표).
사용자가 시청자 화면에 남긴 진짜 질문 1개가 [집계]로 묶이는 것까지 확인. **2026-09-06 (7차): 긴 영상 자동 분할.** M5 검증용 95분 영상이 OOM 으로 죽어서 끼어든 작업이다(§4-11).
청크는 이제 코드가 나눈다 — 사용자는 "구간 추출" 한 번을 누르고, 조각이 4개라는 건 화면에 안 보인다.
**2026-09-06 (8차): M5 [답하기] 파이프라인.** 라우팅 → 검색 → 후보 3개 → judge 병렬 → 승자 컷 → 렌더.
계획의 "실패하면 재시도" 를 **best-of-3** 로 바꿨다(§4-12). 조합 클립은 브릿지 카드를 끼워 한 번의
인코딩으로 이어붙인다 — 합성 영상으로 필터 그래프를 실측했다(7.40초, 한글 정상). 테스트 295개.
**남은 것은 실제 영상 관통** — 전사 → [집계] → [답하기] → [발행] → `/watch`.

---

## 2. 커밋 · 푸시 상태

**전부 커밋되고 `HHsungmoon/shorts_maker` `main` 에 푸시됐다** (2026-09-04, `bc6f1d6` → `bf9e4f4`).
5개 커밋으로 나눴다 — 구조 이동 / 백엔드 독립(인증·스키마 v8) / 프론트 / 빌드·배포 / 문서.
`git log --oneline bc6f1d6..` 로 본다. 워킹트리는 깨끗하다.

앞으로의 커밋은 `CLAUDE.md` 규약대로 — **사용자가 명시적으로 요청할 때만.**

---

## 3. 지금 구조

```
~/dev/shorts_maker/            ← SWYP-APP-S6/ 에서 빠져나옴. 레포 하나, 원격 github.com/HHsungmoon/shorts_maker
├── backend/                   FastAPI + CLI. 파이프라인 본체
│   ├── src/shorts_maker/      (9/6 재편 — 역할별. 의존 방향 http → pipeline/answers → adapters·db)
│   │   ├── http/              server.py(앱 조립·공개 라우트·serve) · deps.py(cfg·queue·require_auth) ·
│   │   │                      studio.py(/api/**, 라우터 레벨 인증) · watch.py(/api/watch/**, 🔴 인증 없음) ·
│   │   │                      auth.py · debug_page.py
│   │   ├── pipeline/          ingest · stt · segmentation · ranking · cutting · render · media · subtitles · orchestrate
│   │   ├── answers/           viewers(익명 쿠키·레이트리밋) · events(퍼널) · embeddings(벡터·코사인) ·
│   │   │                      clusters([집계]·상태 기계). M5 부터 routing · retrieval · judge · answer
│   │   ├── adapters/          ffmpeg · gemini · ytdlp
│   │   ├── db/store.py        🆕 psycopg 풀 · connect() · apply_schema() · reset(). SCHEMA_VERSION = 파일 번호 최댓값
│   │   ├── db/migrations/     001_baseline.sql · 002_embedding_task_type.sql.
│   │   │                      🔴 적용된 파일은 안 고친다, 새 번호로 덧붙인다
│   │   ├── cli/main.py        `sm` 명령
│   │   └── config.py · jobs.py · doctor.py · pricing.py
│   ├── tests/                 src 미러링(http/ pipeline/ adapters/ db/). support.py 가 shorts_test DB 를 준다(없으면 skip)
│   ├── work/                  ⛔ git 제외. 호스트 실행(uv) 전용 작업 폴더. 컨테이너는 볼륨 /data/work 를 쓴다
│   ├── eval/questions.json    🆕 θ 튜닝용 질문 세트. `sm answers eval-cluster` 가 읽는다(이미지에도 들어간다)
│   ├── sources/               ⛔ git 제외. **원본 영상은 여기**(니체 강연 2편, 1.5GB). 컨테이너의 /sources
│   ├── .env                   ⛔ git 제외. GEMINI_API_KEY · SHORTS_ADMIN_PASSWORD · 🔴 POSTGRES_PASSWORD(9/6 부터 필수)
│   ├── .env.example           로컬용 템플릿 (Postgres 항목 추가됨)
│   ├── deploy.env.example     서버용 템플릿
│   └── pyproject.toml         + numpy · psycopg[binary] · psycopg-pool (9/6) · [dependency-groups] dev = httpx
├── web/                       🆕 React + Vite + TS. admin-web 의 숏폼 탭을 옮겨온 것
│   └── src/
│       ├── App.tsx            🆕(9/6) react-router. /watch → 시청자, 나머지 → 스튜디오. 🔴 lazy 로 가른 건
│       │                      번들이 아니라 **인증** 때문이다 — 한 트리면 시청자도 /auth/me 를 부른다
│       ├── shared/            client.ts(fetch 래퍼) · useAsync · brand.ts · styles.css
│       ├── studio/            StudioApp(인증 게이트) · LoginPage · ShortsPage · api.ts · types.ts · components/
│       └── watch/             🆕 WatchApp · HomePage(목록) · SourcePage(임베드·질문·좋아요) · api.ts · watch.css
├── docs/
│   ├── handoff.md             이 문서
│   ├── tease.md               🆕 제품·아키텍처·스키마 v9 (823줄)
│   ├── update_plan.md         🆕 (9/5) TEASE 실행 계획 — M0~M9, 상태 전이표, 불변식, 결정 D1~D10
│   └── make_shorts.md         backend 레포에서 가져옴. §0·§10·§13 갱신
├── Dockerfile                 🆕 루트로 이동. node 빌드 → python → runtime, 한 이미지
├── compose.yaml               **로컬·운영 공용.** db(postgres:17, 127.0.0.1:5432, shorts-pg 볼륨) + shorts(127.0.0.1:8100)
├── .dockerignore              🆕 work/ 1.6GB 가 빌드 컨텍스트에 안 들어가게
├── deploy/nginx-shorts.conf   🆕 nginx 예시 (range 요청, 2g 업로드)
├── scripts/deploy.sh          SHORTS_ADMIN_PASSWORD 검사로 변경
├── CLAUDE.md                  독립 서비스 기준으로 갱신. 인증 경계 절 추가
└── README.md                  재작성
```

---

## 4. 이번에 한 것과 **이유**

### 4-1. 레포 분리

- `~/dev/SWYP-APP-S6/shorts_maker` → `~/dev/shorts_maker`. **`mv` 로 옮겼다** — `work/`·`.env` 가
  gitignore 라 clone 으로는 안 따라온다.
- 루트에 있던 파이썬을 `backend/` 로, 프론트는 `web/` 로. `git mv` 라 이력 유지.
- `.venv` 는 절대경로가 박혀 있어 지우고 `uv sync --extra stt` 로 재생성.
- `config.py` 의 `REPO_ROOT` → `BACKEND_ROOT`. `parents[2]` 가 이제 `backend/` 를 가리키는데
  `.env`·`work/` 를 거기 뒀으니 동작은 그대로.
- `.gitignore` 재작성 — `sources/*` 가 슬래시 때문에 루트 고정이라 `backend/sources/` 를 못 잡고
  있었다. **원본 영상이 커밋될 뻔한 자리.**
- 설계 문서 `make_shorts.md` 를 backend 레포에서 **복사**해 옴(그쪽 삭제는 안 함, §6).

### 4-2. 인증 — Spring 프록시가 사라져서

원래 Spring 이 REALM_ADMIN 으로 인증하고 이 서비스는 도커 네트워크 안에서만 닿았다. 독립하면
그 전제가 없어지므로 여기서 인증한다.

- `auth.py`: 비밀번호 1개 → HMAC 서명 세션 쿠키(httpOnly · SameSite=lax). 서명 키를 비밀번호에서
  파생해 **비밀번호를 바꾸면 기존 세션이 전부 끊긴다.** 실패 5회 → 60초 잠금(전역 카운터 — 절충,
  주석 참조). 라이브러리 0.
- `require_auth`: 세션 쿠키 **또는** `X-Shorts-Token`(기계 클라이언트용, 선택). 둘 다 미설정이면
  무인증 = 루프백 전용 로컬 모드.
- 🔴 `check_binding`: 비-루프백 바인딩 + 비밀번호 없음 → **기동 거부.** 예전엔 토큰이 그 역할.
- `/health` 를 `{"ok":true}` 로 줄이고 설정값(모델명·스키마)은 `/api/status`(인증)로. 인터넷에
  열리는 순간 그 값들은 공격자에게 먼저 주면 안 된다.
- `test_api_auth.py` 가 이 경계 전체를 지킨다 — 닫힘 기본 · 쿠키 · 토큰 · 위조 · 잠금 · 로컬 모드 · catch-all 격리.

### 4-3. 스키마 v8 — backend 결합 제거

- `sources.created_by_admin_id` · `runs.requested_by_admin_id` · `clip_reviews.admin_id` **삭제.**
  backend 의 `admins.id` 였고 독립 후 참조할 곳이 없다. API 모델 5개의 `adminId` 도 제거.
- SQLite 3.53 이라 `DROP COLUMN` 으로 제자리 처리. **실제 DB(발화 381개)에서 돌려 보존 확인.**
  `store.MIGRATIONS` 규칙을 "평범한 컬럼 삭제까지" 로 넓혔고 `_apply_once` 가 `no such column` 도
  넘긴다(멱등).
- 백업: `backend/work/shorts.db.bak-before-v8`.

### 4-4. 프론트 — admin-web 에서 이식

가져오면서 사라진 것 셋: `ApiResponse<T>` 봉투(Spring 것) · 토큰 회전(세션 쿠키로) · **blob URL
우회**(`<video src>` 가 헤더를 못 붙여 만든 것 — 동일 오리진 쿠키라 불필요. 덤으로 브라우저가
range 요청으로 스트리밍한다). react-router 도 뺐다(당시엔 화면 둘뿐) — **tease.md §8 에서 복귀
예정**(`/watch/:id` 가 필요해짐). CSS 는 죽은 클래스 12개 제거.

### 4-5. 배포 설정

- `Dockerfile` 루트로: node 스테이지가 `web/` 빌드 → runtime 이 `/app/web` 으로 복사 → FastAPI 가
  같은 오리진에서 서빙. CORS 없음.
- `compose.yaml`: `backend_default` 외부 네트워크 의존 제거, `127.0.0.1:8100` 바인딩(nginx 앞),
  `mem_limit` 1400m → **3000m** (4GB VM 단독 사용 전제 — 주석에 명시, 다른 걸 얹으면 재계산).
- `.dockerignore` 신규 — 없으면 `docker build` 가 `work/` 1.6GB 를 데몬으로 보낸다.

### 4-6. 문서

`tease.md` 신규(제품 피벗 통합 설계). `make_shorts.md` §0·§10·§13 을 독립 구조로 갱신.
`CLAUDE.md`·`README.md` 재작성.

### 4-7. (2차) 로컬을 docker 로, DB 경로를 상대경로로 — 그리고 왜

- **레포를 옮기자 DB 의 경로 5행이 전부 깨져 있었다.** `sources.path`·`chunks.path`·`clips.path` 가
  `~/dev/SWYP-APP-S6/...` 절대경로였다. 1차 handoff 는 스키마 마이그레이션만 확인하고 경로는 안 봤다.
  클립 재생 404, 렌더 "원본이 없다", 그리고 삭제 시 파일만 지우고 행이 남는 최악 경로까지 열려 있었다.
- **고침**: `Config.store_source/store_work` 가 `source_dir`/`work_dir` 기준 상대경로를 만들고,
  `source_file/work_file` 이 되찾는다. 절대경로가 들어 있으면 그대로 쓴다(옛 DB 폴백).
  `Path(row["path"])` 를 직접 쓰는 코드는 이제 틀린 것이다. `tests/test_paths.py`.
- **왜 도커로 통일했나**: 호스트(`~/dev`)와 컨테이너(`/sources`, `/data/work`)는 경로가 다르고,
  맥의 ffmpeg 엔 libass 가 없고 폰트도 다르다. 같은 이미지에서 돌리면 그 차이가 전부 사라진다.
  compose 의 `environment` 가 컨테이너 경로를 고정한다 — `.env` 의 호스트용 값이 env_file 로
  새어 들어와 덮는 걸 막기 위해서다. DB(SQLite)는 `shorts-data` 볼륨. **별도 DB 컨테이너는 없다**
  (Postgres 는 여전히 C5).
- 원본 영상은 `backend/work/sources/` → **`backend/sources/`** 로 옮겼다(compose 바인드 마운트 위치).
  `SHORTS_SOURCE_DIR` 기본값도 `sources` 로. 옛 `work/shorts.db` 와 v8 백업은 지웠다 — 사용자 승인.
- **`sm rank run` TypeError**: v8 에서 `requested_by_admin_id` 를 지울 때 CLI 만 인자 5개를 넘기고
  있었다. `tests/test_cli.py` 가 `autospec=True` 로 시그니처를 강제한다.

### 4-8. (3차) M0 — 정리와 결정, 그리고 왜

- **라우터 분리.** 공개 면(`/api/watch/**`, M3)을 붙이기 전에 인증 경계를 구조로 만들었다. 엔드포인트마다
  `Depends(require_auth)` 를 붙이는 방식은 하나 빠뜨려도 아무것도 안 알려준다. 지금은 `studio_api.router` 에
  등록하면 자동으로 걸리고, `api.py` 에 `/api/...` 를 직접 달면 테스트가 잡는다. `cfg`·`queue` 는 `deps.py`
  로 — 라우터 모듈과 앱 모듈이 서로 import 하면 순환이라서.
- **청크 교체.** 예전 "다시 추출" 은 idx 를 올려 옆에 하나 더 만들었다. 화면은 `chunks[0]` 만 보고 파이프라인은
  전 청크를 보니 둘이 다른 것을 봤다. LECTURE 는 청크 1개가 전제라 코드가 그걸 지키게 했다. run 은 chunks 에
  매달려 있지 않아 따로 지운다 — 안 지우면 `runs.ranked` 가 사라진 구간 idx 를 가리킨다.
- **`excluded_by='auto'` 제거.** run(주관)의 판정을 segments(중립)에 영구 기록하던 것. 기준을 바꿔 다시 돌려도
  첫 판정이 남았다. 제외 목록은 `runs.ranked` JSON 에 이미 있다.
- **`sources.status`.** 다운로드는 수 분인데 행이 끝나야 생겨서 그동안 화면엔 아무것도 없었고, 서버가 죽으면
  정리할 대상도 없었다. 이제 `begin_source` 가 `pending:` 지문으로 행을 먼저 만들고 `finish_source` 가 채운다.
  실패는 FAILED+error 로 남고 같은 경로 재시도는 같은 id 를 다시 쓴다.
- 이번엔 **DB 를 유지**했다. 스키마가 안 바뀌어서. (→ 4차에서 Postgres 로 가며 결국 비웠다.)

### 4-9. (4차) 구조 재편과 Postgres — 그리고 왜

- **왜 지금.** 해커톤 뒤 서비스로 갈 것이 확정됐다. SQL 이 코드 113곳·테스트 114곳으로 가장 적은 지금이 전환이
  가장 싼 시점이고, M2~M9 가 지금보다 더 많은 SQL 을 쓴다. 결정적 계기는 M1 이었다 — SQLite 는 CHECK 를 ALTER 로
  못 바꿔 `stage_calls` 재생성·멱등 함수 스텝·DDL 파서까지 짜야 했다. 그 코드를 다 짜고 나서 버렸다(git stash 에
  "M1 sqlite v9" 로 남아 있다). Postgres 에선 그 전부가 `alter table … add constraint` 한 줄이다.
- **서비스가 되면.** 잡 워커를 별도 프로세스로 빼야 하는데 SQLite 단일 쓰기 잠금이 웹 서버와 워커를 서로 막는다.
  Postgres 의 `select … for update skip locked` 가 표준 답이다. 인증인가가 붙으면 전 테이블에 소유자 스코프가
  들어가는데 그것도 ALTER 한 줄. pgvector 도 켤 수 있게 됐지만 N<1000 이라 아직 numpy 다.
- **어떻게.** psycopg 3 + 풀, SQL 은 직접 쓴다(ORM 없음 — 런타임 근거 없음, SQL 이 보이는 게 장점). 마이그레이션은
  번호 붙인 SQL 파일 + `schema_version`. 테스트는 진짜 Postgres(`shorts_test`, 테스트마다 truncate) — SQLite 를
  테스트용으로 남기는 이중 방언은 가장 흔한 함정이라 안 했다. 비밀번호는 `POSTGRES_PASSWORD` 하나를 db 컨테이너와
  앱이 env_file 로 나눠 갖는다.
- **잡은 버그 둘.** ① `debug_page` 모듈명과 함수명이 같아 `/debug` 와 프론트 미빌드 시의 `/` 가 AttributeError
  로 죽었다(이름 변경의 부작용, import 를 별칭으로 바꿔 해결). ② `conn.executemany` 는 psycopg 에 없다 —
  **커서의 메서드**다. sqlite3 에는 연결에도 있어서 그대로 옮겼다가 STT 저장이 통째로 실패했는데, 테스트는
  순수 함수(`to_utterance_rows`)만 봐서 전부 통과했다. 저장 경로를 도는 테스트 3개를 추가했다
  (`tests/pipeline/test_stt.py::RunForChunkTest`).
- **구조.** `tease/` 를 옆에 붙이는 대신 역할별로 갈랐다(§3, update_plan §3). TEASE 는 서비스 이름이라 코드에
  안 쓰고, 그 기능은 `answers/` 다. 이동은 `git mv` 만 하고 로직은 안 건드렸다(별도 커밋). 인증인가 대비로 넣은 건
  없다 — `require_auth` 가 Principal 을 돌려주게 바꾸는 것은 사용자 개념이 생길 때 한다.

### 4-10. (5차) 왜 시청자 면을 임베딩보다 먼저 했나

- **의존이 없었다.** 질문 등록은 insert 만이고 AI 를 안 부른다(D11). 그런데 계획에서는 임베딩(M2) 뒤에
  있었다 — 순서가 틀렸던 것이다.
- **오히려 반대였다.** 묶기 임계값 θ 를 튜닝하려면 실제로 쌓인 질문이 필요한데, 손으로 적은
  `eval/questions.yaml` 은 사람이 쓴 문장과 표현이 다르다. 시청자 면을 먼저 열어야 진짜 데이터가 생긴다.
- **위험이 앞으로 온다.** 무인증 라우터·익명 쿠키·레이트리밋·미발행 404 는 틀리면 조용히 뚫린다.
  일찍 만들어 테스트로 굳히는 편이 낫다.
- **email 로그인은 검토했다가 물렀다.** 검증하지 않는 email 은 쿠키를 지우는 것보다 위조가 쉬워서 좋아요
  중복 방지가 **더 약해진다.** 그리고 첫 관문의 마찰이 이 제품의 핵심 지표(질문 유입, 원본 유입)를 직접
  깎는다. 알림이 필요해지면 질문을 남긴 뒤 선택적으로 받는 게 맞다.
- **프론트 이미지는 가르지 않았다.** 같은 출처에서 서빙하는 것이 인증 설계의 전제고(쿠키가 그냥 실린다),
  가르면 CORS·CSRF 대응이 새로 생긴다. 실측으로 프론트만 고쳤을 때 이미지 재빌드가 2.9초라 아픈 데도 없다.
  CDN 에 올리거나 정적 서빙이 앱을 실제로 방해할 때 가른다.

### 4-11. (7차) 왜 긴 영상을 나누게 됐나

- **증상**: 95분(917MB) 영상을 한 청크로 전사하다 컨테이너가 exit 137 로 죽었다. `docker inspect` 의
  `OOMKilled: true`. 실측하니 30분 전사에 1.4GB 를 쓴다 — 95분이면 4GB 안팎이고 한도는 2.93GB 다.
- **한도를 올리지 않은 이유**: 로컬은 올릴 수 있다(Docker VM 7.7GB, 맥 48GB). 운영이 4GB VM 이라 거기서
  막히고, 더 큰 VM 은 돈이다. 그리고 청크는 **원래 이걸 위해** 설계에 있던 개념이다.
- **내 실수였다**: M0 에서 "LECTURE 는 소스당 청크 1개" 로 못박았는데, 그건 20분 강연에만 맞는 규칙이었다.
- **사용자에게는 하나로 보인다**: 화면·CLI 는 소스 단위로만 말한다. 조각 수는 조용한 각주다.
- **경계는 무음으로**: 고정 길이로 자르면 문장 한복판에서 끊겨 그 발화가 양쪽 청크에서 반토막 난다.
  `silencedetect` 로 목표 ±45초 안의 무음 한가운데를 찾아 거기서 자른다. 실패하면 고정 길이로 떨어진다.
- **동시 실행에 물렸다**: 4조각 전사가 도는 중에 화면에서 "다시 추출" 을 누르자 청크가 삭제됐고,
  마지막 조각의 발화를 넣던 STT 가 외래키 위반으로 죽었다. API 는 잡 큐가 워커 하나라 안전하지만
  **CLI 가 그 큐를 우회**한다. `store.source_lock`(Postgres 어드바이저리 락)으로 원본 단위로 막았다 —
  연결이 끊기면 자동으로 풀려서 프로세스가 죽어도 잔재가 안 남는다. 화면은 `busy` 로 버튼을 잠근다.
- **번호가 걸렸다**: `segments.idx` 가 청크마다 0부터라 여러 청크면 같은 번호가 둘이 된다. rank 프롬프트가
  번호로 구간을 지목하므로 그대로 두면 조용히 엉뚱한 구간이 잘린다. 소스 안에서 연속으로 매기게 바꿨다.

### 4-12. (8차) 왜 재시도 대신 best-of-3 인가

- **질문 하나에 답하는 방식이 여럿이다.** 직접 답하는 한 덩어리 / 흩어진 답을 이어붙인 조합 / 핵심만
  짧게. 어느 게 나은지는 잘라놓은 **대사를 읽어봐야** 안다 — 구간 요약만 보고는 모른다.
- **judge 가 텍스트를 본다.** 그래서 렌더하지 않고 후보 셋을 판정할 수 있고, 이긴 것 하나만 렌더한다.
  이게 best-of-3 가 싼 이유다. LLM 4회(rank 1 + judge 3), 판정은 병렬이라 체감은 1회.
- **원래 계획(순차 재시도)의 문제**: 느리고, 두 번째 시도가 첫 번째보다 낫다는 보장이 없다.
- **선택 규칙**: 자립·답변 두 관문을 **둘 다** 통과한 것 중 최고점. 점수만 보면 자립하지 않는 클립이
  이긴다. 하나도 통과 못 하면 그중 최고점을 REVIEW 로 올리고 llm NG 를 남긴다 — 사람이 판단한다.
- **조합 렌더는 한 번의 인코딩**으로 한다. 조각을 따로 렌더해 concat 데뮤서로 붙이는 방법도 있지만,
  코덱 파라미터가 하나라도 어긋나면 `-c copy` 가 조용히 싱크 틀어진 파일을 만든다. 필터로 붙이면
  그 위험이 없다. 대신 자막을 이어붙인 타임라인 기준으로 오프셋해야 한다(`build_part_cues`).

---

## 5. 지금 동작하는 것 — 검증 방법

```sh
cd ~/dev/shorts_maker
docker compose up -d --build                          # 🔴 backend/.env 에 SHORTS_ADMIN_PASSWORD · POSTGRES_PASSWORD 필수
docker compose exec shorts sm doctor                  # ffmpeg · libass · NanumGothic · Postgres v1 · Gemini
docker compose exec shorts sm db status               # v1 · 테이블 14개
docker compose exec db psql -U shorts shorts          # SQL 직접

cd backend
uv run python -m unittest discover -s tests -t .      # 호스트, uv. db 컨테이너가 떠 있어야 DB 테스트가 돈다(skipped=0 확인)

cd ../web
npm run build && npm run lint                         # tsc strict 통과. 경고 2개는 admin-web 에서 온 패턴
```

실측(2026-09-06, 컨테이너): 등록 DONE · 청크 재추출 409→replace · STT 55발화 20.7초 · 구간 5개 ·
rank 3위/제외 2 · cut · render · 미리보기 6.2MB · 클립 7.8MB · 비용 추정 44.78원. `stage_calls` 7종 기록.
시청자 면: 비공개 영상 404 → 발행 → 목록 노출 · 질문 등록 · 좋아요 토글 · CTA 이벤트 · 위조 이벤트 422.

| 주소 | 내용 |
|---|---|
| `http://127.0.0.1:8100` | 컨테이너. 이미지에 구워진 React 화면 |
| `/debug` | 개발용 단일 페이지 (비밀번호 모드에선 API 가 401 이라 못 쓴다) |
| `/docs` | Swagger |
| `npm run dev` → `:5173` | HMR. 🔴 **5173 으로 접속** — vite 프록시가 컨테이너의 8100 으로 넘긴다 |

**DB 는 비어 있다** — 2차에서 새로 만들었다. 원본 영상 2편은 `backend/sources/` 에 있고 화면의
"새로 만들기 → 서버에 있는 영상" 에서 등록한다. STT 는 컨테이너(리눅스 CPU)에서 다시 돌려야 한다 —
30분에 4~5분.

---

## 6. 안 한 것 · 남긴 것

| | 상태 | 비고 |
|---|---|---|
| **커밋 · 푸시** | ✅ 완료 | 5개 커밋, `main` 푸시됨 (§2) |
| **SWYP 레포 정리** | ⛔ 손대지 않음 | backend 의 `com.swyp.backend.shorts` Java 13개 · `application.properties` · `compose.yaml` · `DEPLOY.md` · `docs/make_shorts.md`(원본 삭제) / admin-web 의 숏폼 탭(ShortsPage · api/shorts.ts · 컴포넌트 4 · types · CSS · route · nav). **사용자가 다른 세션에서 한다고 했다** |
| **원격 레포 이동** | ✅ 완료 (2026-09-04) | `SWYP-APP-S6` → `HHsungmoon/shorts_maker` 로 transfer. 옛 URL 은 리다이렉트. 로컬 origin 갱신됨 |
| **운영 배포** | ⛔ 안 함 | Naver Cloud 4GB/2vCPU 예정. compose·nginx 설정은 준비됨. `deploy.env.example` 채우고 `deploy-sm` |
| `.env` 에 `SHORTS_ADMIN_PASSWORD` | **넣어야 함** | 도커 기본 경로가 되면서 필수가 됐다. 비어 있으면 컨테이너가 기동을 거부한다 |
| `.env` 의 `SHORTS_SOURCE_DIR` | `sources` 로 바꿀 것 | 시크릿 파일이라 자동 수정 안 함. 컨테이너엔 영향 없고 호스트 `uv run sm` 만 이 값을 본다 |
| STT 재실행 | 필요 | DB 를 새로 만들었다. 두 원본을 등록하고 청크·STT 부터 |
| react-router | 뺐음 | tease.md §8 에서 다시 넣는다 |
| 원티드 채널 사용 허락 | 대기 | 받으면 자막 품질·임베드 허용 확인 (tease.md §5-1, §8-3) |

---

## 7. 다음에 만들 것

**`update_plan.md` 가 실행 순서다** — tease.md §12 를 마일스톤 M0~M9 로 자르고 완료 조건과 테스트를 붙였다.
**M0 완료(9/6). 다음은 M1(스키마 v9).** 아래는 tease.md §12 의 원래 요약이고, update_plan.md 와 다르면 그쪽이 맞다:

1. **스키마 v9** (§6) — 6 테이블 신규 · 컬럼 10개 · `stage_calls` 재생성(🔴 규칙 예외, §6-4)
2. **임베딩 레이어** (§5-3) — 질문 묶기 + 세그먼트 검색. θ 튜닝
3. **공개 API** `/api/watch/**` (§7-1) — 익명 쿠키 · 레이트리밋 · 🔴 발행 클립만
4. **`web/` 재배치** — `shared/` · `studio/` · `watch/` + 라우터
5. **`/watch`** — 모방 유튜브 (iframe · 질문 패널 · 숏폼 줄 · CTA)
6. **rank 확장 + 조합 cut + judge** (§5-5~7)
7. **`/studio` 확장** — 클러스터 패널 · 답하기 · 발행
8. 자막 우선 · 챕터 씨앗 (허락 후)
9. 캐싱 · 단어 타임스탬프 켜기
10. 데모 리허설 (§10)

**첫 단계를 구체적으로:**

```
backend/src/shorts_maker/db/schema.sql   ← tease.md §6-2 DDL 그대로 (SQLite 에서 파싱 검증됨)
backend/src/shorts_maker/db/store.py     ← SCHEMA_VERSION = 9, MIGRATIONS[9] (§6-5),
                                            값 타입을 list[str | Callable] 로 넓히기
backend/tests/test_schema.py             ← v8→v9 마이그레이션이 clip_parts 백필 · 행 수 보존하는지,
                                            새 DB 와 마이그레이션한 DB 의 table_info 가 같은지
```

§13 은 결정됐다(9/6, 권고대로). M1 에서 `store.MIGRATIONS` 값 타입을 `list[str | Callable]` 로 넓히는 것이 첫 손질이다.

---

## 8. 함정 — 반드시 읽을 것

- **`.venv` 를 옮기면 깨진다.** 절대경로가 박혀 있다. 경로가 바뀌면 `rm -rf .venv && uv sync --extra stt`.
- **`sources/`·볼륨은 git 에 없다.** clone 하면 영상도 DB 도 없다. 영상은 이 맥 `backend/sources/`,
  DB 는 도커 볼륨 `shorts-pg`, 산출물은 `shorts-data`. `docker compose down -v` 는 둘 다 지운다.
- **DB 경로는 상대경로다.** `Path(row["path"])` 를 직접 쓰지 말고 `cfg.source_file/work_file`.
  절대경로로 저장했다가 레포 이동으로 전 행이 깨진 게 2차 작업의 출발점이다.
- **`.env` 는 env_file 로 컨테이너에 통째로 들어간다.** 호스트용 값(ffmpeg 경로·폰트·work 경로)은
  compose 의 `environment` 가 덮는다. 새 설정 키를 추가할 때 "컨테이너에서 다른 값이어야 하는가"를 묻고
  그렇다면 compose 에도 넣는다.
- **호스트 `uv run sm …` 과 컨테이너는 같은 DB(`db` 컨테이너)를 본다.** 산출물 폴더만 다르다(`backend/work` vs
  `/data/work`). 테스트는 `shorts_test` 라 운영 데이터를 건드리지 않는다.
- **DB 테스트가 `skipped` 로 나오면 통과가 아니다.** `docker compose up -d db` 를 먼저. 요약의 `skipped=N` 을 본다.
- **Postgres 는 실패한 문장 뒤 트랜잭션이 aborted 다.** 테스트에서 `assertRaises(IntegrityError)` 다음엔 `rollback()`.
  코드에서는 `with store.connect()` 가 예외 시 롤백하니 같은 연결로 계속 쓰지 말 것.
- **긴 계산 앞에서 commit.** 읽기만 해도 트랜잭션이 열려 있다. STT·렌더·지문 계산 몇 분 동안 열어두지 않는다.
- **비밀번호가 비어 있으면 컨테이너가 안 뜬다.** 0.0.0.0 바인딩이라 기동 거부. 호스트 uv 실행에서만 무인증 로컬 모드다.
- **vite 개발 서버는 5173 으로 접속.** 8100 을 직접 열면 빌드된 옛 `dist` 를 본다.
- **SPA catch-all 은 API 라우트 뒤에 등록돼야 한다.** 앞으로 올리면 `/api/**` 가 전부 index.html 을
  받는다. `test_api_auth.py::SpaRoutingTest` 가 잡는다.
- **`BACKEND_ROOT` 는 `backend/` 다, 레포 루트가 아니다.** 상대 경로 설정은 전부 그 아래로 풀린다.
- **`executemany` 는 커서에만 있다.** `conn.execute` 는 되지만 `conn.executemany` 는 psycopg 에 없다 —
  `with conn.cursor() as cur: cur.executemany(...)`. sqlite3 와 다른 지점이고 조용히 AttributeError 로 죽는다.
- **순수 함수만 테스트하면 저장 경로가 빈다.** 위 버그가 그렇게 새어 나갔다. 단계 함수는 DB 에 실제로 쓰는
  테스트를 하나씩 둔다.
- **`stage_calls.stage` 에 새 값을 넣으려면 마이그레이션 파일.** `002_*.sql` 에 `alter table stage_calls drop constraint
  …_stage_check, add constraint … check (stage in (…))`. 코드에서 새 stage 문자열을 먼저 쓰면 insert 가 터진다.
- **컨텍스트 캐싱 ↔ 검색은 겹친다.** 검색으로 후보가 줄면 캐싱 이득이 준다. 둘 다 켜지 말고 측정.
- **좋아요 중복 방지는 쿠키 기반이라 완벽하지 않다** (tease.md §7-4). 데모용 절충. 문서에 적어라.
- **`/api/watch/**` 에 새 라우트를 넣을 땐 셋을 확인한다.** 읽기는 발행된 것만(미발행은 404) · 외부 API
  호출 0회 · 쓰기는 레이트리밋. `tests/http/test_watch.py` 가 지키지만 새 라우트는 자동으로 안 걸린다.
- **시청자 이벤트 중 `question_post`·`like` 는 서버가 넣는다.** 클라이언트가 보낼 수 있는 종류는
  `events.KIND_FROM_CLIENT` 로 좁혀 놨다 — 안 그러면 퍼널 수치를 브라우저가 조작할 수 있다.
- **긴 영상은 청크가 여러 개다.** `chunks[0]` 만 보는 코드를 쓰지 마라. 화면은 합산해서 보여준다.
- **`utterances.idx` 는 청크 안에서만 유일하다.** rank 프롬프트에 그대로 쓰면 청크가 여럿일 때 같은
  번호가 둘이 된다. `ranking.number_lines` 가 프롬프트 전용 연속 번호를 매긴다.
- **한 조각(part)은 한 구간 안이어야 한다.** 구간을 넘나들면 조각 **안에서** 점프가 생기고, 그 점프는
  브릿지 카드 없이 붙어 시청자에게는 말이 튀는 것으로만 보인다. `parse_answer` 가 막는다.
- **`ffprobe` 경로를 `ffmpeg` 경로에서 문자열 치환으로 만들지 마라.** `/opt/.../ffmpeg-full/bin/ffmpeg` 가
  `/opt/.../ffprobe-full/bin/ffprobe` 가 된다. 이 레포는 ffprobe 를 PATH 에서 찾는다.
- **같은 원본에 긴 작업을 두 개 돌리지 마라.** `store.source_lock` 이 막지만, 새로 만드는 긴 작업에는
  직접 걸어줘야 한다. 안 걸면 파괴적 동시 실행이 조용히 통과한다.
- **`segments.idx` 는 소스 안에서 연속이어야 한다.** 청크 하나만 다시 나누면 번호가 어긋나 rank 가
  엉뚱한 구간을 지목한다 — 그래서 부분 재분할 경로가 없다.
- **STT 메모리는 전사 길이에 비례한다.** 30분 = 1.4GB. `SHORTS_CHUNK_MAX_SEC` 를 올리기 전에 이 숫자를 본다.
- **`eval-cluster` 는 실제 데이터를 건드린다.** 같은 소스의 진짜 질문도 함께 묶인다 — 그래서 실행 전 소속을
  스냅숏하고 끝나면 되돌린다. `--keep` 을 쓰면 되돌리지 않는다. 실제로 한 번 당했다.
- **θ 는 민감한 변수가 아니다.** 0.60~0.85 에서 결과가 거의 같다. 묶기 품질은 LLM 의 대표 문장 짓기가
  대부분 결정한다. 이상하면 θ 보다 프롬프트를 먼저 본다.
- **임베딩 벡터는 저장 직전에 정규화한다.** gemini-embedding-001 은 3072 이 아닌 차원에서 정규화된 벡터를
  주지 않는다. 안 하면 내적이 코사인이 아니게 되고 θ 가 조용히 의미를 잃는다(순위는 비슷해 보여 더 나쁘다).
- **시청자 화면을 스튜디오 트리에 넣지 마라.** 스튜디오 트리는 마운트되면서 `/auth/me` 를 부른다.
  한 트리면 시청자에게 로그인 화면이 번쩍인다 — `App.tsx` 가 라우트로 가른 이유다.
- **DB 백업은 `pg_dump`.** `docker compose exec db pg_dump -U shorts shorts > backup.sql`. 볼륨 복사는 버전이 묶인다.

---

## 9. 결정이 필요한 것

`tease.md` §13 표. 각각 권고와 근거가 있다. 이 문서에는 옮기지 않는다 — 두 곳에 있으면 어긋난다.
