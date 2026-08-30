-- shorts_maker 스키마 v1
--
-- 문서 §5 의 데이터 모델. "첫날에 반드시 넣을 것" 5개는 컬럼만 두지 않고 **제약으로 강제**한다 --
-- 나중에 넣으면 비싼 것들이고, 코드가 실수해도 DB 가 막아야 의미가 있다.
--
-- 시간은 전부 UTC 문자열(`datetime('now')`). sqlite3 CLI 로 그대로 읽히는 게 개발 중엔 이득이다.

-- ============================================================
-- sources — 원본 영상 (구 Movie)
-- ============================================================
create table if not exists sources (
    id                  integer primary key,
    title               text    not null,

    -- 🔴 파이프라인 분기의 근거(§4). 나중에 넣으면 전 쿼리를 고쳐야 한다.
    content_type        text    not null check (content_type in ('LECTURE', 'FILM')),

    path                text    not null,
    duration_sec        real,

    -- 원본을 어디서 가져왔나(URL 또는 설명). 🔴 권리 확인이 미해결이라(§9-5) 출처를
    -- 잃으면 사후에 되찾을 방법이 없다. path 는 로컬 경로일 뿐 출처가 아니다.
    origin              text,

    -- 🔴 원본 중복 유입은 복구 불가한 데이터 오염이다(§5). 파일 내용 해시.
    fingerprint         text    not null unique,

    -- 줄거리 / 강연 개요. rank 의 자립성 판단에 들어간다(§4-[5]).
    context             text,

    -- backend admins.id. 서비스가 분리돼 있으므로 FK 가 아니라 숫자만 들고 있는다(§13).
    created_by_admin_id integer,

    status              text    not null default 'PENDING'
                                check (status in ('PENDING', 'RUNNING', 'DONE', 'FAILED')),
    error               text,
    created_at          text    not null default (datetime('now')),
    updated_at          text    not null default (datetime('now'))
);

-- ============================================================
-- chunks — 분할 조각
-- ============================================================
-- LECTURE 는 소스당 1개(전체 구간)라 사실상 항등이다. 그래도 지금 만드는 이유는
-- segments 의 부모를 나중에 바꾸면 전 행을 옮겨야 하기 때문이다(§5).
create table if not exists chunks (
    id        integer primary key,
    source_id integer not null references sources (id) on delete cascade,
    idx       integer not null,
    start_sec real    not null,
    end_sec   real    not null,
    path      text    not null,
    unique (source_id, idx),
    check (end_sec > start_sec)
);

-- ============================================================
-- utterances — STT/자막 원문 (§4-[1])
-- ============================================================
-- 문서 §5 에는 없던 테이블이다. 발화를 segments.dialogue JSON 안에만 두면 [3] 이 쓰는
-- 인덱스(청크 기준)와 [6] 이 쓰는 인덱스(세그먼트 기준)가 서로 다른 좌표계가 되고,
-- §12 의 "LLM 출력 화이트리스트 검증"을 SQL 로 못 한다. 발화를 1급 행으로 두면
-- 두 단계 모두 "존재하는 idx 중 하나를 고르는 문제"가 되고 검증이 select 한 방이다.
--
-- 🔴 start_sec/end_sec 은 **소스 절대 초**다. 청크 로컬 시간이 아니다.
create table if not exists utterances (
    id              integer primary key,
    chunk_id        integer not null references chunks (id) on delete cascade,
    idx             integer not null,
    start_sec       real    not null,
    end_sec         real    not null,
    text            text    not null,

    -- word-level 타임스탬프 JSON. [6] 이 발화 중간에서 자르게 될 때 필요해진다.
    words           text,

    -- whisper 의 환각은 avg_logprob 이 낮거나 no_speech_prob 이 높은 구간에서 나온다.
    -- 지금 안 모으면 나중에 되돌려 볼 수 없는 관측값이라 같이 저장한다(§5 기준 ③).
    avg_logprob     real,
    no_speech_prob  real,

    created_at      text    not null default (datetime('now')),
    unique (chunk_id, idx),
    check (end_sec > start_sec)
);

create index if not exists idx_utterances_chunk on utterances (chunk_id);

-- ============================================================
-- segments — 중립 자산 (구 Scene)
-- ============================================================
create table if not exists segments (
    id              integer primary key,
    chunk_id        integer not null references chunks (id) on delete cascade,
    idx             integer not null,
    start_sec       real    not null,
    end_sec         real    not null,

    -- LECTURE: 주제 요약([3]에서 함께 생성) / FILM: 화면 해설([4])
    -- 🔴 기준 중립적으로 써야 한다(§3). 특정 기준이 들어가면 중립 자산이 아니다.
    description     text,

    -- 🔴 발화를 복사하지 않고 **범위로 참조**한다(§9-11 결정). utterances 가 정본이라
    -- 복사하면 중복이고, [6] 이 발화 인덱스를 다룰 때 좌표계가 다시 갈라진다.
    -- 아래 start_sec/end_sec 은 이 범위에서 파생된 값이다 — 생성 시점에 함께 쓰고 이후
    -- 수정하지 않는다는 불변식으로 유지한다(읽기 편의를 위한 비정규화).
    start_utterance_idx integer not null,
    end_utterance_idx   integer not null,

    describe_model  text,
    -- 실패를 null 로 두면 "아직 안 함"과 구분이 안 돼 조용히 누락된다(§5).
    describe_failed integer not null default 0 check (describe_failed in (0, 1)),

    -- 🔴 빈 문자열로 사람/자동을 구분하면 파싱 실패 시 영구히 안 풀린다(§5).
    -- null = 제외 안 됨. '' 는 이 check 가 막는다.
    excluded_by     text    check (excluded_by is null or excluded_by in ('human', 'auto')),
    excluded_reason text,

    created_at      text    not null default (datetime('now')),
    unique (chunk_id, idx),
    check (end_sec > start_sec),
    check (end_utterance_idx >= start_utterance_idx)
);

create index if not exists idx_segments_chunk on segments (chunk_id);

-- ============================================================
-- runs — 주관 (뽑기 1회)
-- ============================================================
create table if not exists runs (
    id                    integer primary key,
    source_id             integer not null references sources (id) on delete cascade,

    -- 🔴 §6-3: 기준이 비면 그 줄 자체를 프롬프트에서 뺀다. 빈 문자열을 저장하면
    -- "기준 없음"과 "빈 지시문"이 구분되지 않으므로 null 만 허용한다.
    criteria_prompt       text    check (criteria_prompt is null or length(trim(criteria_prompt)) > 0),

    -- 🔴 실제로 보낸 프롬프트 전문. criteria_prompt 는 §6-1 의 [가변] 부분만이라
    -- [고정] 관문/출력형식을 튜닝하면 run 끼리 비교가 무의미해진다. 그때 무엇을 물었는지는
    -- 저장해두지 않으면 복원할 수 없고, §11 품질 측정의 전제가 무너진다.
    prompt                text,

    -- 순위·점수 원문 + 파싱 결과 JSON. 원문을 함께 남겨야 파싱 실패를 사후에 고칠 수 있다.
    ranked                text,

    requested_by_admin_id integer,
    status                text    not null default 'PENDING'
                                  check (status in ('PENDING', 'RUNNING', 'DONE', 'FAILED')),
    error                 text,
    created_at            text    not null default (datetime('now')),
    updated_at            text    not null default (datetime('now'))
);

create index if not exists idx_runs_source on runs (source_id);

-- ============================================================
-- clips — 완성 클립
-- ============================================================
create table if not exists clips (
    id         integer primary key,
    run_id     integer not null references runs (id) on delete cascade,
    segment_id integer not null references segments (id) on delete cascade,
    start_sec  real    not null,
    end_sec    real    not null,
    score      real,
    reason     text,
    path       text,
    -- 렌더는 편당 재개가 가능해야 한다 — 실패해도 앞 단계를 다시 돌리지 않는다.
    rendered   integer not null default 0 check (rendered in (0, 1)),
    created_at text    not null default (datetime('now')),
    check (end_sec > start_sec)
);

create index if not exists idx_clips_run on clips (run_id);

-- 🔴 한 Run 안에서 같은 구간으로 클립을 두 번 만들지 않는다. 버튼을 두 번 누르면 조용히
-- 중복이 쌓이고, 어느 게 최신인지 알 수 없게 된다. 다시 만들려면 기존 것을 지우고 만든다.
-- 제약(constraint) 이 아니라 유니크 인덱스인 이유: SQLite 는 기존 테이블에 제약을 못 붙이지만
-- 인덱스는 붙일 수 있어서, 이미 쌓인 데이터를 날리지 않고 올릴 수 있다.
create unique index if not exists uq_clips_run_segment on clips (run_id, segment_id);

-- ============================================================
-- clip_reviews — 품질 측정 (§11)
-- ============================================================
-- append-only 로그다. (clip_id, admin_id) 유니크를 걸지 않는 이유: CLI 단계에서는
-- admin_id 가 null 인데 SQLite 는 유니크에서 null 을 서로 다르게 보므로 제약이 헛돈다.
-- 판정 이력이 남는 게 §11 목적에도 맞다 — 읽을 때 최신 행을 쓴다.
create table if not exists clip_reviews (
    id         integer primary key,
    clip_id    integer not null references clips (id) on delete cascade,
    admin_id   integer,
    verdict    text    not null check (verdict in ('OK', 'NG')),
    note       text,
    created_at text    not null default (datetime('now'))
);

create index if not exists idx_clip_reviews_clip on clip_reviews (clip_id);

-- ============================================================
-- stage_calls — 계측 (§12: 예외 없이 전 단계 기록)
-- ============================================================
-- 🔴 LLM 호출만이 아니라 **모든 단계**를 기록한다. §7 은 이 파이프라인의 실제 제약이
-- 비용이 아니라 **시간**이라고 적어놨는데(STT CPU 시간), LLM 호출만 기록하면 stt·render
-- 같은 비-LLM 단계의 소요 시간이 어디에도 안 남는다 — §9-8 의 실측을 할 수가 없다.
-- LLM 단계는 model/토큰이 추가로 채워지고, 나머지는 null 이다.
--
-- 세 참조가 전부 nullable 인 이유: [3] 주제 분할은 run 이 생기기 전에 돌고 segment 도
-- 아직 없다. source_id 만 있는 호출이 정상이다.
-- on delete set null 인 이유: 소스를 지워도 비용/시간 이력은 남아야 한다. 지금 안 모으면
-- 나중에 처음부터 다시 모아야 하는 종류의 데이터다(§5).
create table if not exists stage_calls (
    id              integer primary key,
    source_id       integer references sources (id) on delete set null,
    run_id          integer references runs (id) on delete set null,
    segment_id      integer references segments (id) on delete set null,

    -- 오타가 나면 집계가 조용히 쪼개지므로 값을 고정한다.
    -- stt/chunk/render 는 LLM 이 아니다 — model 과 토큰이 null 인 게 정상이다.
    stage           text    not null check (stage in
                            ('ping', 'chunk', 'stt', 'segment', 'describe', 'rank', 'cut', 'render')),

    model           text,
    input_tokens    integer,
    output_tokens   integer,
    -- 🔴 사고 토큰은 출력 단가로 과금된다. 비용이 튀는 지점이라 따로 센다(§7).
    thinking_tokens integer,
    latency_ms      integer,

    -- 그 단계를 무엇으로 돌렸나 (JSON). stt 면 initial_prompt·vad, LLM 이면 온도 같은 것.
    -- 🔴 STT 는 initial_prompt 하나로 결과가 눈에 띄게 달라진다(고유명사 교정). 무엇으로
    -- 뽑은 전사인지 남기지 않으면 변형끼리 비교가 불가능하고 재현도 안 된다 — runs.prompt 와
    -- 같은 이유다.
    params          text,

    error           text,
    created_at      text    not null default (datetime('now'))
);

create index if not exists idx_stage_calls_source on stage_calls (source_id);
create index if not exists idx_stage_calls_run    on stage_calls (run_id);
create index if not exists idx_stage_calls_stage  on stage_calls (stage);
