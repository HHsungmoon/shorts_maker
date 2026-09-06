-- 002 — 임베딩에 task_type 을 넣고 유니크 키에 포함한다. 2026-09-06.
--
-- 🔴 같은 문장이라도 task_type 이 다르면 **다른 벡터**가 나온다. 그리고 하나의 대상이 두 가지
-- task_type 을 실제로 필요로 한다:
--   클러스터 대표 문장 = 질문 묶기에서는 'SEMANTIC_SIMILARITY'(문장끼리 대칭 비교),
--                        구간 검색(M5)에서는 'RETRIEVAL_QUERY'(짧은 질문 → 긴 설명, 비대칭).
-- 001 의 unique (kind, ref_id, model) 로는 이 둘이 같은 자리를 다투다 하나가 조용히 덮인다.
--
-- 001 을 고치지 않고 파일을 더하는 이유는 규약이다 — 적용된 마이그레이션은 손대지 않는다.
-- (SQLite 였다면 유니크 인덱스 교체가 테이블 재생성이었다. Postgres 라서 세 줄이다.)

alter table embeddings add column task_type text not null default 'SEMANTIC_SIMILARITY';

-- 001 의 `unique (kind, ref_id, model)` 은 인덱스가 아니라 **제약**으로 만들어졌다(테이블 정의 안에 썼다).
-- 이름은 Postgres 가 붙인 기본값이다: <테이블>_<컬럼들>_key.
alter table embeddings drop constraint embeddings_kind_ref_id_model_key;
alter table embeddings add constraint embeddings_kind_ref_id_model_task_key
    unique (kind, ref_id, model, task_type);
