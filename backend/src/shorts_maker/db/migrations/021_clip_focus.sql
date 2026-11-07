-- 021 — 장면마다 사람 찾기(AI 초점)도 단계다 (tease §5-10 "포커스 규칙", 2026-11-07)
--
-- 숏폼 편집이 장면마다 프레임 세 장을 Gemini 에 보내 사람 · 머리 상자와 말하는 사람을 받는다(`pipeline/focus.py`).
-- 창 좌표는 코드가 규칙으로 계산한다. 호출은 stage_calls 에 남긴다(문서 §12) — 토큰 · 비용이 원본의 비용에 들어간다.
--
-- 찾은 사람 상자는 따로 저장하지 않는다. 결과는 `clips.layout.windows` 의 창(by = "ai")으로만 남는다 —
-- 근거(상자 · 말하는 사람)는 stage_calls.params 에 있다.

alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview',
     'upload', 'livechat', 'comment', 'reply', 'reaction', 'analytics', 'scenes', 'focus'));
