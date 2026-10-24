-- 010 — 원본마다 음성 인식 용어 힌트를 둔다. 2026-10-24.
--
-- whisper 의 initial_prompt 에 고유명사(학교 이름 · 전형 이름 · 고등학교 이름)를 미리 알려 주면 잘못
-- 알아듣는 일이 크게 준다. 지금까지는 CLI `sm stt run --prompt` 로만 넣을 수 있어서 화면에서 돌린
-- 음성 인식은 힌트 없이 돌았다. 원본에 목록으로 두고 화면에서 넣고 뺀다(http/studio.patch_source).
-- 음성 인식은 이 목록을 쉼표로 이어 initial_prompt 로 쓴다(pipeline/stt.run_for_source).
--
-- 🔴 문자열 하나가 아니라 **배열**이다. 화면은 용어 하나하나를 넣고 빼는데, 문자열로 두면 빼기가
-- 쉼표 자르기가 되고 용어 안의 쉼표에서 조용히 깨진다.

alter table sources add column stt_terms text[] not null default '{}';

-- 이미 힌트를 넣어 인식한 원본은 그 힌트를 목록으로 옮겨 둔다 — 마지막으로 힌트를 쓴 stt 호출 기준.
-- 안 옮기면 화면에 빈 목록이 보이고, 그대로 다시 인식하면 힌트 없이 돌아 전사가 오히려 나빠진다.
update sources s
set stt_terms = array(
    select distinct_terms.term
    from (
        select trim(t) as term, min(ord) as first_seen
        from unnest(string_to_array(last.prompt, ',')) with ordinality as u(t, ord)
        where trim(t) <> ''
        group by trim(t)
    ) as distinct_terms
    order by distinct_terms.first_seen
)
from (
    select distinct on (source_id) source_id, params ->> 'initial_prompt' as prompt
    from stage_calls
    where stage = 'stt' and coalesce(params ->> 'initial_prompt', '') <> ''
    order by source_id, created_at desc
) as last
where last.source_id = s.id;
