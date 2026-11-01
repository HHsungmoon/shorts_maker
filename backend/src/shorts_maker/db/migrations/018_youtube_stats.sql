-- 018 — 유튜브 성과: 올린 숏폼의 지금 수치(Data API)와 분석(Analytics API). 2026-11-01. (tease §7-6)
--
-- 🔴 우리 시청자 퍼널(viewer_events)과 섞지 않는다. 시청자 페이지(/watch)는 없어질 수 있다 — 그때 이 테이블들만
-- 남아도 성과 탭이 돌아야 한다. 그래서 viewer_events 와 묶는 외래키·뷰를 두지 않는다.

-- ============================================================
-- clip_uploads.source_id — 올린 기록을 원본에 직접 묶는다
-- ============================================================
-- 클립은 "다시 추출" 로 통째로 지워지고 그때 clip_id 가 null 이 된다(013). 그러면 어느 회차의 숏폼인지 잃는데,
-- 성과 탭은 영상 페이지에 있어 원본으로 모은다. 영상은 채널에 남아 있으니 기록도 원본을 잃지 않아야 한다.
alter table clip_uploads add column source_id integer references sources (id) on delete set null;

update clip_uploads u
   set source_id = r.source_id
  from clips c
  join runs r on r.id = c.run_id
 where c.id = u.clip_id;

create index idx_clip_uploads_source on clip_uploads (source_id);

-- ============================================================
-- short_stats — 숏폼마다 최신 한 줄
-- ============================================================
-- 다시 가져오면 덮어쓴다. 지난 값을 쌓지 않는 이유: 날짜별 흐름은 short_stats_daily 가 들고 있고, "지난주에 본
-- 합계" 는 유튜브가 다시 내 주는 값이라 우리가 따로 보관할 이유가 없다.
create table short_stats (
    upload_id           integer primary key references clip_uploads (id) on delete cascade,

    -- 지금 수치 — Data API videos.list. 바로 반영된다.
    -- found = videos.list 가 돌려줬나. 거짓이면 유튜브에서 지웠거나 볼 수 없게 됐다(아래 값은 비어 있다).
    found               boolean     not null,
    -- 지금 공개 범위. 올릴 때 고른 값(clip_uploads.privacy)과 다를 수 있다 — 스튜디오에서 바꿀 수 있다.
    privacy             text,
    view_count          bigint,
    like_count          bigint,
    comment_count       bigint,

    -- 분석 합계 — Analytics reports.query(차원 없음, video==ID). 🔴 48~72시간 늦다.
    -- 🔴 Shorts 의 views 는 2025-03-31 부터 재생이 시작될 때마다(다시 재생 포함) 센다. 예전 방식이 engaged_views.
    views               bigint,
    engaged_views       bigint,
    minutes_watched     double precision,
    -- 초 · 퍼센트. 둘 다 유튜브가 준 값 그대로다 — 날짜별에서 다시 계산할 수 없다(가중 기준이 공개되지 않았다).
    avg_view_sec        double precision,
    avg_view_pct        double precision,
    likes               bigint,
    comments            bigint,
    shares              bigint,
    subscribers_gained  bigint,
    -- 분석 수치가 이 날(태평양 시간)까지다 — 날짜별 행의 마지막 날. 아직 처리된 날이 없으면 null.
    analytics_through   date,

    fetched_at          timestamptz not null default now()
);

-- ============================================================
-- short_stats_daily — 날짜별 분석
-- ============================================================
-- 🔴 day 는 태평양 시간의 날이다(Analytics 의 day 차원). 다시 가져오면 덮어쓴다 — 최근 며칠은 유튜브가 고친다.
create table short_stats_daily (
    upload_id           integer     not null references clip_uploads (id) on delete cascade,
    day                 date        not null,
    views               bigint,
    engaged_views       bigint,
    minutes_watched     double precision,
    avg_view_sec        double precision,
    avg_view_pct        double precision,
    likes               bigint,
    comments            bigint,
    shares              bigint,
    subscribers_gained  bigint,
    primary key (upload_id, day)
);

-- ============================================================
-- stage_calls — 성과 가져오기도 단계다
-- ============================================================
alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview',
     'upload', 'livechat', 'comment', 'reply', 'reaction', 'analytics'));
