"""스키마 제약이 실제로 막는지 검증한다.

컬럼을 두는 것과 제약이 거는 것은 다르다. 문서 §5 의 "첫날에 반드시 넣을 것"은
코드가 실수해도 DB 가 막아야 의미가 있으므로, 그 방어선을 여기서 고정한다.

진짜 Postgres 의 `shorts_test` DB 를 쓴다(tests/support.py). 서버가 없으면 전부 skip 된다.
🔴 Postgres 는 문장 하나가 실패하면 **트랜잭션 전체가 abort** 된다 — 그 뒤의 문장은 전부
`InFailedSqlTransaction` 으로 튄다. 그래서 실패를 기대하는 문장은 `expect_integrity_error` 로
감싸 rollback 까지 한 번에 한다. `assertRaises` 를 직접 쓰면 다음 문장이 엉뚱한 예외로 죽는다.
"""

import unittest

import psycopg
from psycopg.types.json import Jsonb

from shorts_maker.db import store

from ..support import DbTestCase


class SchemaTestCase(DbTestCase):
    def expect_integrity_error(self, sql: str, params=()) -> None:
        """제약 위반을 기대하는 문장. 실패 뒤 트랜잭션을 되돌려 같은 연결을 계속 쓸 수 있게 한다."""
        with self.assertRaises(psycopg.errors.IntegrityError):
            self.conn.execute(sql, params)
        self.conn.rollback()

    def insert_source(self, **overrides) -> int:
        values = {
            "title": "테스트 강연",
            "content_type": "LECTURE",
            "path": "work/sources/a.mp4",
            "fingerprint": "sha256:aaa",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("%s" for _ in values)
        return self.conn.execute(
            f"insert into sources ({columns}) values ({placeholders}) returning id", tuple(values.values())
        ).fetchone()["id"]

    def insert_chain(self) -> tuple[int, int, int, int]:
        source_id = self.insert_source()
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 1800, 'c.mp4')"
            " returning id",
            (source_id,),
        ).fetchone()["id"]
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec,"
            " start_utterance_idx, end_utterance_idx) values (%s, 0, 0, 300, 0, 12) returning id",
            (chunk_id,),
        ).fetchone()["id"]
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]
        return source_id, chunk_id, segment_id, run_id

    def insert_clip(self, run_id: int, segment_id: int, start: float = 10, end: float = 45) -> int:
        return self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, %s, %s) returning id",
            (run_id, segment_id, start, end),
        ).fetchone()["id"]

    def insert_cluster(self, source_id: int, text: str = "왜 그런가요?") -> int:
        return self.conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, %s) returning id",
            (source_id, text),
        ).fetchone()["id"]

    def insert_question(self, source_id: int, text: str = "이건 왜죠?", viewer_id: str = "v1", cluster_id=None) -> int:
        return self.conn.execute(
            "insert into questions (source_id, text, viewer_id, cluster_id) values (%s, %s, %s, %s) returning id",
            (source_id, text, viewer_id, cluster_id),
        ).fetchone()["id"]


class ApplySchemaTest(SchemaTestCase):
    def test_apply_schema_is_idempotent(self):
        # 기동 때마다 apply_schema 가 돈다. 최신 DB 에 다시 돌려도 아무것도 바꾸면 안 된다.
        self.insert_source()
        self.conn.commit()
        before = store.existing_tables(self.conn)
        self.assertEqual(store.apply_schema(self.conn), store.SCHEMA_VERSION)
        self.assertEqual(store.apply_schema(self.conn), store.SCHEMA_VERSION)
        self.assertEqual(store.existing_tables(self.conn), before)
        self.assertEqual(store.schema_version(self.conn), store.SCHEMA_VERSION)
        self.assertEqual(store.row_counts(self.conn)["sources"], 1)
        versions = self.conn.execute("select count(*) as n from schema_version").fetchone()["n"]
        self.assertEqual(versions, len(store.migration_files()))

    def test_tables_constant_matches_the_database(self):
        # `sm db status` 와 테스트의 truncate 가 TABLES 를 돈다. 새 테이블을 만들고 여기 안 넣으면
        # 집계에서 빠지고 테스트 사이에 행이 새어 다닌다.
        self.assertEqual(set(store.TABLES), set(store.existing_tables(self.conn)) - {"schema_version"})


class ContentTypeTest(SchemaTestCase):
    def test_accepts_known_types(self):
        self.insert_source(content_type="LECTURE", fingerprint="sha256:1")
        self.insert_source(content_type="FILM", fingerprint="sha256:2")

    def test_rejects_unknown_type(self):
        self.expect_integrity_error(
            "insert into sources (title, content_type, path, fingerprint) values ('t', 'PODCAST', 'p', 'sha256:3')"
        )


class FingerprintTest(SchemaTestCase):
    def test_rejects_duplicate_fingerprint(self):
        self.insert_source(fingerprint="sha256:same")
        self.expect_integrity_error(
            "insert into sources (title, content_type, path, fingerprint) values ('t', 'LECTURE', 'p', 'sha256:same')"
        )


class ExcludedByTest(SchemaTestCase):
    SQL = (
        "insert into segments (chunk_id, idx, start_sec, end_sec, excluded_by,"
        " start_utterance_idx, end_utterance_idx) values (%s, 1, 300, 600, %s, 13, 20)"
    )

    def test_allows_null_and_known_values(self):
        _, chunk_id, _, _ = self.insert_chain()
        for idx, value in enumerate((None, "human", "auto")):
            with self.subTest(value=value):
                self.conn.execute(
                    "insert into segments (chunk_id, idx, start_sec, end_sec, excluded_by,"
                    " start_utterance_idx, end_utterance_idx) values (%s, %s, 300, 600, %s, 13, 20)",
                    (chunk_id, idx + 1, value),
                )

    def test_rejects_empty_string(self):
        # 문서 §5 가 명시적으로 경고한 실패 방식 — 빈 문자열로 사람/자동을 구분하면 안 된다.
        _, chunk_id, _, _ = self.insert_chain()
        self.expect_integrity_error(self.SQL, (chunk_id, ""))


class CriteriaPromptTest(SchemaTestCase):
    def test_allows_null(self):
        source_id = self.insert_source()
        self.conn.execute("insert into runs (source_id, criteria_prompt) values (%s, null)", (source_id,))

    def test_rejects_blank(self):
        # §6-3: 빈 지시문을 남기면 모델이 그걸 해석한다. 저장 단계에서 막는다.
        source_id = self.insert_source()
        self.conn.commit()  # rollback 이 source 까지 되돌리지 않게 먼저 굳힌다
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                self.expect_integrity_error(
                    "insert into runs (source_id, criteria_prompt) values (%s, %s)", (source_id, blank)
                )


class TimeRangeTest(SchemaTestCase):
    def test_rejects_non_positive_duration(self):
        _, chunk_id, _, _ = self.insert_chain()
        self.conn.commit()
        for start, end in ((300, 300), (300, 100)):
            with self.subTest(start=start, end=end):
                self.expect_integrity_error(
                    "insert into segments (chunk_id, idx, start_sec, end_sec,"
                    " start_utterance_idx, end_utterance_idx) values (%s, 9, %s, %s, 0, 1)",
                    (chunk_id, start, end),
                )


class SegmentRangeTest(SchemaTestCase):
    def test_rejects_reversed_utterance_range(self):
        _, chunk_id, _, _ = self.insert_chain()
        self.expect_integrity_error(
            "insert into segments (chunk_id, idx, start_sec, end_sec,"
            " start_utterance_idx, end_utterance_idx) values (%s, 5, 0, 10, 9, 3)",
            (chunk_id,),
        )

    def test_requires_utterance_range(self):
        # 발화 범위 없는 세그먼트는 [6] 이 초를 되찾을 근거가 없다 — 만들어지면 안 된다.
        _, chunk_id, _, _ = self.insert_chain()
        self.expect_integrity_error(
            "insert into segments (chunk_id, idx, start_sec, end_sec) values (%s, 6, 0, 10)",
            (chunk_id,),
        )


class StageCallTest(SchemaTestCase):
    STAGES = (
        "ping", "chunk", "stt", "segment", "describe", "rank", "cut", "render",
        "caption", "embed", "retrieve", "cluster", "judge", "classify", "download", "preview",
    )

    def test_rejects_unknown_stage(self):
        # 오타 난 stage 는 비용 집계를 조용히 쪼갠다.
        self.expect_integrity_error(
            "insert into stage_calls (stage, model) values ('ranking', 'gemini-flash-latest')"
        )

    def test_accepts_every_known_stage(self):
        # TEASE 로 늘어난 단계(caption·embed·retrieve·cluster·judge·classify·download·preview)까지
        # 전부 CHECK 에 있어야 한다. 하나라도 빠지면 그 단계의 기록이 통째로 실패한다.
        source_id = self.insert_source()
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                self.conn.execute(
                    "insert into stage_calls (source_id, stage, latency_ms) values (%s, %s, 1)", (source_id, stage)
                )
        self.assertEqual(store.row_counts(self.conn)["stage_calls"], len(self.STAGES))

    def test_allows_call_without_run_or_segment(self):
        # [3] 주제 분할은 run 이 생기기 전에 돌고 segment 도 아직 없다.
        source_id = self.insert_source()
        self.conn.execute(
            "insert into stage_calls (source_id, stage, model) values (%s, 'segment', 'gemini-flash-latest')",
            (source_id,),
        )

    def test_allows_non_llm_stage_without_model(self):
        # stt/render 는 LLM 이 아니다 — 이 행이 들어가야 §9-8 의 소요 시간 실측이 가능하다.
        source_id = self.insert_source()
        for stage in ("stt", "chunk", "render"):
            with self.subTest(stage=stage):
                self.conn.execute(
                    "insert into stage_calls (source_id, stage, latency_ms) values (%s, %s, 1234)",
                    (source_id, stage),
                )
        rows = self.conn.execute("select count(*) as n from stage_calls where model is null").fetchone()
        self.assertEqual(rows["n"], 3)

    def test_params_round_trip_as_a_dict(self):
        # jsonb 는 문자열이 아니라 dict 로 넣고 dict 로 읽는다. json.dumps 문자열을 넣으면
        # 따옴표에 싸인 문자열 하나가 저장돼 재현 파라미터를 못 읽는다.
        source_id = self.insert_source()
        self.conn.execute(
            "insert into stage_calls (source_id, stage, params) values (%s, 'stt', %s)",
            (source_id, Jsonb({"vad": True, "initial_prompt": "강연"})),
        )
        row = self.conn.execute("select params from stage_calls").fetchone()
        self.assertEqual(row["params"], {"vad": True, "initial_prompt": "강연"})

    def test_survives_source_deletion(self):
        # 소스를 지워도 비용 이력은 남아야 한다(on delete set null).
        source_id = self.insert_source()
        self.conn.execute(
            "insert into stage_calls (source_id, stage, model) values (%s, 'rank', 'gemini-flash-latest')",
            (source_id,),
        )
        self.conn.execute("delete from sources where id = %s", (source_id,))
        row = self.conn.execute("select source_id, stage from stage_calls").fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["source_id"])


class CascadeTest(SchemaTestCase):
    def test_deleting_source_removes_pipeline_rows(self):
        source_id, _, segment_id, run_id = self.insert_chain()
        self.insert_clip(run_id, segment_id)
        self.conn.execute("delete from sources where id = %s", (source_id,))
        counts = store.row_counts(self.conn)
        for table in ("chunks", "segments", "runs", "clips"):
            with self.subTest(table=table):
                self.assertEqual(counts[table], 0)

    def test_foreign_keys_are_enforced(self):
        # SQLite 시절엔 pragma 를 빼먹으면 조용히 통과했다. Postgres 는 항상 걸지만, 그래도 고정해 둔다.
        self.expect_integrity_error(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (9999, 0, 0, 10, 'x.mp4')"
        )


class ClipReviewTest(SchemaTestCase):
    def test_rejects_unknown_verdict(self):
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.insert_clip(run_id, segment_id)
        self.expect_integrity_error(
            "insert into clip_reviews (clip_id, verdict) values (%s, 'MAYBE')", (clip_id,)
        )

    def test_reviewer_defaults_to_human_and_accepts_llm(self):
        # judge(tease §5-7)의 판정도 같은 테이블에 쌓인다. 사람 판정과 구분되지 않으면 정확도를 못 잰다.
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.insert_clip(run_id, segment_id)
        self.conn.execute("insert into clip_reviews (clip_id, verdict) values (%s, 'OK')", (clip_id,))
        self.conn.execute("insert into clip_reviews (clip_id, verdict, reviewer) values (%s, 'NG', 'llm')", (clip_id,))
        rows = self.conn.execute("select reviewer from clip_reviews order by id").fetchall()
        self.assertEqual([r["reviewer"] for r in rows], ["human", "llm"])

    def test_rejects_unknown_reviewer(self):
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.insert_clip(run_id, segment_id)
        self.expect_integrity_error(
            "insert into clip_reviews (clip_id, verdict, reviewer) values (%s, 'OK', 'bot')", (clip_id,)
        )


class ClipUniquenessTest(SchemaTestCase):
    def test_rejects_a_second_clip_for_the_same_segment_in_one_run(self):
        # 버튼을 두 번 누르면 조용히 중복이 쌓이고 어느 게 최신인지 알 수 없게 된다.
        _, _, segment_id, run_id = self.insert_chain()
        self.insert_clip(run_id, segment_id)
        self.expect_integrity_error(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 20, 60)",
            (run_id, segment_id),
        )

    def test_allows_the_same_segment_in_a_different_run(self):
        # 기준을 바꿔 다시 돌린 Run 은 같은 구간을 다시 뽑을 수 있어야 한다(§6-2).
        source_id, _, segment_id, run_id = self.insert_chain()
        other_run = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]
        for rid in (run_id, other_run):
            self.insert_clip(rid, segment_id)


class ClipPartTest(SchemaTestCase):
    """조합 클립의 조각(tease §5-6). 단일 컷도 part 1개다."""

    def _insert_part(self, clip_id: int, ordinal: int) -> None:
        self.conn.execute(
            "insert into clip_parts (clip_id, ordinal, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
            " values (%s, %s, 0, 5, 0, 1)",
            (clip_id, ordinal),
        )

    def test_rejects_a_duplicate_ordinal_in_one_clip(self):
        # ordinal 이 겹치면 이어붙이는 순서가 정해지지 않는다.
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.insert_clip(run_id, segment_id)
        self._insert_part(clip_id, 0)
        self.expect_integrity_error(
            "insert into clip_parts (clip_id, ordinal, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
            " values (%s, 0, 5, 10, 2, 3)",
            (clip_id,),
        )

    def test_parts_go_with_their_clip(self):
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.insert_clip(run_id, segment_id)
        self._insert_part(clip_id, 0)
        self._insert_part(clip_id, 1)
        self.conn.execute("delete from clips where id = %s", (clip_id,))
        self.assertEqual(store.row_counts(self.conn)["clip_parts"], 0)


class QuestionTest(SchemaTestCase):
    """시청자 질문(tease §3-1)과 클러스터(§3-2)."""

    def test_rejects_blank_question_text(self):
        # 빈 질문은 묶을 것도 답할 것도 없는데 목록엔 한 줄 차지한다.
        source_id = self.insert_source()
        self.conn.commit()
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                self.expect_integrity_error(
                    "insert into questions (source_id, text, viewer_id) values (%s, %s, 'v1')", (source_id, blank)
                )

    def test_rejects_blank_canonical_text(self):
        # 대표 문장이 runs.criteria_prompt 로 들어간다 — 비면 rank 가 아무 기준도 없이 돈다.
        source_id = self.insert_source()
        self.conn.commit()
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                self.expect_integrity_error(
                    "insert into question_clusters (source_id, canonical_text) values (%s, %s)", (source_id, blank)
                )

    def test_cluster_status_is_one_of_the_transition_table(self):
        # update_plan §2-3 의 상태 전이표. 여기 없는 값이 들어가면 화면의 칸반이 그 행을 잃는다.
        source_id = self.insert_source()
        for status in ("OPEN", "IN_PROGRESS", "REVIEW", "PUBLISHED", "DECLINED", "UNANSWERABLE"):
            with self.subTest(status=status):
                self.conn.execute(
                    "insert into question_clusters (source_id, canonical_text, status) values (%s, 'q', %s)",
                    (source_id, status),
                )
        self.conn.commit()
        self.expect_integrity_error(
            "insert into question_clusters (source_id, canonical_text, status) values (%s, 'q', 'DONE')",
            (source_id,),
        )

    def test_deleting_a_cluster_orphans_its_questions_but_keeps_them(self):
        # 재집계는 클러스터를 지우고 다시 묶는다. 그때 시청자 원문이 같이 사라지면 안 된다.
        source_id = self.insert_source()
        cluster_id = self.insert_cluster(source_id)
        question_id = self.insert_question(source_id, cluster_id=cluster_id)
        self.conn.execute("delete from question_clusters where id = %s", (cluster_id,))
        row = self.conn.execute("select cluster_id from questions where id = %s", (question_id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["cluster_id"])

    def test_a_viewer_can_like_a_question_only_once(self):
        # 🔴 이게 없으면 좋아요 순위가 새로고침 연타로 조작된다.
        source_id = self.insert_source()
        question_id = self.insert_question(source_id)
        self.conn.execute(
            "insert into question_likes (question_id, viewer_id) values (%s, 'viewer-a')", (question_id,)
        )
        self.conn.execute(
            "insert into question_likes (question_id, viewer_id) values (%s, 'viewer-b')", (question_id,)
        )
        self.expect_integrity_error(
            "insert into question_likes (question_id, viewer_id) values (%s, 'viewer-a')", (question_id,)
        )


class EmbeddingTest(SchemaTestCase):
    """질문 묶기와 구간 검색이 공유하는 벡터 저장소(tease §5-3)."""

    VECTOR = b"\x00\x00\x80\x3f" * 4  # float32 1.0 x 4, little-endian

    def _insert(self, kind: str, ref_id: int, model: str) -> None:
        self.conn.execute(
            "insert into embeddings (kind, ref_id, model, dim, vector) values (%s, %s, %s, 4, %s)",
            (kind, ref_id, model, self.VECTOR),
        )

    def test_one_vector_per_target_and_model(self):
        # 같은 대상·같은 모델에 벡터가 둘이면 검색이 어느 걸 쓸지 정해지지 않는다. 모델을 바꾸면 새 행.
        self._insert("segment", 1, "text-embedding-004")
        self._insert("segment", 1, "gemini-embedding-001")
        self._insert("question", 1, "text-embedding-004")
        self.conn.commit()
        self.expect_integrity_error(
            "insert into embeddings (kind, ref_id, model, dim, vector) values ('segment', 1, 'text-embedding-004', 4, %s)",
            (self.VECTOR,),
        )

    def test_rejects_unknown_kind(self):
        self.expect_integrity_error(
            "insert into embeddings (kind, ref_id, model, dim, vector) values ('clip', 1, 'm', 4, %s)",
            (self.VECTOR,),
        )

    def test_vector_bytes_round_trip(self):
        # bytea 는 bytes 그대로 넣고 그대로 나온다 — numpy.frombuffer 가 이걸 전제한다.
        self._insert("cluster", 7, "m")
        row = self.conn.execute("select vector from embeddings").fetchone()
        self.assertEqual(bytes(row["vector"]), self.VECTOR)


class ViewerEventTest(SchemaTestCase):
    """유입 계측(tease §9). append-only 이고 원본이 사라져도 남는다."""

    def test_rejects_unknown_kind(self):
        self.expect_integrity_error(
            "insert into viewer_events (viewer_id, kind) values ('v1', 'scrolled')"
        )

    def test_survives_source_deletion(self):
        # "숏폼이 원본 유입을 늘린다"는 이 행 없이는 주장할 수 없다. 원본을 지워도 지표는 남아야 한다.
        source_id = self.insert_source()
        self.conn.execute(
            "insert into viewer_events (viewer_id, source_id, kind, payload) values ('v1', %s, 'origin_seek', %s)",
            (source_id, Jsonb({"position_sec": 812})),
        )
        self.conn.execute("delete from sources where id = %s", (source_id,))
        row = self.conn.execute("select source_id, kind, payload from viewer_events").fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["source_id"])
        self.assertEqual(row["payload"], {"position_sec": 812})


if __name__ == "__main__":
    unittest.main()
