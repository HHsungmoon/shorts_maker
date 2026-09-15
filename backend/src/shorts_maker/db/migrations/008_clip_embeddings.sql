-- 008 — 클립도 임베딩한다. 2026-09-15.
--
-- 질문을 남기는 순간 **이미 발행된 숏폼 중 비슷한 궁금증에 답한 것**을 추천한다(update_plan D13,
-- answers/suggest.py). 그러려면 발행된 클립마다 "무엇에 답하는가" 를 벡터로 들고 있어야 한다.
--
-- 🔴 벡터로 만드는 글은 **클립의 실제 대사**다. 제목이나 구간 설명이 아니다. 실측(2026-09-15,
-- 질문 8 · 발행 숏폼 2)에서 제목·구간 설명은 맞는 연결과 틀린 연결의 틈이 음수였고 대사만 +0.015 였다.
-- 용도는 검색이라 task_type 은 RETRIEVAL_DOCUMENT 다(질문 쪽이 RETRIEVAL_QUERY).
--
-- kind 를 새로 여는 것뿐이다. 키(kind, ref_id, model, task_type)와 정규화 규칙은 그대로 쓴다.
-- 🔴 적용된 파일은 고치지 않는다 — 001 의 CHECK 를 여기서 갈아 끼운다.

alter table embeddings drop constraint embeddings_kind_check;
alter table embeddings add constraint embeddings_kind_check
    check (kind in ('segment', 'question', 'cluster', 'clip'));
