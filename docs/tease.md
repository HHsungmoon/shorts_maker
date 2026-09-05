# TEASE — 시청자 수요로 만드는 숏폼, 원본 유입 엔진

**통합 설계 문서 — 기획 · 기술 · 스키마.** 2026-09-03.

파이프라인 내부(STT · 분할 · rank · cut · render 의 세부, 비용 기준선, 코딩 규약)는
[`make_shorts.md`](make_shorts.md) 가 정본이다. 이 문서는 그 위에 **무엇을 얹어 어떤 제품을
만드는지**를 적는다. 두 문서가 충돌하면 이 문서가 최신이고, 충돌 지점은 `make_shorts.md` 에
반영한다.

표기: 🔴 치명적 주의 · ⚠️ 판단이 갈린 지점 · **[결정]** 정해진 것 · **[미결]** §13 에서 다룸

---

## 0. 한 줄

**시청자가 궁금한 것을 적으면, AI 가 그 답이 있는 구간을 찾아 30초로 만들고, 크리에이터가
승인 한 번으로 발행한다. 숏폼은 완결된 소비가 아니라 원본으로 되돌려보내는 미끼다.**

이 레포(`shorts_maker`)는 원래 "긴 강연 → 기준 프롬프트로 rank → 숏폼" 파이프라인이다.
TEASE 는 그 **기준 프롬프트의 출처를 시청자 질문으로 넓힌 것**이다. 파이프라인 본체는
그대로 쓰고, 앞(수요 수집)과 뒤(발행·계측)를 붙인다.

---

## 1. 서비스 정의

### 1-1. 이름 · 슬로건

**TEASE** — 살짝 보여주고 애태우다. 완결된 소비가 아니라 **미완의 소비를 설계**해 원본으로
되돌려보낸다. 티저(Teaser)라는 업계 용어와 자연스럽게 이어진다.

슬로건 후보:
- "30초만 맛보여 드립니다. 나머지 59분 30초는 원본에서."
- "시청자가 만드는 예고편."
- "숏폼은 미끼다."

### 1-2. 문제

**시청자.** 1시간짜리 채용설명회·컨퍼런스·강의에서 정작 궁금한 건 "연봉 얘기 어디서
나와요?" 한 줄이다. 그 구간을 찾으려면 영상 전체를 스크러빙해야 하고, 대부분은 탐색을
포기하고 **영상 자체를 이탈**한다.

**크리에이터 / 기업 채널.** 숏폼은 조회수에 직결되지만 편집 공수가 든다. 기존 AI 숏폼
툴(Opus Clip, Vrew 등)은 크리에이터가 직접 툴을 켜고, 분석을 돌리고, **어떤 구간이 먹힐지
감으로** 골라야 한다. 그리고 "숏폼이 원본 조회수를 잡아먹는다"는 카니발리제이션 우려가
상존한다.

**시장의 빈틈.** 현재 숏폼 툴은 전부 **크리에이터 → 시청자의 단방향 추측 게임**이다. 어떤
구간에 수요가 있는지 데이터 없이 만든다. **수요를 먼저 받고 만드는 서비스는 없다.**

### 1-3. 솔루션과 차별점

| 구분 | 기존 AI 숏폼 툴 | TEASE |
|---|---|---|
| 편집 시작점 | 크리에이터의 추측 | **시청자의 명시적 질문 + 좋아요**(수요 데이터) |
| 크리에이터 공수 | 툴 실행 → 분석 → 선별 → 편집 | 대시보드에서 **승인 클릭 1회** |
| 숏폼의 목적 | 독립 콘텐츠 (조회수 분산) | **원본으로의 유입 퍼널** |
| 비용 구조 | 요청 건당 AI 호출 | **중립 자산 캐시 + 질문 묶기**로 중복 호출 제거(§4-1) |
| 답이 없을 때 | 뭐든 뽑아냄 | **"이 영상엔 없어요"**(§3-4) |
| 구간 | 연속 A~B 하나 | **A~B + C~D 조합**, 브릿지 카드(§5-6) |
| 타깃 도메인 | 예능·브이로그 | **HR·교육·테크 세미나** (정보성 롱폼) |

### 1-4. 핵심 인사이트

시청자의 질문은 그 자체로 **"이 영상에서 사람들이 진짜 궁금해하는 것"에 대한 1차 데이터**다.
TEASE 는 숏폼 생성기이면서 동시에 **콘텐츠 수요 리서치 툴**이다. 장기적으로 팔릴 자산은
클립이 아니라 **수요 데이터와 유입 계측**이다(§9).

### 1-5. 목표

- **단기: 원티드 AI 해커톤 2026 1등.** 심사가 기술 비중이 크다(§11). 소재는 채널 원티드
  영상 — **사용 허락을 먼저 받는다.**
- **장기: 유튜브 인수.** 그쪽이 살 만한 것은 클립이 아니라 "시청자가 롱폼의 어디를 궁금해하나"
  라는 데이터와, 숏폼→원본 유입을 계측하는 장치다. 지금부터 그 둘을 실제로 쌓는다.

---

## 2. 유저 시나리오

### 2-1. Flow A — 시청자 (모방 유튜브 `/watch`)

1. 원티드 영상(21분) 시청 중. 플레이어 아래 TEASE 패널.
2. **이미 올라온 질문**이 좋아요 순으로 보인다: "이직 기준 어떻게 세워요?" (47) · "희망연봉
   얼마나 올려 불러요?" (31) …
3. 답이 **발행된 질문은 옆에 숏폼**이 붙어 있다 → 클릭 → 즉시 재생. **AI 호출 0회**(캐시).
4. 궁금한 게 목록에 없으면 **직접 적는다** → 원문 그대로 저장된다. **여기서는 AI 도 임베딩도 돌지
   않는다** — 요청 등록이다. 크리에이터가 [집계](§5-3)를 돌린 뒤에 비슷한 질문과 한 묶음이 되고
   "N명이 궁금해함"이 올라간다. 집계 전에는 개별 질문으로 보인다.
5. 숏폼 끝에 **"이 구간부터 원본 보기"** → 같은 페이지의 유튜브 플레이어가 그 초로 이동(§8-3).
   페이지를 떠나지 않는다.

### 2-2. Flow B — 크리에이터 (스튜디오 `/studio`)

1. 로그인(비밀번호). 채널 영상 목록.
2. **[집계]** — 쌓인 질문을 LLM 이 그룹 대표 문장으로 정리하고, 임베딩이 질문을 그 그룹에 붙인다(§5-3).
   스튜디오에서 영상을 열 때 자동으로 한 번 돈다(인증 경로에서만). 그 뒤 영상별 **질문 TOP N** —
   좋아요·묶인 질문 수 순(SQL). "우리 영상에서 사람들이 뭘 궁금해하는지"가 여기서 보인다.
3. 질문 하나(=클러스터, §3-2)를 클릭 → **그 질문을 기준으로 파이프라인 실행**. 몇 초~수십 초.
4. AI 가 뽑은 구간 프리뷰 + judge 소견("자립함 · 질문에 답함") 확인. in/out 미세 조정 가능.
5. **[발행]** 또는 **[거절]**. 발행하면 §2-1 의 3번이 된다.
6. 기존처럼 **크리에이터가 직접 기준을 써서 뽑는 경로도 그대로 있다** — "인용할 만한 한 문장",
   "핵심만 30초". 질문 게시판은 기준의 *또 하나의 출처*이지 대체가 아니다.
7. 성과: 숏폼 재생 수 · CTA 클릭 · 원본 이동 · 원본 시청 시작(§9).

### 2-3. 루프가 닫히는 곳

```
/watch  시청자가 질문 ──► /studio  크리에이터가 승인 ──► /watch  숏폼이 그 영상 아래에 붙음
```

**실제 유튜브로 나가지 않고 우리 플랫폼 안에서 닫힌다.** 심사위원이 두 화면을 나란히 놓고
시청자 역할과 크리에이터 역할을 **직접 둘 다** 해볼 수 있다.

### 2-4. 왜 크롬 확장이 아니라 모방 플랫폼인가

확장은 **시청자 화면만** 대체한다. 숏폼을 자르려면 영상 파일이 필요해서 어차피 백엔드가
`yt-dlp` 로 받아야 하고, 어려운 부분은 하나도 줄지 않는다. 대신 manifest · content script ·
CSP · 스토어 심사가 새로 붙는다. 그리고 **심사에서는 모방 플랫폼이 더 잘 보인다** — 화면
전체를 통제하니 두 역할을 나란히 보여줄 수 있다. 확장은 제품이 검증된 뒤의 유통 채널이다.

---

## 3. 수요의 단위 — 질문, 좋아요, 클러스터

### 3-1. 질문 ≠ 태그

수요의 단위는 `#연봉` 같은 태그가 아니라 **자연어 질문**이다. "연봉 얼마예요?" 와 "급여 수준
궁금해요" 는 같은 수요다. 태그로 받으면 그 통합을 시청자가 해야 하고(안 한다), 질문으로 받으면
**시스템이 묶는다**(§5-3). 묶인 질문 수 + 좋아요 = "**N명이 궁금해함**" — 크리에이터가 왜 이
숏폼을 만들어야 하는지가 자명해지는 숫자다.

### 3-2. **[결정]** 클러스터가 작업 단위다

비슷한 질문 여러 개 → **클러스터 하나 → 숏폼 하나.** 질문마다 만들면 거의 같은 클립이
여러 개 생긴다. 클러스터의 대표 문장(`canonical_text`)이 `runs.criteria_prompt` 로 들어간다.
질문이 하나뿐인 클러스터도 클러스터다 — 코드 경로가 하나로 통일된다.

크리에이터는 클러스터 안의 개별 질문을 펼쳐 볼 수 있고, 대표 문장을 고쳐 쓸 수 있다.

### 3-3. 질문 유형 — 편향을 피하기 위해

한 질문("연봉 얼마?")에 맞춰 설계하면 그 유형에만 잘 되는 시스템이 나온다. 아래는 **채널
원티드 「잡소리」 ep.45** (이직 방법 총정리, 21분, 7챕터) 기준의 유형 분류다. 개발 중 테스트
질문 세트는 **유형별 최소 하나**를 갖는다(§10-3).

```
 0:00 인트로 · 1:15 이직 기준 세팅 · 3:06 나는 어떤 상태일까 -1 · 5:10 -2
10:22 어떤 회사로 이직해야 할까 · 14:19 나는 경쟁력 있는 인재일까 · 19:26 마무리
```

| 유형 | 예시 | 답의 위치 | 파이프라인에서 걸리는 곳 |
|---|---|---|---|
| 사실 조회 | "희망연봉 얼마나 올려 불러요?" | 한 구간에 국소 | 검색(§5-3)이 잘 먹음 |
| 절차 | "이직 기준을 어떻게 세워요?" | 챕터 1 | 단일 구간. 챕터가 곧 답 |
| 자기 진단 | "지금 이직해야 하는 상태인지 어떻게 알아요?" | 챕터 2+3 에 **분산** | **조합**(§5-6). 단일 컷은 반쪽 답 |
| 조건부 | "3년차인데 어떤 회사로 가요?" | 챕터 4 — 영상은 *틀*만 줌 | 답이 **부분적**. 과장하지 않기 |
| 시리즈 밖 | "면접 준비는요?" | **이 영상에 없음**(`#면접준비`는 다른 편) | 채널 검색 → 다른 편 안내, 없으면 "없어요" |
| 하이라이트 | "핵심만 30초로" | 질문이 아님 | 검색할 쿼리가 없음 → **기존 rank 그대로**(§5-4) |
| 검증 | "이거 진짜 효과 있어요?" | "원티드 데이터로 검증" 대목 | 답은 있되 근거 강도를 과장하면 안 됨 |

### 3-4. "답이 없다"는 실패가 아니라 기능이다

뭘 물어도 뭔가 뽑아내는 툴과, **"이 영상엔 그 답이 없어요"** 라고 말하는 툴은 다른 제품이다.
시리즈 밖 질문과 조건부 질문이 이걸 시험한다. judge(§5-7)가 자립성뿐 아니라 **답변
가능성(answerability)** 을 판정하는 이유다. 채널 검색이 다른 편에서 답을 찾으면 그쪽으로
안내한다 — 이 순간이 데모에서 가장 "똑똑해 보이는" 장면이다(§10-2).

---

## 4. 아키텍처

### 4-1. 기존 위에 얹는다 — 중립/주관 분리가 곧 시맨틱 캐시

`make_shorts.md` §3 의 원칙이 TEASE 의 비용 구조를 이미 만들어 놨다.

```
Source ─ Chunk ─ Segment      중립 자산. 영상당 1회 (STT + 분할 = 비싸고 느림)
                   │
                   └─ Run     주관. criteria_prompt 마다 1회 (rank = 1센트 수준, 수 초)
                        └─ Clip
```

- **비싼 것은 영상당 한 번**, 질문마다 드는 건 rank 한 번. 질문이 100개 쌓여도 STT 는 다시
  돌지 않는다.
- 질문 묶기(§5-3)로 **같은 수요는 rank 도 한 번**. 발행된 클러스터의 재생은 AI 호출 0.
- 이 전부가 `stage_calls` 에 실측으로 남는다 — "질문당 N원"을 슬라이드가 아니라 **DB 에서**
  꺼내 보여준다.

### 4-2. 전체 그림

```
                           ┌────────────────────────── /watch (공개) ──────────────────────────┐
  시청자 ── 질문 등록 ──► questions (원문 · cluster_id null · AI 0회)
        ── 좋아요 ─────► question_likes
        ── 숏폼 재생 / CTA / 원본 이동 ──► viewer_events
                           └───────────────────────────────────────────────────────────────────┘
                                                        │
                           ┌────────────────────────── /studio (인증) ─────────────────────────┐
  크리에이터 ── [집계] ──► LLM: 새 그룹 대표 문장 ──► [임베딩] 질문↔대표 코사인 ──► question_clusters (OPEN)
             ── 클러스터 선택 ──► "이 질문에 답하기"
                                    │
                                    ├─ 라우팅 (§5-4): 쿼리 의미 있음 → 검색 경로 / 없음 → rank 경로
                                    ├─ [검색] canonical 임베딩 vs segment 임베딩 → 상위 k (이 영상 → 채널)
                                    ├─ [rank] 후보 중 질문에 답하는 구간 1~3개, 순서대로 + 답변가능성
                                    │        └ 화이트리스트 검증 (존재하는 idx 만) — 기존 로직
                                    ├─ [cut]  발화 범위(단어 단위) → clip_parts → ffmpeg concat + 브릿지
                                    ├─ [judge] "이어서 봤을 때 답이 되나? 자립하나?" → clip_reviews(llm)
                                    │        └ 실패 → 최고점 범위 하나로 재시도(1회) → 또 실패 → REVIEW+경고
                                    └─ [render] → 클러스터 REVIEW
              ── 프리뷰 확인 ── [발행] ──► clips.published_at ──► 클러스터 PUBLISHED ──► /watch 에 노출
                              [거절] ──► DECLINED
                           └───────────────────────────────────────────────────────────────────┘
```

### 4-3. 서비스 경계 — 공개 면과 스튜디오

한 백엔드, 한 오리진, 두 면.

| | `/watch/**`, `/api/watch/**` | `/studio`, `/api/**`(기존) |
|---|---|---|
| 인증 | **없음** (익명 시청자 쿠키, §7-4) | 세션 쿠키 (기존 `require_auth`) |
| 읽기 | 발행된 것만 | 전부 |
| 쓰기 | 질문 · 좋아요 · 이벤트 (레이트리밋) | 파이프라인 실행 · 발행 · 거절 |

🔴 **클립 파일** `/api/clips/{id}/file` 은 지금 인증 필수다. 시청자가 재생하려면 공개 경로가
필요한데, **`published_at is not null` 인 클립만** 공개한다(§7-3). 미발행 클립은 계속 인증 뒤에
있다. 이 조건 하나가 빠지면 크리에이터가 거절한 클립이 시청자에게 보인다.

---

## 5. 파이프라인 확장

각 항목은 `make_shorts.md` §4 의 단계 번호를 따른다. **전부 `stage_calls` 에 기록한다** — 새
stage 값은 §6-4.

### 5-1. [1] 대사 확보 — 자막 우선, whisper 폴백 **[미결 — 허락 후 품질 확인]**

ep.45 에 **한국어 자동자막**이 있다. whisper `small` 로 21분을 돌리면 2vCPU 에서 수 분이고,
자막은 즉시다. 이 서비스의 실제 제약은 비용이 아니라 **사용자가 기다리는 시간**(`make_shorts.md`
§7)이라, 유튜브 원본의 대기 시간을 사실상 없애는 가장 큰 단일 개선이다.

- `yt-dlp --write-auto-sub --sub-lang ko` → VTT 파싱 → `utterances` (자막 큐 = 발화)
- `sources.caption_source = 'youtube'`. 없으면 whisper → `'whisper'`
- ⚠️ 자동자막 품질은 whisper 보다 낮다. 그러나 rank 는 발화 **내용**을 보므로 오타에 덜 민감하다.
  렌더 자막(번인)은 품질이 보이므로 필요하면 그때만 whisper 로 다시 뽑는다(발행 시점, 비동기).
- 허락받은 뒤 ep.45 자막을 실제로 보고 결정한다. 결정 전까지는 whisper 경로가 기본.

### 5-2. [3] 구간 분할 — 챕터를 씨앗으로

유튜브 챕터는 **사람이 손으로 나눈 주제 분할**이다. 있으면 `sources.chapters`(JSON) 에 저장하고:

- LECTURE 이고 챕터가 있으면 분할 프롬프트에 힌트로 준다("이 영상은 이렇게 나뉘어 있다.
  이 경계를 존중하되 필요하면 더 잘게 나눠라").
- **측정**: 우리 분할 경계와 챕터 경계의 일치율. `make_shorts.md` §11 품질 측정에 바로 쓰이는
  첫 숫자다.
- 챕터를 그대로 세그먼트로 쓰는 것은 하지 않는다 — 챕터는 5분 단위라 30초 클립의 후보로는
  너무 굵다. 분할은 여전히 LLM 이 하고, 챕터는 그 경계를 잡아주는 역할.

### 5-3. 임베딩 레이어 — 질문 묶기와 검색은 같은 것이다 (RAG)

**하나의 임베딩 시스템이 두 문제를 푼다.** 이게 덧붙인 게 아니라 설계인 이유다.

**(a) 질문 묶기 — 크리에이터가 트리거하는 [집계].** 질문이 들어올 때는 아무것도 돌지 않는다
(2026-09-05 결정). 공개 경로가 요청마다 외부 API 를 부르면 레이트리밋이 있어도 공격면이고,
"수요를 취합해서 고른다"는 제품 정의상 묶기는 크리에이터의 행동이다. [집계]는 스튜디오에서
영상을 열 때 자동으로 한 번, 또는 버튼으로 돈다. 역할을 셋으로 가른다:

| 역할 | 누가 | 왜 |
|---|---|---|
| 그룹 **이름 짓기** — 미분류 질문 + 기존 대표 문장을 보고 **새 그룹의 대표 문장**만 뽑는다 | LLM 1회 (`stage='cluster'`) | 유일하게 LLM 이 필요한 지점. 출력 검증: 비어 있지 않고, 기존 대표와 중복이 아닐 것 |
| 질문을 그룹에 **붙이기** — 대표 문장과 미분류 질문을 한 번에 임베딩(배치 1회), 질문마다 가장 가까운 대표에 코사인 ≥ θ 면 합류 | 임베딩 (`stage='embed'`) | 기계적·재현 가능·θ 로 튠. 못 붙으면 원문이 곧 대표 문장인 단독 클러스터 |
| **개수** — "N명이 궁금해함" | SQL `count(questions) + sum(likes)` | LLM 이 세면 틀린다 |

🔴 LLM 에게 질문의 **소속(인덱스)이나 개수**를 시키지 않는다. 수백 개를 인덱스로 뱉게 하면 빠지고
겹치고, 개수는 세다가 틀린다. 소속은 임베딩이, 개수는 SQL 이 한다. 원문 `questions.text` 는 재집계해도
바뀌지 않고, 기존 클러스터 id 와 좋아요는 보존된다(증분 — 다음 집계는 `cluster_id is null` 인 것만).
🔴 **θ 가 성패를 가른다.** 너무 높으면 전부 단독 클러스터가 돼 "N명이 궁금해함"이 전부 1 이 되고,
너무 낮으면 다른 질문이 섞인다. 초기값 0.85, §10-3 의 질문 세트로 튜닝 **[미결]**.

**(b) 구간 검색.** 세그먼트 `description`(+ 발화 앞 N자) 을 임베딩(`RETRIEVAL_DOCUMENT`)해 두고,
클러스터 대표 문장을 쿼리로 코사인 → 상위 k(=5) 를 rank 후보로. 효과:
- 입력 토큰이 전 세그먼트 → 5개로 줄어 rank 가 빨라지고 싸진다
- **채널 횡단**: 같은 채널의 다른 영상 세그먼트까지 검색 범위를 넓히면 "면접 준비는요?" 에
  "ep.47 에 있어요" 가 나온다(§3-4)
- 선택 근거가 숫자로 남는다("유사도 0.87") — 설명 가능성
- 최대 유사도가 임계값 미만이면 **답변 불가의 1차 신호**

**저장·연산.** `embeddings` 테이블(§6-2)에 float32 blob. **벡터 DB 는 넣지 않는다** — 세그먼트
수백 개면 numpy 코사인 브루트포스가 1ms 밑이다. "N<1000 이라 벡터 DB 를 안 썼다" 는 문장이
벡터 DB 를 쓴 것보다 기술 심사에서 좋게 읽힌다. 수만 개가 되면 sqlite-vec 를 본다.

**모델.** Gemini 임베딩(`gemini-embedding-001` 계열). 문서/쿼리 비대칭 `task_type` 을 쓴다.
비용은 rank 보다 한 자릿수 싸다. `stage_calls.stage='embed'`.

⚠️ 용어: 엄밀히는 생성이 아니라 **선택**을 검색으로 보강하는 것이라 "retrieval-augmented
selection" 이 더 정확하다. 제출 문서에는 RAG 라고 쓰되, 이 뉘앙스를 한 줄 적는다.

### 5-4. 라우팅 — 검색 경로 vs rank 경로

쿼리 의미가 있는 질문("연봉 얼마")은 검색을 먼저 타고, 쿼리가 없는 요청("핵심만 30초",
"재밌는 부분")은 기존 rank 로 바로 간다. **두 경로가 반드시 공존한다** — 후자가 곧 기존
크리에이터 경로다.

분기는 **LLM 분류 1회**(수십 토큰)로 한다 **[미결 — 규칙 기반과 비교]**. 결과를
`runs.route` 에 남겨 어느 경로가 얼마나 쓰였는지 본다.

### 5-5. [5] rank 확장 — 순서 있는 범위 1~3개 + 답변 가능성

기존 rank 는 세그먼트 **하나**를 고른다. 확장:

- 출력: `{ answerable: bool, reason, parts: [{segment_idx, start_utterance_idx, end_utterance_idx}, …] }`
  — 순서 있는 **1~3개** 범위. 30초 예산(§5-6)을 프롬프트에 알려준다.
- **화이트리스트 검증은 범위마다** 그대로 적용된다(존재하는 idx, 범위 안). 검증 실패 → 재시도.
  `make_shorts.md` §12 의 원칙이 그대로 산다.
- `answerable=false` → 클러스터 `UNANSWERABLE`, reason 을 시청자에게 그대로 보여주지 않고
  크리에이터가 확인한다. 채널 검색이 다른 영상에서 후보를 찾았으면 그 영상 id 를 함께 남긴다.
- 자립성 관문은 그대로 — 감점이 아니라 **제외**(§1 EchoCut 교훈).

### 5-6. [6] 조합 cut — A~B + C~D, 브릿지, 30초 예산

Q&A 숏폼에는 조합이 **맞는 답**이다. "지금 이직해야 하는 상태인지" 의 답은 챕터 2 와 3 에
흩어져 있다. 연속 구간 하나로는 반쪽이다. 그리고 **이게 화면에 보이는 유일한 차별점**이다 —
RAG 와 judge 는 인프라라 안 보이고, 조합은 심사위원이 클립을 틀면 12:30 에서 41:00 으로
점프하면서 질문에 답하는 게 보인다.

🔴 **잘못 이어붙인 클립은 깔끔한 단일 컷보다 나쁘다.** 화자의 흐름이 끊겨 협박 편지처럼
들리는 게 AI 숏폼 툴의 고전적 실패다. 그래서 조합은 **judge(§5-7) 없이는 넣지 않는다.**

- `clip_parts` 에 범위별 행. 단일 컷도 part 1개 — 코드 경로 하나.
- ffmpeg `concat`(filter). render 가 이미 재인코딩하므로 추가 비용 없음.
- part 사이 **브릿지 카드** 0.4초 — "…41분에서 이어집니다". 점프를 숨기지 않고 **읽히게** 만드는
  쪽이 더 낫다. 시청자는 "편집됐다"를 알고 봐야 한다.
- **30초 예산** `SHORTS_TEASER_MAX_SEC=30`. rank 프롬프트에 알려주고, **코드가 강제**한다 —
  합이 넘으면 점수 낮은 part 부터 자르고, 그래도 넘으면 최고점 part 하나만. LLM 이 말한 길이를
  믿지 않는다.
- 단어 단위 타임스탬프(§5-8)로 앞뒤 필러("음, 그러니까")를 떼어 조인다.

### 5-7. LLM-as-judge + bounded retry

지금 파이프라인에 구멍이 하나 있다. rank 의 자립성 관문은 *세그먼트 설명*을 보고 판정하는데,
cut 은 그 안의 *발화 부분 범위*를 고른다. **잘려나온 실제 구간이 혼자 서는지는 아무도 안 본다.**
`make_shorts.md` §11 "미해결 과제 — 품질 측정" 이 정확히 이 얘기다.

- cut 뒤, render 전에 judge: 조합된 발화 텍스트를 순서대로 주고 **두 가지**를 묻는다 —
  ① 앞을 안 본 사람이 이해하나(자립) ② 클러스터 질문에 답하나(답변)
- 결과를 `clip_reviews` 에 `reviewer='llm'` 으로 기록. 사람의 OK/NG 는 `reviewer='human'`.
  **둘의 일치율**이 §11 의 첫 품질 지표다.
- 실패 → **최고점 part 하나로 축소해 1회 재시도.** 또 실패 → 그래도 REVIEW 로 올리되 경고 표시.
  크리에이터가 최종 판단한다. 무한 루프 없음.
- ⚠️ 이건 "에이전트 하네스"가 아니다. 파이프라인은 고정 DAG 라 다음에 뭘 할지 에이전트가
  결정할 게 없다. **경계가 있는 판정→재시도 루프** 하나다. 제출 문서에도 그렇게 쓴다 —
  정확한 용어가 심사에서 더 강하다.

### 5-8. 켜기만 하면 되는 것 — 이미 예약돼 있다

| | 상태 | 효과 |
|---|---|---|
| **단어 단위 타임스탬프** | `utterances.words` 컬럼 예약됨. faster-whisper `word_timestamps=True` 한 플래그 | 발화 경계가 아니라 단어 경계에서 자른다. 필러 제거, 클립이 조여진다 |
| **Gemini 컨텍스트 캐싱** | `stage_calls.cached_tokens` 컬럼 예약됨(config 주석: "지금은 안 쓰지만 값은 남겨둔다") | 질문마다 같은 세그먼트 목록을 다시 보내는 구조라 입력 비용 ~90% 절감. **전후 숫자가 DB 에서 바로 나온다** |

⚠️ 캐싱과 검색(§5-3 b)은 겹친다 — 검색으로 후보가 5개로 줄면 프롬프트가 작아져 캐싱 이득이
준다. 모델별 최소 캐시 토큰(1k~4k) 도 있어 20 세그먼트 영상은 못 미칠 수 있다. **검색을
먼저, 캐싱은 rank 경로(전 세그먼트를 보내는 쪽)에만.** 측정하고 결정한다.

### 5-9. 하지 않는 것 — 그리고 왜 (제출 문서에도 적는다)

- **화자 분리(diarization).** 채용설명회 Q&A 에는 가치가 있지만 STT 시간이 늘어 **속도 목표와
  충돌**한다. 2vCPU 에서 whisperX 는 무겁다. 강의형(ep.45)은 화자가 1명이라 당장 필요도 없다.
- **과거 인기 숏츠 이력 RAG** ("이 채널에서 뭐가 잘 됐나"). 외부 데이터(YouTube Data API)가
  필요하고, 기업 채널엔 이력이 얇고, 결과가 rank 보조 신호 하나에 그친다. "RAG 넣으려고
  넣었다" 로 읽힐 위험.
- **범용 에이전트 프레임워크.** DAG 에 필요 없다(§5-7).
- **영상 이해(Gemini 비디오 입력).** 강연은 화면에 정보가 없다(`make_shorts.md` §1-2). FILM
  단계의 일이다.
- **벡터 DB.** §5-3.

---

## 6. 데이터 모델 — 스키마 v9

### 6-1. 원칙

`make_shorts.md` §5 와 `store.py` 의 규칙을 따른다: **덧붙이기만.** 새 테이블은 제약을 다
걸고, 기존 테이블에 추가하는 컬럼은 CHECK 를 걸지 않는다(새 DB 와 마이그레이션한 DB 의
스키마가 갈리므로 — 검증은 코드에서). 기존 데이터(발화 381개, 수 분짜리 STT)는 날리지 않는다.

🔴 **예외가 하나 있다** — `stage_calls.stage` 의 CHECK 집합에 새 stage 를 넣어야 한다(§6-4).
SQLite 는 CHECK 를 ALTER 로 못 바꾼다. 테이블 재생성이 필요하고, 그건 규칙이 금하는 일이다.
왜 이번만 하는지는 §6-4.

### 6-2. 새 테이블

```sql
-- ============================================================
-- questions — 시청자가 적은 질문 원문 (§3-1)
-- ============================================================
-- 원문을 그대로 남긴다. 묶기(§5-3)는 cluster_id 로 표현하고, 다시 묶어도 원문은 안 바뀐다.
create table if not exists questions (
    id          integer primary key,
    -- 어느 영상을 보다가 물었나. 답이 다른 영상에 있을 수 있어도(§3-4) "물은 곳"은 여기다.
    source_id   integer not null references sources (id) on delete cascade,
    text        text    not null check (length(trim(text)) > 0),
    -- 익명 시청자 쿠키 id(§7-4). 계정이 없으므로 FK 아님.
    viewer_id   text    not null,
    cluster_id  integer references question_clusters (id) on delete set null,
    created_at  text    not null default (datetime('now'))
);
create index if not exists idx_questions_source  on questions (source_id);
create index if not exists idx_questions_cluster on questions (cluster_id);

-- ============================================================
-- question_likes — 좋아요. 클러스터가 아니라 **질문**에 붙는다
-- ============================================================
-- 화면은 클러스터를 보여주지만 좋아요는 대표 질문 행에 저장한다. 다시 묶어도 좋아요가
-- 보존된다 — 클러스터에 붙이면 재클러스터링 때마다 옮겨야 한다.
create table if not exists question_likes (
    id          integer primary key,
    question_id integer not null references questions (id) on delete cascade,
    viewer_id   text    not null,
    created_at  text    not null default (datetime('now')),
    -- 🔴 같은 사람이 두 번 못 누른다. 이게 없으면 좋아요 순위가 새로고침 연타로 조작된다.
    unique (question_id, viewer_id)
);

-- ============================================================
-- question_clusters — 작업 단위 (§3-2). 클러스터 하나 = 숏폼 하나
-- ============================================================
create table if not exists question_clusters (
    id              integer primary key,
    source_id       integer not null references sources (id) on delete cascade,
    -- 대표 문장. 처음엔 첫 질문 원문, 3개 이상 모이면 LLM 이 다시 쓴다(§5-3 a).
    -- 이게 runs.criteria_prompt 로 들어간다.
    canonical_text  text    not null check (length(trim(canonical_text)) > 0),
    -- 상태 기계는 §6-6. 파생 가능한 값이지만 조인 넷을 타야 해서 명시한다.
    status          text    not null default 'OPEN'
                    check (status in ('OPEN', 'IN_PROGRESS', 'REVIEW', 'PUBLISHED',
                                      'DECLINED', 'UNANSWERABLE')),
    -- 이 클러스터에 답하려고 띄운 run. 클립이 나오기 전에도 진행 상태를 보여야 한다.
    run_id          integer references runs (id) on delete set null,
    -- UNANSWERABLE 인데 채널 검색이 다른 영상에서 후보를 찾았으면 그 영상(§3-4).
    suggested_source_id integer references sources (id) on delete set null,
    created_at      text    not null default (datetime('now')),
    updated_at      text    not null default (datetime('now'))
);
create index if not exists idx_clusters_source_status on question_clusters (source_id, status);

-- ============================================================
-- embeddings — 질문 묶기와 구간 검색이 공유하는 벡터 저장소 (§5-3)
-- ============================================================
-- 컬럼이 아니라 테이블인 이유: 세그먼트·질문·클러스터 셋이 다 임베딩을 갖고, 모델을 바꾸면
-- 셋을 다 다시 계산해야 한다. model 을 키에 넣어 새 모델 벡터가 옛것과 섞이지 않게 한다.
create table if not exists embeddings (
    id          integer primary key,
    kind        text    not null check (kind in ('segment', 'question', 'cluster')),
    ref_id      integer not null,
    model       text    not null,
    dim         integer not null,
    -- float32 little-endian. numpy.frombuffer 로 바로 읽는다. 벡터 DB 없음(§5-3).
    vector      blob    not null,
    created_at  text    not null default (datetime('now')),
    unique (kind, ref_id, model)
);
-- ref_id 는 kind 에 따라 다른 테이블을 가리켜서 FK 를 걸 수 없다. 대상이 지워지면 고아가 남는데,
-- 검색 시 조인에서 자연히 빠지고, `sm db vacuum-embeddings` 로 주기적으로 지운다.

-- ============================================================
-- clip_parts — 조합 클립의 조각 (§5-6). 단일 컷도 part 1개
-- ============================================================
create table if not exists clip_parts (
    id                  integer primary key,
    clip_id             integer not null references clips (id) on delete cascade,
    ordinal             integer not null,
    segment_id          integer references segments (id) on delete set null,
    start_sec           real    not null,
    end_sec             real    not null,
    -- 🔴 초는 여기서 파생된다. LLM 이 말한 초가 아니라 utterances(단어 단위 포함)에서 되찾은 값.
    start_utterance_idx integer not null,
    end_utterance_idx   integer not null,
    unique (clip_id, ordinal),
    check (end_sec > start_sec),
    check (end_utterance_idx >= start_utterance_idx)
);

-- ============================================================
-- viewer_events — 유입 계측 (§9). append-only
-- ============================================================
-- "숏폼이 원본 유입을 늘린다"는 여기 쌓인 행 없이는 주장할 수 없다.
create table if not exists viewer_events (
    id          integer primary key,
    viewer_id   text    not null,
    source_id   integer references sources (id) on delete set null,
    clip_id     integer references clips (id) on delete set null,
    kind        text    not null check (kind in
                        ('short_play', 'short_complete', 'cta_click', 'origin_seek',
                         'origin_play', 'question_post', 'like')),
    -- 위치(초), 재생 길이 같은 부가 정보 JSON.
    payload     text,
    created_at  text    not null default (datetime('now'))
);
create index if not exists idx_events_source_kind on viewer_events (source_id, kind);
create index if not exists idx_events_clip on viewer_events (clip_id);
```

### 6-3. 기존 테이블에 추가하는 컬럼 (CHECK 없음)

```sql
-- sources
alter table sources add column youtube_id     text;   -- 임베드 플레이어·중복 판정. origin 파싱은 취약하다
alter table sources add column chapters       text;   -- yt-dlp 챕터 JSON (§5-2)
alter table sources add column caption_source text;   -- 'youtube' | 'whisper' (§5-1). 코드에서 검증
alter table sources add column channel        text;   -- 채널 횡단 검색의 범위 키 (§5-3 b)
alter table sources add column published      integer not null default 0;  -- /watch 목록 노출 여부

-- runs
alter table runs add column route text;               -- 'retrieval' | 'rank' (§5-4)

-- clips
alter table clips add column question_cluster_id integer references question_clusters (id) on delete set null;
alter table clips add column published_at text;       -- 🔴 null 이면 시청자에게 보이지 않는다 (§7-3)
alter table clips add column total_sec    real;       -- part 합. start/end 는 조합 클립에선 봉투(envelope)일 뿐

-- clip_reviews
alter table clip_reviews add column reviewer text;    -- 'human' | 'llm' (§5-7). 코드에서 검증
```

`clips.start_sec / end_sec` 는 유지한다 — 조합 클립에서는 첫 part 시작 ~ 마지막 part 끝(봉투)
이고, 실제 길이는 `total_sec`. 기존 코드가 이 두 컬럼을 읽으므로 없애지 않는다.

`utterances.words` 는 이미 있다. 채우기만 한다(§5-8).

### 6-4. 🔴 `stage_calls` 재생성 — 규칙의 유일한 예외

새 stage: `'caption'`(자막 파싱) · `'embed'` · `'retrieve'` · `'cluster'`(대표문장 재작성) ·
`'judge'` · `'classify'`(라우팅). 기존 CHECK `stage in ('ping', …, 'render')` 에 없다.

**대안 검토.**
- (a) 새 stage 를 기존 값으로 위장하고 `params` 로 구분 → "오타가 나면 집계가 조용히 쪼개진다"
  는 CHECK 의 목적을 스스로 무너뜨린다. 거부.
- (b) 새 stage 만 별도 테이블 → 계측이 두 곳으로 갈려 §7 비용 집계가 깨진다. 거부.
- **(c) 테이블 재생성.** SQLite 공식 ALTER 절차(새 테이블 생성 → 복사 → 삭제 → 개명 → 인덱스).

(c) 를 택하는 이유: `stage_calls` 는 **append-only 로그이고 이 테이블을 참조하는 FK 가 없다.**
규칙이 재생성을 금한 이유는 "조용히 어긋날 여지" 인데, 그 위험은 참조 무결성과 schema.sql 불일치에서
온다. 전자는 여기 해당 없고, 후자는 `test_a_fresh_db_...` 류 테스트가 새 DB 와 마이그레이션한 DB
의 `pragma table_info` 를 비교해 잡는다.

`MIGRATIONS` 는 지금 `dict[int, list[str]]` 이다. 재생성은 단일 SQL 문장이 아니라 **멱등하게
짜기 어렵다**(중간에 죽으면 `_new` 테이블이 남는다). 따라서 **v9 는 값 타입을
`list[str | Callable[[sqlite3.Connection], None]]` 로 넓히고**, 재생성 스텝은 함수로 둔다 —
함수 안에서 `_new` 존재 여부를 보고 이어서 하거나 정리한다.

### 6-5. `MIGRATIONS[9]`

```python
9: [
    # 새 테이블 — 전부 if not exists 라 멱등
    "create table if not exists question_clusters (...)",   # §6-2 순서: clusters → questions (FK)
    "create table if not exists questions (...)",
    "create table if not exists question_likes (...)",
    "create table if not exists embeddings (...)",
    "create table if not exists clip_parts (...)",
    "create table if not exists viewer_events (...)",
    "create index if not exists ...",                       # 위 인덱스 전부
    # 컬럼 추가 — _apply_once 가 duplicate column 을 넘긴다
    "alter table sources add column youtube_id text",
    # … §6-3 전부
    # 🔴 기존 클립을 part 1개로 백필. insert 는 재실행 시 중복되므로 not exists 로 막는다.
    """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,
                               start_utterance_idx, end_utterance_idx)
       select c.id, 0, c.segment_id, c.start_sec, c.end_sec,
              s.start_utterance_idx, s.end_utterance_idx
       from clips c join segments s on s.id = c.segment_id
       where not exists (select 1 from clip_parts p where p.clip_id = c.id)""",
    "update clips set total_sec = end_sec - start_sec where total_sec is null",
    # 기존 사람 리뷰는 전부 human
    "update clip_reviews set reviewer = 'human' where reviewer is null",
    _recreate_stage_calls_with_new_stages,                  # §6-4, 함수
]
```

`schema.sql` 도 같은 내용으로 갱신한다(새 DB 경로). `SCHEMA_VERSION = 9`.

### 6-6. 클러스터 상태 기계

```
                 크리에이터 "답하기"        rank/cut/judge 완료        [발행]
  OPEN ─────────────────────────► IN_PROGRESS ──────────────────► REVIEW ─────────► PUBLISHED
    ▲                                  │                            │
    │  (질문 추가·좋아요는 상태 무관)    │ rank: answerable=false      │ [거절]
    │                                  ▼                            ▼
    │                            UNANSWERABLE                    DECLINED
    │                             (suggested_source_id 있을 수 있음)
    └── 크리에이터 "다시 열기" ◄────────┴────────────────────────────┘
```

- `IN_PROGRESS` 에서 잡이 죽으면(재기동) `OPEN` 으로 되돌린다 — 기존 "RUNNING → FAILED" 정리와
  같은 자리(`api.serve`).
- `PUBLISHED` 는 `clips.published_at is not null` 인 클립이 정확히 하나 있다는 뜻이다. 발행을
  취소하면 `REVIEW` 로.

---

## 7. API

### 7-1. 공개 — `/api/watch/**` (인증 없음)

| | 설명 |
|---|---|
| `GET  /api/watch/sources` | `published=1` 인 영상 목록 (제목·youtube_id·길이·클러스터 수·발행 숏폼 수) |
| `GET  /api/watch/sources/{id}` | 영상 + 클러스터(좋아요 순, 상태 포함) + **발행된** 클립만 |
| `POST /api/watch/sources/{id}/questions` | 질문 원문 insert 만. **외부 호출 0회.** → `{questionId}` (cluster_id 는 집계 후 채워진다) |
| `POST /api/watch/questions/{id}/like` | 토글. `unique(question_id, viewer_id)` 가 중복을 막는다 |
| `POST /api/watch/events` | `viewer_events` 적재 (§9) |
| `GET  /api/watch/clips/{id}/file` | 🔴 **`published_at is not null` 인 클립만.** 아니면 404 (403 아님 — 존재를 알리지 않는다) |

### 7-2. 스튜디오 — 기존 `/api/**` + 추가 (인증)

| | 설명 |
|---|---|
| `POST /api/sources/{id}/aggregate` | **[집계]** — 미분류 질문을 LLM(대표 문장) + 임베딩(소속)으로 묶는다(§5-3 a). 잡 반환. 스튜디오가 영상을 열 때 자동 호출 |
| `GET  /api/sources/{id}/clusters` | 클러스터 + 개별 질문 펼침 + 좋아요 + 상태. 미분류 질문은 따로 준다 |
| `POST /api/clusters/{id}/answer` | **파이프라인 실행** — 라우팅 → 검색 → rank → cut → judge → render. 잡 반환 |
| `PATCH /api/clusters/{id}` | 대표 문장 수정 · 상태 변경(거절 · 다시 열기) |
| `POST /api/clips/{id}/publish` · `/unpublish` | `published_at` 설정/해제 → 클러스터 상태 전이 |
| `PATCH /api/clips/{id}/parts` | in/out 미세 조정 → 재렌더 잡 |
| `POST /api/sources/{id}/publish` | `/watch` 목록에 영상 노출 |
| `GET  /api/sources/{id}/insights` | 성과(§9): 재생·CTA·원본 이동, 클러스터별 |

기존 `POST /api/sources/{id}/rank`(크리에이터 기준 프롬프트)는 **그대로 유지**된다(§2-2 6번).

### 7-3. 🔴 클립 파일 공개 조건

기존 `/api/clips/{id}/file` 은 인증 뒤에 남는다(스튜디오 프리뷰용). 공개 경로는 **별도 엔드포인트**
`/api/watch/clips/{id}/file` 이고 조건이 하나 더 붙는다. 같은 핸들러에 분기를 넣지 않는 이유:
`require_auth` 의존성이 라우트 단위라, 한 핸들러에서 "인증됐으면 전부 / 아니면 발행분만"을
분기하면 그 분기 하나가 유일한 울타리가 된다. 경로를 나누면 테스트가 단순해진다
(`test_api_auth.py` 에 "미발행 클립은 공개 경로에서 404" 한 줄).

### 7-4. 익명 시청자 · 레이트리밋

- 첫 방문에 `tease_viewer` 쿠키(UUID v4, httpOnly, 1년) 발급. 이게 `viewer_id`. 계정 없음.
- 🔴 쿠키를 지우면 새 사람이다 — 좋아요 중복 방지가 **완벽하지 않다.** 데모·초기엔 받아들인다.
  본격 운영은 로그인이든 기기 지문이든 다른 얘기다. 제출 문서에도 이 한계를 적는다.
- 공개 쓰기 레이트리밋: viewer_id 당 질문 분당 5 · 좋아요 분당 30, IP 당 그 3배. 메모리 카운터
  (`auth.py` 의 로그인 잠금과 같은 방식). 넘으면 429.
- 질문 길이 200자 상한. 비속어 필터는 넣지 않는다 — 크리에이터가 거절하면 된다.

---

## 8. 프론트

### 8-1. **[결정]** 앱 하나, 라우트 트리 둘

```
web/src/
  shared/    api 클라이언트 · 타입 · 훅 · 공통 CSS    ← 지금 있는 것 대부분
  studio/    크리에이터 콘솔                          ← 지금 ShortsPage 가 여기로
  watch/     모방 유튜브                              ← 새로
  App.tsx    라우터 (React.lazy 로 두 트리를 분리 로드)
```

두 앱으로 쪼개지 않는 이유는 하나 — **`types.ts` 와 `client.ts` 를 둘이 공유해야 하고 스키마가
아직 움직인다.** 앱을 나누면 복제(어긋남)하거나 npm workspaces(해커톤 중에 할 설정이 아님)다.
"시청자가 콘솔 코드까지 내려받는다"는 우려는 `React.lazy` 로 라우트를 나누면 사라진다.

**react-router 를 다시 넣는다.** 뺐을 때의 근거("화면 둘, 전환은 인증 상태")는 `/watch/:id` 같은
공유 가능한 URL 이 필요해지면서 사라졌다. 백엔드 catch-all 은 이미 `/studio`, `/watch/123`,
`/watch/123/shorts` 전부 index.html 을 준다 — 확인됨. 라우팅 때문에 고칠 백엔드는 없다.

### 8-2. 라우트

| | 화면 | 인증 |
|---|---|---|
| `/` | watch 홈 — 발행된 영상 카드 목록 | 없음 |
| `/watch/:sourceId` | 플레이어 + 질문 패널 + 숏폼 줄 | 없음 |
| `/studio` | 영상 목록 · 질문 TOP N | 로그인 |
| `/studio/sources/:id` | 파이프라인(기존 화면) + 클러스터 패널 + 발행 | 로그인 |
| `/studio/insights` | 성과 | 로그인 |

### 8-3. 유튜브 IFrame 플레이어와 계측

`/watch/:id` 의 원본은 **유튜브 IFrame Player API** 로 임베드한다(`youtube-nocookie.com`).
숏폼은 백엔드 mp4 를 `<video>` 로. 둘이 한 페이지에 공존한다.

- "이 구간부터 원본 보기" → `player.seekTo(t, true)` → 페이지를 **떠나지 않는다**
- 🔴 **이게 실제 유튜브 영상의 시청 시간이다.** IFrame API 의 `onStateChange(PLAYING)` 이
  `viewer_events.origin_play` 가 된다. 딥링크 클릭 카운터보다 훨씬 진짜에 가까운 유입 증거(§9).
- 원티드 영상은 임베드가 허용돼 있어야 한다 — 허락 요청 때 함께 확인한다.

---

## 9. 계측과 성과 — 정직한 주장

🔴 **"숏폼이 원본 유입을 늘린다"는 해커톤에서 증명할 수 없다.** 며칠짜리 데모에 그 데이터가
없고, 심사위원이 가장 먼저 찌를 지점이다.

**주장을 좁힌다: "우리는 유입 퍼널을 계측 가능하게 만들었다."** 그리고 실제로 만든다.

```
short_play ─► short_complete ─► cta_click ─► origin_seek ─► origin_play
   (숏폼 재생)    (끝까지)         (CTA 클릭)    (원본 이동)    (원본 재생 시작)
```

`viewer_events` 에 전부 쌓이고, `/studio/insights` 가 클러스터별 퍼널을 보여준다. 데모 중에
심사위원이 직접 누른 이벤트가 **실시간으로** 숫자에 반영되는 것을 보여준다. 증명되지 않은 성과를
주장하는 것보다 측정 장치를 만들었다는 게 강하고, 반박당하지 않는다. 유튜브 인수를 목표로
둔다면 더 그렇다 — 그쪽이 살 자산이 바로 이 계측이다.

---

## 10. 데모 계획

### 10-1. 소재

- **채널 원티드 「잡소리」 ep.45** (이직 방법 총정리, 21분) + **면접 편**(있다면). 🔴 **두 개여야**
  시리즈 횡단("면접 준비는요?" → "ep.N 에 있어요")이 보인다. 하나면 그 장면은 못 한다.
- 사용 허락과 함께 확인: 자동자막 품질(§5-1), 임베드 허용(§8-3).
- 허락 전에는 DB 에 있는 니체 강연 2건으로 개발한다 — 스키마와 로직은 영상이 뭐든 같다.

### 10-2. 시나리오 — 두 화면 나란히

1. `/watch` 에서 ep.45 를 튼다. 질문 패널에 미리 쌓인 질문들(좋아요 순).
2. 심사위원이 질문을 적는다 — **"급여 수준이 어떤가요?"** → 개별 질문으로 즉시 보인다(AI 0회).
   `/studio` 를 열면 [집계]가 돌아 기존 "희망연봉 얼마나…" 클러스터에 **합쳐지고**, "N명이 궁금해함" 이
   +1. (LLM 1회 + 임베딩 배치 1회 — 크리에이터 쪽에서만 돈다)
3. 발행된 숏폼 클릭 → 즉시 재생. **AI 호출 0.** 끝에 CTA → 같은 페이지 플레이어가 그 초로 이동.
4. `/studio` 로 전환. 방금 질문이 TOP N 에 있다. **"지금 이직해야 하는 상태인지"** 클러스터 →
   [답하기] → 수십 초 → 프리뷰: **챕터 2 + 챕터 3 조합**, 브릿지 카드, judge "자립 ✓ 답변 ✓".
5. [발행] → `/watch` 로 돌아가면 그 질문 옆에 숏폼이 붙어 있다. **루프가 닫혔다.**
6. `/watch` 에서 **"면접 준비는 어떻게 해요?"** → `/studio` 에서 [답하기] → **UNANSWERABLE**,
   "이 영상엔 없어요. ep.N 에서 다룹니다" (채널 검색). 가장 똑똑해 보이는 장면.
7. `/studio/insights` — 방금 심사위원이 누른 CTA 가 퍼널에 찍혀 있다.
8. 마지막에 **비용**: `stage_calls` 에서 "이 데모의 총 API 비용 N원, 질문당 평균 M원".

### 10-3. 테스트 질문 세트 — 유형별 최소 하나 (§3-3)

개발 중 이 세트로 θ(§5-3) · 라우팅(§5-4) · judge(§5-7) 를 튠한다. **"연봉" 하나로 만들면
사실 조회형에만 최적화된 시스템이 나오고, 조합·거절·채널 검색은 데모 당일에 처음 시험하게 된다.**

| 유형 | 질문 | 기대 |
|---|---|---|
| 사실 조회 | 희망연봉 얼마나 올려 불러요? | 단일 part, 검색 히트 |
| 절차 | 이직 기준을 어떻게 세워요? | 단일 part (챕터 1) |
| 자기 진단 | 지금 이직해야 하는 상태인지 어떻게 알아요? | **2 part 조합**, judge 통과 |
| 조건부 | 3년차인데 어떤 회사로 가요? | 단일 part, judge 가 "부분 답" 소견 |
| 시리즈 밖 | 면접 준비는요? | **UNANSWERABLE** + suggested_source |
| 하이라이트 | 핵심만 30초로 | **rank 경로**(검색 생략) |
| 검증 | 이거 진짜 효과 있어요? | 단일 part, 근거 대목 |
| 묶기 | 급여 수준이 어떤가요? / 돈 많이 줘요? | 사실 조회 클러스터에 **합류** |
| 묶기(거부) | 야근 많아요? | 새 클러스터 (연봉과 섞이면 θ 가 낮은 것) |

### 10-4. 리스크와 대비

| 리스크 | 대비 |
|---|---|
| 라이브 캐시 미스 — 심사위원이 예상 밖 질문 → 수십 초 대기 | 시나리오의 4·6 번만 라이브로 돌리고 나머지는 **미리 발행**. 미스는 "요청 등록" UX 라 원래 대기가 아님 |
| 워커 1개 — 두 [답하기] 가 겹침 | 큐 위치를 화면에 표시. 데모에선 한 번에 하나만 누른다 |
| judge 가 좋은 조합을 거부 | 실패해도 REVIEW 로 올라온다(경고 표시). 크리에이터가 발행할 수 있다 |
| Gemini 장애 | 발행된 것은 AI 없이 재생된다. 시나리오 1·3·5·7 은 살아 있다 |
| 유튜브 임베드 차단 | 허락 때 확인. 안 되면 `<video>` 로 원본도 직접 서빙(다운로드한 파일이 있다) |
| θ 튜닝 실패 — 묶기가 안 되거나 과하게 됨 | §10-3 세트로 사전 튠. 데모 2번은 확실히 붙는 문장으로 |

---

## 11. 해커톤 제출 — 기술 포인트

### 11-1. 쓰는 것 (왜 이것인지 한 줄씩)

- **임베딩 검색(RAG)** — 시청자 질문이 쿼리, 전사가 코퍼스. 질문 묶기와 같은 인프라. 채널 횡단.
- **구간 조합 + 브릿지** — Q&A 의 답은 흩어져 있다. 화면에 보이는 유일한 차별점.
- **LLM-as-judge + bounded retry** — cut 결과의 자립성·답변가능성. 사람 리뷰와 일치율 측정.
- **자막 우선 STT** — 대기 시간을 수 분 → 수 초로. (허락 후 품질 확인 조건부)
- **컨텍스트 캐싱 · 단어 타임스탬프** — 스키마에 이미 예약. 켜고 전후 측정.
- **시청자 유입 계측** — 퍼널 5단계, 실시간.

### 11-2. 이미 있고 쓰고 있는 것 (안 쓰면 손해)

- **중립/주관 분리 = 시맨틱 캐시 아키텍처** — "요청 건당 AI 호출" 대비 비용 우위의 *구조적* 근거
- **구조화 출력 + 화이트리스트 검증 + 재시도** — LLM 이 말한 idx 가 존재하는지, 초는 절대 LLM 을
  믿지 않고 `utterances` 에서 되찾는다
- **전 단계 계측** — LLM 만이 아니라 STT·렌더까지 토큰·지연·비용. "질문당 N원" 실측
- **워커 1개 = 동시 1건이 정책이 아니라 구조** — 2vCPU 에서 API 응답을 지키는 방법
- **fail-closed 인증 가드** — 외부 바인딩 + 비밀번호 없음이면 기동 거부

### 11-3. 안 쓴 것과 이유 (§5-9) — 이것도 적는다

화자 분리(속도와 충돌) · 인기 숏츠 이력 RAG(외부 데이터, 얇은 신호) · 에이전트 프레임워크(DAG)
· 영상 이해(강연은 화면에 정보가 없음) · 벡터 DB(N<1000). **판단이 있는 생략은 도입보다 높게
평가받는다.**

---

## 12. 개발 순서

파이프라인 본체는 있으므로 **① 이 제품의 뼈대 → ② 데모의 얼굴 → ③ 켜기만 하면 되는 것** 순.

1. **스키마 v9** (§6) + 마이그레이션 테스트. 기존 DB 에서 돌려 데이터 보존 확인.
2. **임베딩 레이어** (§5-3) — `embeddings` 적재, 질문 묶기, 세그먼트 검색. §10-3 세트로 θ 튠.
3. **공개 API** (§7-1) + 익명 쿠키 + 레이트리밋 + 🔴 발행 클립만 공개 테스트.
4. **`web/` 재배치** (§8-1) — shared/studio/watch, 라우터, 기존 화면을 `/studio` 로.
5. **`/watch`** — 목록 · 플레이어(iframe) · 질문 패널 · 숏폼 줄 · CTA · 이벤트 적재.
6. **rank 확장 + 조합 cut + judge** (§5-5 ~ 5-7). 단일 part 경로가 먼저 돌아가는지 확인 후 조합.
7. **`/studio` 확장** — 클러스터 패널 · [답하기] · 프리뷰 · [발행]/[거절] · insights.
8. **자막 우선 · 챕터 씨앗** (§5-1, 5-2) — 허락 후.
9. **캐싱 · 단어 타임스탬프 켜기** (§5-8) — 전후 측정.
10. 데모 리허설 (§10) — 두 영상, 질문 세트 전부, 미리 발행.

각 단계는 **결과를 눈으로 확인**하고 다음으로 간다. 컴파일/실행 성공 ≠ 완료(`CLAUDE.md`).

---

## 13. 미결 사항 — 권고 포함

| | 선택지 | 권고 | 근거 |
|---|---|---|---|
| Flow A / B 범위 | A 집중 · B 최소 / 둘 다 | **A 집중** | B 는 기존 스튜디오 + 버튼 몇 개로 80% 있다. 새 힘은 A 로 |
| 시청자 면 접근 | 완전 공개 / 링크 아는 사람만 | **완전 공개(익명 쿠키)** | 데모에 로그인이 끼면 흐름이 끊긴다. 공개 쓰기는 레이트리밋으로 |
| 묶기 임계값 θ | 0.80 ~ 0.90 | **0.85 시작, §10-3 으로 튠** | "야근 많아요?" 가 연봉에 안 붙고, "급여 수준" 은 붙는 지점 |
| 라우팅 판정 | LLM 분류 1회 / 규칙(의문사·길이) | **LLM 1회** | 규칙은 "핵심만"과 "핵심 논지가 뭐예요?" 를 못 가른다. 수십 토큰이라 비용 무시 가능. 둘 다 `runs.route` 에 남겨 비교 |
| 자막 우선 | 자막 → whisper 폴백 / whisper 만 | **허락 후 자막 품질 보고 결정** | 품질이 rank 를 흔들 정도면 whisper. 아니면 자막(대기 시간 ↓↓) |
| 30초 예산 | 30 / 45 / 60 | **30, 설정값** | 슬로건이 30초. 조건부·자기진단형은 넘칠 수 있어 설정으로 |
| 채널 횡단 범위 | 같은 `channel` / 전체 | **같은 channel** | 다른 채널 영상을 안내하면 유입이 밖으로 새고, 권리 문제가 있다 |
| 원본 재생 | iframe / 자체 `<video>` | **iframe** | 실제 유튜브 시청 시간이 된다(§8-3). 임베드 차단 시에만 자체 |
| 원격 레포 | ~~개인 transfer / 새 조직 / 새 레포~~ | **완료** — `HHsungmoon/shorts_maker` | 2026-09-04 transfer |

---

## 부록 — 용어

| | |
|---|---|
| 클러스터 | 같은 수요로 묶인 질문들. 작업 단위. 숏폼 하나로 답한다 |
| 대표 문장 `canonical_text` | 클러스터를 한 문장으로. `runs.criteria_prompt` 로 들어간다 |
| part | 조합 클립의 조각 하나. 단일 컷도 part 1개 |
| 브릿지 | part 사이 0.4초 카드. 점프를 읽히게 한다 |
| 검색 경로 / rank 경로 | 쿼리 의미가 있으면 임베딩 검색 후 rank, 없으면 전 세그먼트 rank(기존) |
| judge | cut 뒤 LLM 판정. 자립 + 답변. `clip_reviews(reviewer='llm')` |
| 발행 | `clips.published_at` 설정. 이때부터 시청자에게 보인다 |
| 퍼널 | short_play → short_complete → cta_click → origin_seek → origin_play |
