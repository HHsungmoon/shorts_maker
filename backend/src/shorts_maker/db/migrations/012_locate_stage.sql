-- 012 — Jev 의 위치 찾기를 단계 목록에 넣는다. 2026-10-31.
--
-- jev 엔진(answers/proposals.py)은 답하기에서 Jev 를 두 가지로 부른다.
--   locate — 고른 구간의 대사 전체를 한 번 보내 "어느 줄이 답의 핵심인가" 를 묻는다. 답하기당 1회
--   judge  — 범위 하나를 판정한다. Gemini 판정기와 **같은 일**이라 같은 값을 쓴다. 답하기당 수십 회
--
-- 🔴 `locate` 를 `retrieve` 에 얹지 않는다. `retrieve` 는 임베딩 top-k 검색(answers/retrieval.py)이고
-- 값을 섞으면 "임베딩 검색에 얼마 썼나" 가 조용히 부푼다. 모델 이름(stage_calls.model)이 `jev-…` 라
-- 엔진은 그걸로 가른다(pricing.py 가 그 이름으로 단가를 고른다).

alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview'));
