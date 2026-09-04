# 인수인계 — shorts_maker

**2026-09-04 기준.** 이 레포를 처음 여는 사람(사람이든 새 Claude 세션이든)이 **10분 안에
"지금 뭐가 있고, 다음에 뭘 하나"를 알게** 하는 문서다. 세부는 다른 문서로 보낸다.

| 알고 싶은 것 | 문서 |
|---|---|
| 지금 상태 · 다음 할 일 · 함정 | **이 문서** |
| 제품(TEASE) · 아키텍처 · 스키마 v9 · 미결 사항 | [`tease.md`](tease.md) |
| 파이프라인 내부 · 비용 기준선 · 코딩 규약 | [`make_shorts.md`](make_shorts.md) |
| 실행 · 테스트 · 배포 명령 | [`../README.md`](../README.md) |
| 작업 규약(커밋 정책, 주석, 언어) | [`../CLAUDE.md`](../CLAUDE.md) — 새 세션이 자동으로 읽는다 |

---

## 1. 한 줄 현황

**SWYP 조직의 3개 레포(backend · admin-web · shorts_maker)에서 shorts_maker 를 완전히 떼어내
독립 서비스로 만들었다.** 자체 프론트(`web/`)와 로그인이 붙었고, 테스트 128개 · 프론트 빌드 ·
실제 DB 마이그레이션(v7→v8)까지 통과했다. **다음 단계는 제품 피벗(TEASE)** — 시청자 질문 기반
숏폼. 설계는 끝났고(`tease.md`) 구현은 시작 전이다.

---

## 2. 🔴 가장 먼저 할 일 — 커밋

**79개 파일이 커밋되지 않은 채 워킹트리에 있다**(VS Code 는 이름변경+수정을 둘로 세서 93개로 보인다). HEAD 는 아직 분리 전 `bc6f1d6` 이다.
`git mv` 로 옮겨서 이력은 따라오지만, **커밋 전에 `git checkout .` 이나 `git stash` 를 잘못 치면
이번 작업이 통째로 날아간다.**

```sh
cd ~/dev/shorts_maker
git status --short | head        # R = 이름 변경(이력 유지), ?? = 신규
git add -A && git commit -m "..."   # CLAUDE.md 규약: 사용자가 명시적으로 요청할 때만
```

커밋을 안 한 이유는 규약 때문이지(사용자 요청 시에만), 미완이라서가 아니다. 검증은 §5 대로
전부 통과한 상태다.

---

## 3. 지금 구조

```
~/dev/shorts_maker/            ← SWYP-APP-S6/ 에서 빠져나옴. 레포 하나, 원격 github.com/HHsungmoon/shorts_maker
├── backend/                   FastAPI + CLI. 파이프라인 본체
│   ├── src/shorts_maker/
│   │   ├── api.py             HTTP. 인증 의존성 · /auth/** · /health · SPA 서빙(catch-all)
│   │   ├── auth.py            🆕 비밀번호 1개 + HMAC 세션 쿠키 + 로그인 잠금. stdlib 만
│   │   ├── config.py          .env 로딩. BACKEND_ROOT = backend/ (레포 루트 아님)
│   │   ├── cli.py             `sm` 명령. serve · db · source · chunk · stt · segment · rank · render
│   │   ├── db/schema.sql      v8. admin id 컬럼 3개 제거됨
│   │   ├── db/store.py        MIGRATIONS (덧붙이기 + 평범한 컬럼 삭제까지)
│   │   ├── web.py             node 빌드 없이 보는 개발용 단일 페이지 (/debug)
│   │   └── (ingest · stt · segmentation · ranking · cutting · render · jobs …)  파이프라인, 변경 없음
│   ├── tests/                 128개. 🆕 test_auth.py · test_api_auth.py(인증 경계) · test_schema.py 확장
│   ├── work/                  ⛔ git 제외. SQLite(v8) + 원본 영상 1.6GB + 백업 shorts.db.bak-before-v8
│   ├── sources/               ⛔ git 제외. scp 로 넣는 원본
│   ├── .env                   ⛔ git 제외. GEMINI_API_KEY 있음, SHORTS_ADMIN_PASSWORD **없음**
│   ├── .env.example           로컬용 템플릿 (인증 항목 추가됨)
│   ├── deploy.env.example     서버용 템플릿
│   └── pyproject.toml         + [dependency-groups] dev = httpx (테스트용)
├── web/                       🆕 React + Vite + TS. admin-web 의 숏폼 탭을 옮겨온 것
│   └── src/
│       ├── api/client.ts      fetch 래퍼. ApiResponse 봉투 없음, 401 → onUnauthorized
│       ├── api/shorts.ts      /api/** 호출. 영상은 URL 만 (blob 우회 삭제)
│       ├── api/auth.ts        /auth/me · login · logout
│       ├── auth/AuthContext   checking / in / out
│       ├── pages/LoginPage    비밀번호 폼
│       ├── pages/ShortsPage   파이프라인 화면 (667줄, 기존 것 이식)
│       ├── components/        Step · MediaLibrary · NewSourceModal · Modal · ClipVideo
│       └── App.tsx            라우터 없음 — 인증 상태로 Login/Shorts 전환 (⚠️ tease.md 에서 라우터 복귀 예정)
├── docs/
│   ├── handoff.md             이 문서
│   ├── tease.md               🆕 제품·아키텍처·스키마 v9 (823줄)
│   └── make_shorts.md         backend 레포에서 가져옴. §0·§10·§13 갱신
├── Dockerfile                 🆕 루트로 이동. node 빌드 → python → runtime, 한 이미지
├── compose.yaml               backend_default 외부 네트워크 제거, 127.0.0.1:8100, mem 3000m
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

---

## 5. 지금 동작하는 것 — 검증 방법

```sh
cd ~/dev/shorts_maker/backend
uv run python -m unittest discover -s tests -t .      # Ran 128 tests … OK
uv run sm doctor                                      # ffmpeg · libass · sqlite v8 · Gemini 전부 ok
uv run sm serve                                       # 127.0.0.1:8100 — 무인증 모드 (.env 에 비밀번호 없음)
SHORTS_ADMIN_PASSWORD=dev1234 uv run sm serve         # 로그인 화면을 보려면

cd ../web
npm run build && npm run lint                         # tsc strict 통과. 경고 2개는 admin-web 에서 온 패턴
```

| 주소 | 내용 |
|---|---|
| `http://127.0.0.1:8100` | React 화면 (`web/dist` 있을 때) · 없으면 개발용 단일 페이지로 폴백 |
| `/debug` | 개발용 단일 페이지 (항상) |
| `/docs` | Swagger |
| `npm run dev` → `:5173` | HMR. 🔴 **5173 으로 접속** — vite 프록시가 동일 오리진을 만들어야 쿠키가 실린다 |

DB 에는 원본 2건(니체 강연, 지혜의 향연)·발화 381·세그먼트 7·클립 2 가 있다. 개발용으로 그대로 쓴다.

---

## 6. 안 한 것 · 남긴 것

| | 상태 | 비고 |
|---|---|---|
| **커밋** | ⛔ 79개 파일 미커밋 | §2. 규약상 사용자 요청 시에만 |
| **SWYP 레포 정리** | ⛔ 손대지 않음 | backend 의 `com.swyp.backend.shorts` Java 13개 · `application.properties` · `compose.yaml` · `DEPLOY.md` · `docs/make_shorts.md`(원본 삭제) / admin-web 의 숏폼 탭(ShortsPage · api/shorts.ts · 컴포넌트 4 · types · CSS · route · nav). **사용자가 다른 세션에서 한다고 했다** |
| **원격 레포 이동** | ✅ 완료 (2026-09-04) | `SWYP-APP-S6` → `HHsungmoon/shorts_maker` 로 transfer. 옛 URL 은 리다이렉트. 로컬 origin 갱신됨 |
| **운영 배포** | ⛔ 안 함 | Naver Cloud 4GB/2vCPU 예정. compose·nginx 설정은 준비됨. `deploy.env.example` 채우고 `deploy-sm` |
| `.env` 에 `SHORTS_ADMIN_PASSWORD` | 없음 | 의도된 로컬 무인증 모드. 외부 바인딩 시 기동 거부됨 |
| react-router | 뺐음 | tease.md §8 에서 다시 넣는다 |
| 원티드 채널 사용 허락 | 대기 | 받으면 자막 품질·임베드 허용 확인 (tease.md §5-1, §8-3) |

---

## 7. 다음에 만들 것

**`tease.md` §12 가 순서다.** 요약:

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

시작 전에 **§13 의 미결 9개를 결정**한다. 권고가 달려 있으니 "권고대로" 한 마디면 된다.

---

## 8. 함정 — 반드시 읽을 것

- **`.venv` 를 옮기면 깨진다.** 절대경로가 박혀 있다. 경로가 바뀌면 `rm -rf .venv && uv sync --extra stt`.
- **`work/` 는 git 에 없다.** clone 하면 DB 도 영상도 없다. 이 맥에만 있다. 백업은 별도로.
- **비밀번호가 비어 있으면 로그인 화면이 안 뜬다.** 버그가 아니라 로컬 모드다. 외부 바인딩은 막힌다.
- **vite 개발 서버는 5173 으로 접속.** 8100 을 직접 열면 빌드된 옛 `dist` 를 본다.
- **SPA catch-all 은 API 라우트 뒤에 등록돼야 한다.** 앞으로 올리면 `/api/**` 가 전부 index.html 을
  받는다. `test_api_auth.py::SpaRoutingTest` 가 잡는다.
- **`BACKEND_ROOT` 는 `backend/` 다, 레포 루트가 아니다.** 상대 경로 설정은 전부 그 아래로 풀린다.
- **`stage_calls.stage` 는 CHECK 라 새 stage 를 못 넣는다.** v9 에서 테이블 재생성이 필요하다
  (tease.md §6-4). `MIGRATIONS` 가 문자열만 받으니 함수 스텝을 허용하게 넓혀야 한다.
- **컨텍스트 캐싱 ↔ 검색은 겹친다.** 검색으로 후보가 줄면 캐싱 이득이 준다. 둘 다 켜지 말고 측정.
- **좋아요 중복 방지는 쿠키 기반이라 완벽하지 않다** (tease.md §7-4). 데모용 절충. 문서에 적어라.
- **세션 배경 서버.** Claude 세션이 띄운 `sm serve` 는 세션이 닫히면 죽는다. 직접 띄워라.
- **DB 백업 `shorts.db.bak-before-v8`** — v9 전에도 하나 더 뜬다. `cp work/shorts.db work/shorts.db.bak-before-v9`.

---

## 9. 결정이 필요한 것

`tease.md` §13 표. 각각 권고와 근거가 있다. 이 문서에는 옮기지 않는다 — 두 곳에 있으면 어긋난다.
