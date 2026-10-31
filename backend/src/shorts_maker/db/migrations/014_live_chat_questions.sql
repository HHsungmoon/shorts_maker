-- 014 — 라이브 채팅 다시보기에서 가져온 질문. 2026-10-31. (tease §3-5)
--
-- 질문의 출처가 둘이 된다: 우리 시청자 페이지(page)와 끝난 라이브의 채팅 다시보기(live_chat).
-- 쏘카 LIVE(96분) 채팅 464개 중 질문 꼴 약 91개(실측) — 페이지 질문만으로는 루프가 돌 재료가 없었다.

-- ============================================================
-- questions — 어디서 온 질문인가
-- ============================================================
-- 기존 행은 전부 우리 페이지에서 왔다. 기본값이 그 사실을 그대로 적는다.
alter table questions add column origin text not null default 'page'
    check (origin in ('page', 'live_chat'));

-- 바깥에서 온 질문의 원래 id(채팅 메시지 id). 🔴 이 값으로 중복을 막는다 — [가져오기] 를 몇 번 눌러도
-- 같은 메시지가 두 번 들어오지 않는다. 우리 페이지 질문은 바깥 id 가 없다.
alter table questions add column external_id text;
alter table questions add constraint questions_external_id_check
    check ((origin = 'page') = (external_id is null));
create unique index uq_questions_external on questions (source_id, origin, external_id)
    where external_id is not null;

-- 영상 안의 몇 초에 물었나(채팅이 달린 시각). 우리 페이지 질문은 영상 어디서 물었는지 모른다.
-- 라이브 Q&A 는 질문 뒤 몇 분 안에 답하는 일이 많아 답 구간 찾기의 힌트가 될 수 있다 — 지금은 저장만 한다.
alter table questions add column asked_at_sec double precision check (asked_at_sec >= 0);

-- ============================================================
-- stage_calls — 가져오기도 단계다(규약: 모든 단계를 기록한다)
-- ============================================================
alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview',
     'upload', 'livechat'));
