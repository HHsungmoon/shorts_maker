-- 004 — 클립에 제목. 2026-09-08.
--
-- 시청자 화면의 숏폼 목록에는 **질문에서 나온 클립과 그렇지 않은 클립이 함께** 올라간다.
-- 지금까지는 질문(클러스터 대표 문장)을 카드의 제목처럼 쓰고 있었는데, 크리에이터가 자기 기준으로
-- 뽑은 클립에는 질문이 없어서 제목 자리가 비었다.
--
-- 🔴 제목을 파생값으로 두지 않고 컬럼으로 둔다. 크리에이터가 고칠 수 있어야 하기 때문이다 —
-- 질문 문장이 그대로 좋은 제목인 경우도 있지만 대개는 다듬고 싶어진다.

alter table clips add column title text;

-- 기존 클립의 기본값: 질문에서 나온 것은 그 질문, 아니면 구간 설명.
-- 구간 설명은 "…를 설명한다" 형태라 제목으로 완벽하지 않지만, 빈 칸보다는 낫고 고칠 수 있다.
update clips c
   set title = coalesce(
         (select qc.canonical_text from question_clusters qc where qc.id = c.question_cluster_id),
         (select sg.description from segments sg where sg.id = c.segment_id)
       )
 where c.title is null;
