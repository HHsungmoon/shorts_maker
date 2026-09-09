"""클러스터 상태 기계와 [집계].

여기서 지키는 것:
  1. 🔴 상태 전이는 표에 있는 것만. **36칸을 전부** 확인한다 — 몇 개만 보면 새로 추가한 전이가
     조용히 통과한다
  2. 🔴 LLM 은 대표 문장만 짓는다. 소속은 코사인이, 개수는 SQL 이 정한다
  3. 🔴 재집계는 증분이다 — 이미 묶인 질문의 소속과 좋아요는 그대로다(불변식 I12)

Gemini 는 부르지 않는다(mock). 임베딩도 마찬가지 — 여기서 검증하는 건 모델 품질이 아니라
**배정 규칙**이다. θ 를 넘기면 붙고 아니면 안 붙는가.
"""

import unittest
from unittest import mock

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from shorts_maker.adapters import gemini
from shorts_maker.answers import clusters, embeddings

from ..support import DbTestCase, make_config

import dataclasses
import tempfile
from pathlib import Path


class ClusterTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, published)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', true) returning id"
        ).fetchone()["id"]
        self.conn.commit()

    def cluster(self, text="연봉이 어떻게 되나요?", status="OPEN") -> int:
        cluster_id = self.conn.execute(
            "insert into question_clusters (source_id, canonical_text, status) values (%s, %s, %s) returning id",
            (self.source_id, text, status),
        ).fetchone()["id"]
        self.conn.commit()
        return cluster_id

    def question(self, text, cluster_id=None, viewer="v1") -> int:
        got = self.conn.execute(
            "insert into questions (source_id, text, viewer_id, cluster_id) values (%s, %s, %s, %s) returning id",
            (self.source_id, text, viewer, cluster_id),
        ).fetchone()["id"]
        self.conn.commit()
        return got

    def status_of(self, cluster_id: int) -> str:
        return self.conn.execute(
            "select status from question_clusters where id = %s", (cluster_id,)
        ).fetchone()["status"]


class TransitionTableTest(ClusterTestCase):
    """🔴 6×6 = 36칸. 표에 있는 것만 통과하고 나머지는 전부 거부돼야 한다."""

    def test_every_pair_matches_the_table(self):
        for source in clusters.STATUSES:
            for target in clusters.STATUSES:
                with self.subTest(frm=source, to=target):
                    cluster_id = self.cluster(status=source)
                    allowed = target in clusters.TRANSITIONS[source]
                    if allowed:
                        clusters.transition(self.conn, cluster_id, target)
                        self.conn.commit()
                        self.assertEqual(self.status_of(cluster_id), target)
                    else:
                        with self.assertRaises(clusters.ClusterError):
                            clusters.transition(self.conn, cluster_id, target)
                        self.conn.rollback()
                        self.assertEqual(self.status_of(cluster_id), source)

    def test_the_table_covers_every_status_in_the_schema(self):
        # 스키마 CHECK 에 상태를 더하고 표를 안 고치면 그 상태는 영원히 빠져나올 수 없다.
        rows = self.conn.execute(
            """select pg_get_constraintdef(oid) as def from pg_constraint
               where conrelid = 'question_clusters'::regclass and conname like '%%status%%'"""
        ).fetchone()
        for status in clusters.STATUSES:
            self.assertIn(f"'{status}'", rows["def"])

    def test_a_rejected_transition_changes_nothing_else(self):
        cluster_id = self.cluster(status="OPEN")
        with self.assertRaises(clusters.ClusterError):
            clusters.transition(self.conn, cluster_id, "PUBLISHED", run_id=7)
        self.conn.rollback()
        row = self.conn.execute(
            "select status, run_id from question_clusters where id = %s", (cluster_id,)
        ).fetchone()
        self.assertEqual((row["status"], row["run_id"]), ("OPEN", None))

    def test_an_unknown_status_is_refused(self):
        cluster_id = self.cluster()
        with self.assertRaises(clusters.ClusterError):
            clusters.transition(self.conn, cluster_id, "DONE")

    def test_transition_carries_the_run_id(self):
        cluster_id = self.cluster(status="OPEN")
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        row = clusters.transition(self.conn, cluster_id, "IN_PROGRESS", run_id=run_id)
        self.assertEqual(row["run_id"], run_id)

    def test_restart_reopens_stuck_clusters_only(self):
        # 잡 큐는 메모리에만 있다 — 재기동하면 IN_PROGRESS 는 이미 죽은 것이다.
        stuck = self.cluster(status="IN_PROGRESS")
        review = self.cluster(text="다른 질문?", status="REVIEW")
        self.assertEqual(clusters.reopen_stuck(self.conn), 1)
        self.conn.commit()
        self.assertEqual(self.status_of(stuck), "OPEN")
        self.assertEqual(self.status_of(review), "REVIEW")


class DemandTest(ClusterTestCase):
    """🔴 개수는 SQL 이 센다. LLM 에게 시키지 않는다."""

    def test_counts_questions_and_likes_per_cluster(self):
        popular = self.cluster("연봉이 어떻게 되나요?")
        quiet = self.cluster("야근 많나요?")
        first = self.question("연봉은?", popular)
        self.question("초봉은?", popular)
        self.question("야근?", quiet)
        for viewer in ("a", "b", "c"):
            self.conn.execute(
                "insert into question_likes (question_id, viewer_id) values (%s, %s)", (first, viewer)
            )
        self.conn.commit()
        found = {c["id"]: c for c in clusters.demand(self.conn, self.source_id)}
        self.assertEqual((found[popular]["question_count"], found[popular]["like_count"]), (2, 3))
        self.assertEqual((found[quiet]["question_count"], found[quiet]["like_count"]), (1, 0))

    def test_demand_is_ordered_by_question_count(self):
        quiet = self.cluster("야근 많나요?")
        popular = self.cluster("연봉이 어떻게 되나요?")
        self.question("연봉은?", popular)
        self.question("초봉은?", popular)
        self.question("야근?", quiet)
        order = [c["id"] for c in clusters.demand(self.conn, self.source_id)]
        self.assertEqual(order, [popular, quiet])

    def test_unclustered_questions_are_listed_separately(self):
        joined = self.cluster()
        self.question("연봉은?", joined)
        self.question("아직 안 묶인 질문")
        self.assertEqual([q["text"] for q in clusters.unclustered(self.conn, self.source_id)],
                         ["아직 안 묶인 질문"])


class NamingValidationTest(unittest.TestCase):
    """LLM 출력 검증(§12). 형식이 아니라 **내용**을 본다."""

    def test_drops_blanks_and_duplicates_of_existing(self):
        raw = '{"canonical": ["  ", "연봉이 어떻게 되나요?", "새 질문은?", "연봉이 어떻게 되나요?"]}'
        got = clusters.parse_naming(raw, ["연봉이 어떻게 되나요?"], 10)
        self.assertEqual(got, ["새 질문은?"])

    def test_duplicate_detection_ignores_case_and_spacing(self):
        raw = '{"canonical": ["  Salary Range?  "]}'
        self.assertEqual(clusters.parse_naming(raw, ["salary range?"], 10), [])

    def test_long_texts_are_truncated_not_rejected(self):
        raw = '{"canonical": ["%s"]}' % ("가" * 300)
        got = clusters.parse_naming(raw, [], 10)
        self.assertEqual(len(got[0]), clusters.CANONICAL_MAX_LEN)

    def test_the_limit_is_enforced(self):
        raw = '{"canonical": ["하나?", "둘?", "셋?"]}'
        self.assertEqual(len(clusters.parse_naming(raw, [], 2)), 2)

    def test_broken_json_and_missing_key_are_errors(self):
        for raw in ("not json", '{"nope": []}', '{"canonical": "문자열"}'):
            with self.subTest(raw=raw):
                with self.assertRaises(clusters.ClusterError):
                    clusters.parse_naming(raw, [], 10)

    def test_the_prompt_never_asks_for_indices_or_counts(self):
        # 🔴 프롬프트가 소속·개수를 물으면 모델이 답하고, 그 답은 틀린다. 입력에서 빼는 게 규약이다.
        prompt = clusters.build_naming_prompt(["연봉은?", "야근은?"], ["기존 문장?"], 5)
        self.assertIn("판단하지 마라", prompt)
        for banned in ("인덱스", "번호", "몇 개인지 세", "개수를 세"):
            self.assertNotIn(banned, prompt.replace("몇 개가 있는지는 **판단하지 마라.**", ""))
        # 질문에 번호를 매겨 보내지 않는다 — 번호가 있으면 모델이 그걸 지목하고 싶어진다.
        self.assertNotIn("[0]", prompt)
        self.assertNotIn("1.", prompt)


def fake_embeddings(mapping: dict[str, list[float]]):
    """텍스트 → 벡터를 고정해 주는 gemini.embed_texts 대역."""

    def embed(cfg, texts, task_type):
        return [mapping[text] for text in texts], 1, 1

    return embed


class AggregateTest(ClusterTestCase):
    """[집계] 전체. Gemini 는 대역으로 바꾸고 배정 규칙만 본다."""

    def setUp(self) -> None:
        super().setUp()
        # 2차원이면 각도로 유사도를 손계산할 수 있어서 θ 경계를 정확히 겨눌 수 있다.
        self.cfg = dataclasses.replace(self.cfg, embed_dim=2, cluster_theta=0.9)

    def run_aggregate(self, canonical: list[str], vectors: dict[str, list[float]]):
        payload = '{"canonical": %s}' % __import__("json").dumps(canonical, ensure_ascii=False)
        with (
            mock.patch.object(gemini, "generate_json", return_value=(payload, {
                "input_tokens": 1, "output_tokens": 1, "thinking_tokens": 0,
                "total_tokens": 2, "cached_tokens": 0, "attempts": 1}, 5)),
            mock.patch.object(gemini, "embed_texts", side_effect=fake_embeddings(vectors)),
        ):
            return clusters.aggregate(self.conn, self.cfg, self.source_id)

    def test_questions_join_the_nearest_canonical_above_theta(self):
        self.question("연봉은 얼마인가요?")
        self.question("급여 수준이 궁금해요")
        result = self.run_aggregate(
            ["연봉이 어느 정도인가요?"],
            {
                "연봉이 어느 정도인가요?": [1.0, 0.0],
                "연봉은 얼마인가요?": [1.0, 0.02],      # 거의 같은 방향 → 붙는다
                "급여 수준이 궁금해요": [1.0, 0.05],
            },
        )
        self.assertEqual(result["assigned"], 2)
        self.assertEqual(result["newClusters"], 1)
        found = clusters.demand(self.conn, self.source_id)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["question_count"], 2)

    def test_a_question_below_theta_becomes_its_own_cluster(self):
        self.question("연봉은 얼마인가요?")
        self.question("야근 많아요?")
        self.run_aggregate(
            ["연봉이 어느 정도인가요?"],
            {
                "연봉이 어느 정도인가요?": [1.0, 0.0],
                "연봉은 얼마인가요?": [1.0, 0.02],
                "야근 많아요?": [0.0, 1.0],   # 직교 → 코사인 0 → 단독
            },
        )
        found = {c["canonical_text"]: c for c in clusters.demand(self.conn, self.source_id)}
        self.assertEqual(len(found), 2)
        # 단독 클러스터의 대표 문장은 질문 원문 그대로다.
        self.assertIn("야근 많아요?", found)
        self.assertEqual(found["야근 많아요?"]["question_count"], 1)

    def test_later_questions_join_a_singleton_made_earlier_in_the_same_run(self):
        # 🔴 이게 없으면 LLM 이 놓친 표현이 전부 1개짜리 클러스터로 흩어진다.
        self.question("야근 많아요?")
        self.question("주말에도 일하나요?")
        self.run_aggregate(
            [],
            {
                "야근 많아요?": [0.0, 1.0],
                "주말에도 일하나요?": [0.03, 1.0],
            },
        )
        found = clusters.demand(self.conn, self.source_id)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["question_count"], 2)

    def test_a_canonical_nobody_joined_is_removed(self):
        self.question("연봉은 얼마인가요?")
        self.run_aggregate(
            ["연봉이 어느 정도인가요?", "아무도 안 물어본 주제는?"],
            {
                "연봉이 어느 정도인가요?": [1.0, 0.0],
                "아무도 안 물어본 주제는?": [-1.0, 0.0],
                "연봉은 얼마인가요?": [1.0, 0.02],
            },
        )
        texts = [c["canonical_text"] for c in clusters.demand(self.conn, self.source_id)]
        self.assertEqual(texts, ["연봉이 어느 정도인가요?"])

    def test_reaggregating_leaves_existing_membership_and_likes_alone(self):
        # 불변식 I12. 재집계는 증분이다 — 몇 번을 눌러도 안전해야 크리에이터가 누른다.
        kept = self.cluster("이미 있는 그룹?")
        old_question = self.question("이미 묶인 질문", kept)
        self.conn.execute(
            "insert into question_likes (question_id, viewer_id) values (%s, 'a')", (old_question,)
        )
        self.conn.commit()
        self.question("새로 들어온 질문")
        self.run_aggregate(
            [],
            {
                "이미 있는 그룹?": [1.0, 0.0],
                "새로 들어온 질문": [0.0, 1.0],
            },
        )
        row = self.conn.execute(
            "select text, cluster_id from questions where id = %s", (old_question,)
        ).fetchone()
        self.assertEqual((row["text"], row["cluster_id"]), ("이미 묶인 질문", kept))
        self.assertEqual(
            self.conn.execute("select count(*) as n from question_likes").fetchone()["n"], 1
        )

    def test_nothing_happens_and_nothing_is_called_when_there_is_nothing_new(self):
        # 🔴 미분류 질문이 없으면 LLM 도 임베딩도 부르지 않는다 — 버튼 연타가 돈이 되면 안 된다.
        with (
            mock.patch.object(gemini, "generate_json", autospec=True) as llm,
            mock.patch.object(gemini, "embed_texts", autospec=True) as embed,
        ):
            result = clusters.aggregate(self.conn, self.cfg, self.source_id)
        llm.assert_not_called()
        embed.assert_not_called()
        self.assertEqual(result["assigned"], 0)

    def test_the_llm_call_is_recorded_as_a_stage(self):
        self.question("연봉은 얼마인가요?")
        self.run_aggregate(
            ["연봉이 어느 정도인가요?"],
            {"연봉이 어느 정도인가요?": [1.0, 0.0], "연봉은 얼마인가요?": [1.0, 0.02]},
        )
        stages = [r["stage"] for r in self.conn.execute(
            "select stage from stage_calls order by id"
        )]
        self.assertEqual(stages.count("cluster"), 1)
        # 임베딩도 단계다 — 대표 문장 배치 1회 + 질문 배치 1회.
        self.assertEqual(stages.count("embed"), 2)


class RenameTest(ClusterTestCase):
    def test_renaming_recomputes_the_vector(self):
        # 🔴 문장을 고쳤는데 벡터가 그대로면 다음 집계가 옛 문장 기준으로 붙인다.
        cluster_id = self.cluster("옛 문장?")
        with mock.patch.object(gemini, "embed_texts", side_effect=fake_embeddings({"옛 문장?": [1.0, 0.0]})):
            embeddings.embed_and_store(
                self.conn, self.cfg, "cluster", [(cluster_id, "옛 문장?")], embeddings.SIMILARITY
            )
            self.conn.commit()
        with mock.patch.object(gemini, "embed_texts", side_effect=fake_embeddings({"새 문장?": [0.0, 1.0]})) as embed:
            row = clusters.rename(self.conn, self.cfg, cluster_id, "  새 문장?  ")
        self.assertEqual(row["canonical_text"], "새 문장?")
        embed.assert_called_once()
        stored = embeddings.load(self.conn, self.cfg, "cluster", [cluster_id], embeddings.SIMILARITY)
        vector = embeddings.from_blobs([stored[cluster_id]], self.cfg.embed_dim)[0]
        self.assertAlmostEqual(float(vector[1]), 1.0, places=5)

    def test_a_blank_name_is_refused(self):
        cluster_id = self.cluster()
        with self.assertRaises(clusters.ClusterError):
            clusters.rename(self.conn, self.cfg, cluster_id, "   ")


class EmbeddingStoreTest(ClusterTestCase):
    def test_vectors_are_normalised_on_the_way_in(self):
        # 🔴 정규화하지 않으면 내적이 코사인이 아니게 되고 θ 가 의미를 잃는다.
        blob = embeddings.to_blob([3.0, 4.0])
        vector = np.frombuffer(blob, dtype="<f4")
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=5)
        self.assertAlmostEqual(float(vector[0]), 0.6, places=5)

    def test_a_zero_vector_is_refused(self):
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.to_blob([0.0, 0.0])

    def test_vectors_of_another_model_or_task_type_are_not_reused(self):
        # 설정을 바꾸면 옛 벡터가 조용히 섞이면 안 된다 — 안 보이면 다시 계산된다.
        embeddings.store(self.conn, self.cfg, "cluster", [1], [[1.0, 0.0]], embeddings.SIMILARITY)
        self.conn.commit()
        self.assertEqual(len(embeddings.load(self.conn, self.cfg, "cluster", [1], embeddings.SIMILARITY)), 1)
        self.assertEqual(len(embeddings.load(self.conn, self.cfg, "cluster", [1], embeddings.QUERY)), 0)
        other = dataclasses.replace(self.cfg, embed_model="다른-모델")
        self.assertEqual(len(embeddings.load(self.conn, other, "cluster", [1], embeddings.SIMILARITY)), 0)

    def test_the_same_ref_can_hold_two_task_types(self):
        # 002 마이그레이션의 이유. 대표 문장은 묶기용과 검색용 벡터를 둘 다 갖는다.
        embeddings.store(self.conn, self.cfg, "cluster", [1], [[1.0, 0.0]], embeddings.SIMILARITY)
        embeddings.store(self.conn, self.cfg, "cluster", [1], [[0.0, 1.0]], embeddings.QUERY)
        self.conn.commit()
        self.assertEqual(self.conn.execute("select count(*) as n from embeddings").fetchone()["n"], 2)

    def test_storing_twice_updates_instead_of_failing(self):
        embeddings.store(self.conn, self.cfg, "cluster", [1], [[1.0, 0.0]], embeddings.SIMILARITY)
        embeddings.store(self.conn, self.cfg, "cluster", [1], [[0.0, 1.0]], embeddings.SIMILARITY)
        self.conn.commit()
        stored = embeddings.load(self.conn, self.cfg, "cluster", [1], embeddings.SIMILARITY)
        vector = embeddings.from_blobs([stored[1]], 2)[0]
        self.assertAlmostEqual(float(vector[1]), 1.0, places=5)

    def test_mismatched_lengths_are_refused(self):
        with self.assertRaises(embeddings.EmbeddingError):
            embeddings.store(self.conn, self.cfg, "cluster", [1, 2], [[1.0, 0.0]], embeddings.SIMILARITY)

    def test_already_embedded_items_are_not_sent_again(self):
        # 재집계가 증분으로 도는 근거 — 매번 전부 다시 임베딩하면 버튼을 누를 때마다 돈이 든다.
        with mock.patch.object(gemini, "embed_texts", side_effect=fake_embeddings({"문장?": [1.0, 0.0]})) as embed:
            embeddings.embed_and_store(self.conn, self.cfg, "cluster", [(1, "문장?")], embeddings.SIMILARITY)
            self.conn.commit()
            embeddings.embed_and_store(self.conn, self.cfg, "cluster", [(1, "문장?")], embeddings.SIMILARITY)
        embed.assert_called_once()

    def test_cosine_of_orthogonal_and_identical_vectors(self):
        left = embeddings.from_blobs([embeddings.to_blob([1.0, 0.0])], 2)
        right = embeddings.from_blobs(
            [embeddings.to_blob([1.0, 0.0]), embeddings.to_blob([0.0, 1.0])], 2
        )
        scores = embeddings.cosine(left, right)[0]
        self.assertAlmostEqual(float(scores[0]), 1.0, places=5)
        self.assertAlmostEqual(float(scores[1]), 0.0, places=5)



class RunNoteTest(ClusterTestCase):
    """🔴 상태만 보여주면 크리에이터는 왜 그렇게 됐는지 알 수 없다.

    영상에 그 얘기가 정말 없어서 UNANSWERABLE 이 된 것과, 검색이 엉뚱한 데를 짚어서 그런 것은
    대응이 다르다 — 전자는 받아들이고 후자는 대표 문장을 고쳐 다시 돌린다(2026-09-08).
    """

    def test_the_reason_travels_with_the_cluster(self):
        cluster_id = self.cluster(status="OPEN")
        run_id = self.conn.execute(
            """insert into runs (source_id, status, ranked) values (%s, 'DONE', %s) returning id""",
            (self.source_id, Jsonb({"answerable": False, "reason": "영상에 복지 얘기가 없다"})),
        ).fetchone()["id"]
        clusters.transition(self.conn, cluster_id, "IN_PROGRESS", run_id=run_id)
        clusters.transition(self.conn, cluster_id, "UNANSWERABLE")
        self.conn.commit()
        found = clusters.demand(self.conn, self.source_id)[0]
        self.assertEqual(found["status"], "UNANSWERABLE")
        self.assertEqual(found["run_note"], "영상에 복지 얘기가 없다")

    def test_a_failed_run_leaves_its_error(self):
        cluster_id = self.cluster(status="OPEN")
        run_id = self.conn.execute(
            "insert into runs (source_id, status, error) values (%s, 'FAILED', '할당량 초과') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        clusters.transition(self.conn, cluster_id, "IN_PROGRESS", run_id=run_id)
        clusters.transition(self.conn, cluster_id, "OPEN")
        self.conn.commit()
        found = clusters.demand(self.conn, self.source_id)[0]
        self.assertEqual(found["run_error"], "할당량 초과")

    def test_a_cluster_without_a_run_has_no_note(self):
        self.cluster()
        found = clusters.demand(self.conn, self.source_id)[0]
        self.assertIsNone(found["run_note"])


class CandidateListTest(ClusterTestCase):
    """🔴 겨룬 후보가 **대사와 함께** 화면까지 간다.

    판정 결과만 보여주는 것으로는 부족하다는 게 요지다 — "조합, 2조각, 28초, 자립 X, 45점" 만
    보고는 무엇을 만들지 고를 수 없다. 사람은 내용을 읽어야 판단한다.
    """

    def run_with(self, *candidates: dict) -> int:
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        for ordinal, spec in enumerate(candidates):
            self.conn.execute(
                """insert into run_candidates (run_id, ordinal, label, reason, parts, total_sec,
                                               standalone, answers, score, judge_note)
                   values (%s, %s, %s, '이유', %s, %s, %s, %s, %s, '소견')""",
                (
                    run_id, ordinal, spec["label"],
                    Jsonb(spec.get("parts") or [{"ordinal": 0, "segment_id": 1, "chunk_id": 1,
                                                 "start_sec": 0.0, "end_sec": 26.0,
                                                 "start_utterance_idx": 0, "end_utterance_idx": 3,
                                                 "text": spec.get("text", "대사")}]),
                    spec.get("total_sec", 26.0), spec["standalone"], spec.get("answers", True),
                    spec["score"],
                ),
            )
        return run_id

    def test_the_transcript_comes_with_each_candidate(self):
        """🔴 이 대사가 목록의 존재 이유다 — 크리에이터가 읽고 고르는 것이 그것이다."""
        run_id = self.run_with({"label": "single", "standalone": True, "score": 90,
                                "text": "쏘카의 프로덕트는 세 가지입니다"})
        found = clusters.candidates_of(self.conn, run_id)
        self.assertEqual(found[0]["parts"][0]["text"], "쏘카의 프로덕트는 세 가지입니다")

    def test_candidates_come_back_best_first(self):
        run_id = self.run_with(
            {"label": "single", "standalone": True, "score": 90},
            {"label": "combo", "standalone": False, "score": 45},
            {"label": "tight", "standalone": False, "score": 40},
        )
        found = clusters.candidates_of(self.conn, run_id)
        self.assertEqual([c["label"] for c in found], ["single", "combo", "tight"])

    def test_the_recommendation_needs_both_gates_not_just_the_score(self):
        """🔴 점수만으로 추천하면 자립하지 않는 클립을 권하게 된다.

        실측에서 조합이 45점으로 2위였지만 자립성에서 떨어졌다 — 시청자가 앞뒤를 모른 채 본다.
        """
        run_id = self.run_with(
            {"label": "combo", "standalone": False, "score": 95},
            {"label": "single", "standalone": True, "score": 60},
        )
        found = clusters.candidates_of(self.conn, run_id)
        recommended = [c["label"] for c in found if c["recommended"]]
        self.assertEqual(recommended, ["single"])

    def test_when_nothing_passes_the_best_score_is_still_recommended(self):
        # 전부 떨어져도 하나는 권한다 — 사람이 읽어 보고 판단할 수 있다.
        run_id = self.run_with(
            {"label": "combo", "standalone": False, "score": 45},
            {"label": "tight", "standalone": False, "score": 40},
        )
        found = clusters.candidates_of(self.conn, run_id)
        self.assertEqual([c["label"] for c in found if c["recommended"]], ["combo"])

    def test_a_run_without_candidates_gives_an_empty_list(self):
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        self.assertEqual(clusters.candidates_of(self.conn, run_id), [])

    def test_no_run_at_all_is_not_an_error(self):
        self.assertEqual(clusters.candidates_of(self.conn, None), [])

    def test_another_runs_candidates_do_not_leak_in(self):
        self.run_with({"label": "single", "standalone": True, "score": 90})
        mine = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        self.assertEqual(clusters.candidates_of(self.conn, mine), [])


if __name__ == "__main__":
    unittest.main()
