"""스키마 제약이 실제로 막는지 검증한다.

컬럼을 두는 것과 제약이 거는 것은 다르다. 문서 §5 의 "첫날에 반드시 넣을 것"은
코드가 실수해도 DB 가 막아야 의미가 있으므로, 그 방어선을 여기서 고정한다.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from shorts_maker.db import store


class SchemaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self._dir.name) / "test.db")
        store.apply_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self._dir.cleanup()

    def insert_source(self, **overrides) -> int:
        values = {
            "title": "테스트 강연",
            "content_type": "LECTURE",
            "path": "work/sources/a.mp4",
            "fingerprint": "sha256:aaa",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self.conn.execute(
            f"insert into sources ({columns}) values ({placeholders})", tuple(values.values())
        )
        return cursor.lastrowid

    def insert_chain(self) -> tuple[int, int, int, int]:
        source_id = self.insert_source()
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (?, 0, 0, 1800, 'c.mp4')",
            (source_id,),
        ).lastrowid
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec,"
            " start_utterance_idx, end_utterance_idx) values (?, 0, 0, 300, 0, 12)",
            (chunk_id,),
        ).lastrowid
        run_id = self.conn.execute(
            "insert into runs (source_id) values (?)", (source_id,)
        ).lastrowid
        return source_id, chunk_id, segment_id, run_id


class ApplySchemaTest(SchemaTestCase):
    def test_refuses_in_place_upgrade(self):
        # schema.sql 은 전부 `if not exists` 라 컬럼 추가가 안 된다. 구버전 DB 에 그냥
        # 적용하면 컬럼 없이 버전만 올라가 스키마와 버전이 어긋난다 — 조용히 굴러가면 안 된다.
        self.conn.execute("delete from schema_version")
        self.conn.execute("insert into schema_version (version) values (1)")
        with self.assertRaises(store.SchemaError):
            store.apply_schema(self.conn)

    def test_migrates_in_place_without_losing_rows(self):
        # STT 한 번에 수 분이 든다. 컬럼 하나 추가하려고 전사를 날리면 안 된다.
        self.insert_source()
        self.conn.execute("delete from schema_version")
        self.conn.execute("insert into schema_version (version) values (5)")
        self.conn.commit()
        self.assertEqual(store.apply_schema(self.conn), store.SCHEMA_VERSION)
        self.assertEqual(store.row_counts(self.conn)["sources"], 1)
        self.conn.execute("update sources set language = 'en'")

    def test_rerunning_a_half_applied_migration_is_safe(self):
        # ALTER 는 됐는데 버전 기록 전에 죽은 상태. 다시 돌려도 막히면 안 된다.
        self.conn.execute("delete from schema_version")
        self.conn.execute("insert into schema_version (version) values (5)")
        self.conn.commit()
        store.apply_schema(self.conn)
        self.conn.execute("delete from schema_version")
        self.conn.execute("insert into schema_version (version) values (5)")
        self.conn.commit()
        self.assertEqual(store.apply_schema(self.conn), store.SCHEMA_VERSION)

    def test_is_idempotent_at_current_version(self):
        self.assertEqual(store.apply_schema(self.conn), store.SCHEMA_VERSION)

    def test_drops_the_backend_admin_columns_without_losing_rows(self):
        # v8 은 backend(Spring) 의 admins.id 를 담던 컬럼 3개를 지운다. 서비스가 독립하면서
        # 참조할 곳이 없어진 값이지만, 그걸 지우자고 전사(수 분짜리)를 날릴 수는 없다.
        self.insert_source()
        for table, column in (
            ("sources", "created_by_admin_id"),
            ("runs", "requested_by_admin_id"),
            ("clip_reviews", "admin_id"),
        ):
            self.conn.execute(f"alter table {table} add column {column} integer")
        self.conn.execute("delete from schema_version")
        self.conn.execute("insert into schema_version (version) values (7)")
        self.conn.commit()

        self.assertEqual(store.apply_schema(self.conn), 8)
        self.assertEqual(store.row_counts(self.conn)["sources"], 1)
        for table in ("sources", "runs", "clip_reviews"):
            columns = [r[1] for r in self.conn.execute(f"pragma table_info({table})")]
            self.assertEqual([c for c in columns if "admin" in c], [], table)

    def test_a_fresh_db_has_no_admin_columns_either(self):
        # 새로 만든 DB 와 마이그레이션한 DB 의 스키마가 갈리면 한쪽에서만 나는 버그가 생긴다.
        for table in ("sources", "runs", "clip_reviews"):
            columns = [r[1] for r in self.conn.execute(f"pragma table_info({table})")]
            self.assertEqual([c for c in columns if "admin" in c], [], table)


class ContentTypeTest(SchemaTestCase):
    def test_accepts_known_types(self):
        self.insert_source(content_type="LECTURE", fingerprint="sha256:1")
        self.insert_source(content_type="FILM", fingerprint="sha256:2")

    def test_rejects_unknown_type(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_source(content_type="PODCAST", fingerprint="sha256:3")


class FingerprintTest(SchemaTestCase):
    def test_rejects_duplicate_fingerprint(self):
        self.insert_source(fingerprint="sha256:same")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_source(fingerprint="sha256:same")


class ExcludedByTest(SchemaTestCase):
    def _insert_segment(self, excluded_by):
        _, chunk_id, _, _ = self.insert_chain()
        self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, excluded_by,"
            " start_utterance_idx, end_utterance_idx) values (?, 1, 300, 600, ?, 13, 20)",
            (chunk_id, excluded_by),
        )

    def test_allows_null_and_known_values(self):
        for value in (None, "human", "auto"):
            with self.subTest(value=value):
                self.setUp()
                self._insert_segment(value)

    def test_rejects_empty_string(self):
        # 문서 §5 가 명시적으로 경고한 실패 방식 — 빈 문자열로 사람/자동을 구분하면 안 된다.
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_segment("")


class CriteriaPromptTest(SchemaTestCase):
    def test_allows_null(self):
        source_id = self.insert_source()
        self.conn.execute("insert into runs (source_id, criteria_prompt) values (?, null)", (source_id,))

    def test_rejects_blank(self):
        # §6-3: 빈 지시문을 남기면 모델이 그걸 해석한다. 저장 단계에서 막는다.
        source_id = self.insert_source()
        for blank in ("", "   "):
            with self.subTest(blank=repr(blank)):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(
                        "insert into runs (source_id, criteria_prompt) values (?, ?)", (source_id, blank)
                    )


class TimeRangeTest(SchemaTestCase):
    def test_rejects_non_positive_duration(self):
        _, chunk_id, _, _ = self.insert_chain()
        for start, end in ((300, 300), (300, 100)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(
                        "insert into segments (chunk_id, idx, start_sec, end_sec,"
                        " start_utterance_idx, end_utterance_idx) values (?, 9, ?, ?, 0, 1)",
                        (chunk_id, start, end),
                    )


class SegmentRangeTest(SchemaTestCase):
    def test_rejects_reversed_utterance_range(self):
        _, chunk_id, _, _ = self.insert_chain()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec,"
                " start_utterance_idx, end_utterance_idx) values (?, 5, 0, 10, 9, 3)",
                (chunk_id,),
            )

    def test_requires_utterance_range(self):
        # 발화 범위 없는 세그먼트는 [6] 이 초를 되찾을 근거가 없다 — 만들어지면 안 된다.
        _, chunk_id, _, _ = self.insert_chain()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec) values (?, 6, 0, 10)",
                (chunk_id,),
            )


class StageCallTest(SchemaTestCase):
    def test_rejects_unknown_stage(self):
        # 오타 난 stage 는 비용 집계를 조용히 쪼갠다.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into stage_calls (stage, model) values ('ranking', 'gemini-flash-latest')"
            )

    def test_allows_call_without_run_or_segment(self):
        # [3] 주제 분할은 run 이 생기기 전에 돌고 segment 도 아직 없다.
        source_id = self.insert_source()
        self.conn.execute(
            "insert into stage_calls (source_id, stage, model) values (?, 'segment', 'gemini-flash-latest')",
            (source_id,),
        )

    def test_allows_non_llm_stage_without_model(self):
        # stt/render 는 LLM 이 아니다 — 이 행이 들어가야 §9-8 의 소요 시간 실측이 가능하다.
        source_id = self.insert_source()
        for stage in ("stt", "chunk", "render"):
            with self.subTest(stage=stage):
                self.conn.execute(
                    "insert into stage_calls (source_id, stage, latency_ms) values (?, ?, 1234)",
                    (source_id, stage),
                )
        rows = self.conn.execute("select count(*) as n from stage_calls where model is null").fetchone()
        self.assertEqual(rows["n"], 3)

    def test_survives_source_deletion(self):
        # 소스를 지워도 비용 이력은 남아야 한다(on delete set null).
        source_id = self.insert_source()
        self.conn.execute(
            "insert into stage_calls (source_id, stage, model) values (?, 'rank', 'gemini-flash-latest')",
            (source_id,),
        )
        self.conn.execute("delete from sources where id = ?", (source_id,))
        row = self.conn.execute("select source_id, stage from stage_calls").fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["source_id"])


class CascadeTest(SchemaTestCase):
    def test_deleting_source_removes_pipeline_rows(self):
        source_id, _, segment_id, run_id = self.insert_chain()
        self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (?, ?, 10, 45)",
            (run_id, segment_id),
        )
        self.conn.execute("delete from sources where id = ?", (source_id,))
        counts = store.row_counts(self.conn)
        for table in ("chunks", "segments", "runs", "clips"):
            with self.subTest(table=table):
                self.assertEqual(counts[table], 0)

    def test_foreign_keys_are_enforced(self):
        # 🔴 pragma foreign_keys 를 빼먹으면 이 테스트만 조용히 통과하지 않는다.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (9999, 0, 0, 10, 'x.mp4')"
            )


class ClipReviewTest(SchemaTestCase):
    def test_rejects_unknown_verdict(self):
        _, _, segment_id, run_id = self.insert_chain()
        clip_id = self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (?, ?, 10, 45)",
            (run_id, segment_id),
        ).lastrowid
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into clip_reviews (clip_id, verdict) values (?, 'MAYBE')", (clip_id,)
            )


if __name__ == "__main__":
    unittest.main()


class ClipUniquenessTest(SchemaTestCase):
    def test_rejects_a_second_clip_for_the_same_segment_in_one_run(self):
        # 버튼을 두 번 누르면 조용히 중복이 쌓이고 어느 게 최신인지 알 수 없게 된다.
        _, _, segment_id, run_id = self.insert_chain()
        self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (?, ?, 10, 45)",
            (run_id, segment_id),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec) values (?, ?, 20, 60)",
                (run_id, segment_id),
            )

    def test_allows_the_same_segment_in_a_different_run(self):
        # 기준을 바꿔 다시 돌린 Run 은 같은 구간을 다시 뽑을 수 있어야 한다(§6-2).
        source_id, _, segment_id, run_id = self.insert_chain()
        other_run = self.conn.execute(
            "insert into runs (source_id) values (?)", (source_id,)
        ).lastrowid
        for rid in (run_id, other_run):
            self.conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec) values (?, ?, 10, 45)",
                (rid, segment_id),
            )
