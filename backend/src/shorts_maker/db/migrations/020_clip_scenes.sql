-- 020 — 장면 나누기도 단계다 (tease §5-10 "장면마다 창", 2026-11-07)
--
-- 숏폼 편집이 클립 구간 안의 장면 경계를 ffmpeg 장면 점수로 잡는다(`pipeline/scenes.py`). LLM 호출은 없지만
-- 모든 단계는 stage_calls 에 남긴다(문서 §12) — 이 파이프라인의 실제 제약은 시간이다.
--
-- 경계 자체는 따로 저장하지 않는다. 사람이 고친 경계와 장면마다 창은 `clips.layout.windows` 에 이미 담긴다 —
-- 자동으로 잡은 경계를 따로 두면 "지금 쓰는 경계" 가 두 곳이 된다.

alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'locate', 'download', 'preview',
     'upload', 'livechat', 'comment', 'reply', 'reaction', 'analytics', 'scenes'));
