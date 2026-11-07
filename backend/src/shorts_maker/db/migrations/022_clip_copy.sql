-- 022 — 숏폼 카피 후보도 단계다 (tease §5-10 "AI 카피 후보", 2026-11-08)
--
-- 편집 화면의 [AI 카피 후보] 가 클립 대사 · 제목 · 영상 개요 · 관리자 기준으로 카피 두 줄 후보 다섯을 받는다
-- (`pipeline/headlines.py`, Gemini 1회). 호출은 stage_calls 에 남긴다(문서 §12) — 토큰 · 비용이 원본의 비용에 들어간다.
--
-- 후보는 따로 저장하지 않는다. 사람이 고른 것만 `clips.layout.headline` 으로 남는다 — 후보 전체와 버린 이유는
-- stage_calls.response · params 에 있다.

alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview',
     'upload', 'livechat', 'comment', 'reply', 'reaction', 'analytics', 'scenes', 'focus', 'copy'));
