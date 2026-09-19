-- 009 — 영상 개요 초안을 단계 목록에 넣는다. 2026-09-19.
--
-- `sources.context`(프롬프트 3층)를 구간 요약에서 LLM 이 한 번에 써 준다(pipeline/overview.py).
-- 그 호출도 `stage_calls` 에 남아야 한다 — 규약은 "모든 단계를 기록한다" 이고 예외가 없다.
--
-- 🔴 **`describe` 를 재활용하지 않는다.** 뜻이 다르다: `describe` 는 구간 **하나하나**의 해설이고
-- (FILM 에서 쓴다) `overview` 는 영상 **전체**를 한 문장으로 줄인 것이다. 같은 값으로 적으면
-- "구간 해설에 얼마 썼나" 가 조용히 부풀고, 나중에 FILM 을 켤 때 그 둘을 되가를 방법이 없다.
-- 오타가 집계를 쪼개는 것을 막으려고 값을 CHECK 로 고정해 둔 표라, 새 값은 여기서 늘린다.
--
-- 회차 비용 리포트(answers/report.py)는 단계 목록이 아니라 `run_id` 로 준비/답변을 가르므로
-- 이 값은 자동으로 "회차 준비" 쪽에 들어간다. 고칠 곳이 더 없다.

alter table stage_calls drop constraint stage_calls_stage_check;

alter table stage_calls add constraint stage_calls_stage_check check (stage in
    ('ping', 'chunk', 'stt', 'segment', 'describe', 'overview', 'rank', 'cut', 'render',
     'caption', 'embed', 'retrieve', 'cluster', 'judge', 'classify', 'download', 'preview'));
