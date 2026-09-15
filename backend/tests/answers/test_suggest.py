"""질문을 남기는 순간 발행 숏폼 추천 (update_plan D13, `answers/suggest.py`).

🔴 여기서 지키는 것:
  1. **비교할 발행 숏폼이 없으면 외부 호출 0회.** 발행 숏폼이 없는 영상에서 공개 경로가 API 를 부르면
     D11 을 푼 이유가 없다
  2. **발행된 것만, 이 영상 것만.** 미발행이나 다른 영상 숏폼은 벡터가 있어도 비교 대상이 아니다
  3. **실패는 올리지 않는다.** 임베딩이 죽어도 빈 목록이고, 무엇이 왜 실패했는지 stage_calls 에 남는다
  4. **분당 상한.** 넘으면 호출하지 않는다 — 크리에이터 작업의 분당 한도를 지킨다
  5. **색인은 실제 대사로.** 실측에서 제목·구간 설명은 틈이 음수였다
"""

import dataclasses
import tempfile
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import gemini
from shorts_maker.answers import embeddings, suggest

from ..support import DbTestCase, make_config


class SuggestTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 2차원이면 코사인을 손으로 겨눌 수 있다.
        self.cfg = dataclasses.replace(
            make_config(Path(self._tmp.name)), embed_dim=2, suggest_min_sim=0.73, suggest_max=2,
            suggest_per_min=30, suggest_timeout_sec=3.0,
        )
        suggest.reset_budget()
        self.addCleanup(suggest.reset_budget)
        self.source_id = self.make_source("sha256:a")

    def make_source(self, fingerprint: str) -> int:
        return self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, published)"
            " values ('강연', 'LECTURE', 'a.mp4', %s, 'DONE', true) returning id", (fingerprint,)
        ).fetchone()["id"]

    def make_clip(self, source_id: int | None = None, published: bool = True,
                  texts: tuple[str, ...] = ("쏘카의 프로덕트는 세 가지입니다", "고객용 제품이 있습니다"),
                  with_parts: bool = False) -> int:
        """발화 · 구간 · 클립. 발화는 5초씩 0초부터."""
        source_id = source_id or self.source_id
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, (select count(*) from chunks where source_id = %s), 0, 600, 'c.wav') returning id",
            (source_id, source_id),
        ).fetchone()["id"]
        for n, text in enumerate(texts):
            self.conn.execute(
                "insert into utterances (chunk_id, idx, start_sec, end_sec, text) values (%s, %s, %s, %s, %s)",
                (chunk_id, n, n * 5.0, n * 5.0 + 5.0, text),
            )
        last = max(0, len(texts) - 1)
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
            " values (%s, 0, 0, %s, 0, %s) returning id", (chunk_id, last * 5.0 + 5.0, last)
        ).fetchone()["id"]
        clip_id = self.conn.execute(
            """insert into clips (run_id, segment_id, start_sec, end_sec, total_sec, rendered, published_at)
               values (%s, %s, 0, %s, %s, true, %s) returning id""",
            (run_id, segment_id, last * 5.0 + 5.0, last * 5.0 + 5.0, "now()" if published else None),
        ).fetchone()["id"]
        if published:
            self.conn.execute("update clips set published_at = now() where id = %s", (clip_id,))
        if with_parts:
            self.conn.execute(
                """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,
                                           start_utterance_idx, end_utterance_idx)
                   values (%s, 0, %s, 0, 5, 0, 0)""", (clip_id, segment_id)
            )
        self.conn.commit()
        return clip_id

    def vector(self, clip_id: int, values: list[float]) -> None:
        embeddings.store(self.conn, self.cfg, suggest.KIND, [clip_id], [values], embeddings.DOCUMENT)
        self.conn.commit()

    def stage_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "select model, error, params from stage_calls where stage = 'embed' order by id"
        )]


class IndexTest(SuggestTestCase):
    def test_the_transcript_is_what_gets_indexed(self):
        # 🔴 제목이 아니라 대사다 — 실측에서 제목은 맞는 연결과 틀린 연결의 틈이 음수였다.
        clip_id = self.make_clip(texts=("쏘카의 프로덕트는 세 가지입니다", "고객용 제품이 있습니다"))
        self.assertEqual(suggest.clip_text(self.conn, clip_id), "쏘카의 프로덕트는 세 가지입니다 고객용 제품이 있습니다")

    def test_a_clip_with_parts_uses_only_the_parts(self):
        # 답하기 경로 클립은 조각의 발화 번호 범위만 모은다. 구간 전체가 아니다.
        clip_id = self.make_clip(texts=("첫 발화", "둘째 발화", "셋째 발화"), with_parts=True)
        self.assertEqual(suggest.clip_text(self.conn, clip_id), "첫 발화")

    def test_indexing_stores_a_clip_vector_once(self):
        clip_id = self.make_clip()
        with mock.patch.object(gemini, "embed_texts", return_value=([[1.0, 0.0]], 5, 1)) as embed:
            self.assertEqual(suggest.index_clip(self.conn, self.cfg, clip_id), "indexed")
            self.assertEqual(suggest.index_clip(self.conn, self.cfg, clip_id), "indexed")
        self.assertEqual(embed.call_count, 1, "이미 있는 벡터를 다시 만들었다")
        self.assertEqual(embed.call_args.args[2], embeddings.DOCUMENT)

    def test_a_clip_without_transcript_is_not_sent(self):
        clip_id = self.make_clip(texts=())
        with mock.patch.object(gemini, "embed_texts", autospec=True) as embed:
            self.assertEqual(suggest.index_clip(self.conn, self.cfg, clip_id), "empty")
        embed.assert_not_called()

    def test_an_indexing_failure_is_recorded_not_raised(self):
        """🔴 발행 직후에 부른다. 할당량이 떨어졌다고 크리에이터의 발행이 실패하면 안 된다."""
        clip_id = self.make_clip()
        with mock.patch.object(gemini, "embed_texts", side_effect=gemini.GeminiError("한도 소진")):
            self.assertEqual(suggest.index_clip(self.conn, self.cfg, clip_id), "failed")
        self.conn.commit()
        rows = self.stage_rows()
        self.assertEqual(len(rows), 1)
        self.assertIn("한도 소진", rows[0]["error"])
        self.assertEqual(rows[0]["params"]["purpose"], "index")

    def test_backfill_indexes_only_published_clips_missing_a_vector(self):
        done = self.make_clip()
        self.vector(done, [1.0, 0.0])
        missing = self.make_clip()
        self.make_clip(published=False)
        with mock.patch.object(gemini, "embed_texts", return_value=([[0.0, 1.0]], 5, 1)) as embed:
            counted = suggest.index_published(self.conn, self.cfg, self.source_id)
        self.assertEqual(counted["indexed"], 1)
        self.assertEqual(embed.call_count, 1)
        self.assertIn(missing, embeddings.load(self.conn, self.cfg, suggest.KIND, [missing], embeddings.DOCUMENT))


class MatchTest(SuggestTestCase):
    def run_match(self, query: list[float], text: str = "쏘카는 어떤 제품을 만드나요?"):
        with mock.patch.object(gemini, "embed_once", return_value=(query, 7)) as once:
            found = suggest.match(self.conn, self.cfg, self.source_id, text, question_id=42)
        return found, once

    def test_nothing_published_to_compare_means_no_call_at_all(self):
        """🔴 발행 숏폼이 없는 영상에서는 공개 경로 외부 호출이 여전히 0회다."""
        found, once = self.run_match([1.0, 0.0])
        self.assertEqual(found, [])
        once.assert_not_called()
        self.assertEqual(self.stage_rows(), [])

    def test_a_published_clip_without_a_vector_is_not_worth_a_call(self):
        self.make_clip()
        _, once = self.run_match([1.0, 0.0])
        once.assert_not_called()

    def test_a_close_clip_is_suggested(self):
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        found, once = self.run_match([1.0, 0.0])
        self.assertEqual([c for c, _ in found], [clip_id])
        self.assertEqual(once.call_args.args[2], embeddings.QUERY)
        self.assertEqual(once.call_args.args[3], self.cfg.suggest_timeout_sec)

    def test_a_distant_clip_is_not_suggested(self):
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        found, _ = self.run_match([0.6, 0.8])   # 코사인 0.6 < 0.73
        self.assertEqual(found, [])

    def test_best_first_and_capped(self):
        near = self.make_clip()
        nearer = self.make_clip()
        also = self.make_clip()
        self.vector(near, [0.8, 0.6])      # 0.8
        self.vector(nearer, [1.0, 0.0])    # 1.0
        self.vector(also, [0.9, 0.436])    # ≈0.9
        found, _ = self.run_match([1.0, 0.0])
        self.assertEqual([c for c, _ in found], [nearer, also])

    def test_an_unpublished_clip_is_never_suggested(self):
        # 🔴 공개 규칙 ①. 벡터가 남아 있어도(내렸다 등) 비교 대상이 아니다.
        hidden = self.make_clip(published=False)
        self.vector(hidden, [1.0, 0.0])
        found, once = self.run_match([1.0, 0.0])
        self.assertEqual(found, [])
        once.assert_not_called()

    def test_another_videos_clip_is_never_suggested(self):
        other = self.make_source("sha256:b")
        clip_id = self.make_clip(source_id=other)
        self.vector(clip_id, [1.0, 0.0])
        found, once = self.run_match([1.0, 0.0])
        self.assertEqual(found, [])
        once.assert_not_called()

    def test_a_failed_embedding_gives_nothing_and_is_recorded(self):
        """🔴 올리지 않는다. 질문은 이미 커밋됐고 추천만 빠진다."""
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        with mock.patch.object(gemini, "embed_once", side_effect=gemini.GeminiError("시간 초과")):
            found = suggest.match(self.conn, self.cfg, self.source_id, "질문", question_id=42)
        self.assertEqual(found, [])
        rows = self.stage_rows()
        self.assertIn("시간 초과", rows[-1]["error"])
        self.assertEqual(rows[-1]["params"]["question_id"], 42)

    def test_a_malformed_vector_gives_nothing(self):
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        found, _ = self.run_match([1.0, 0.0, 0.0])   # 차원이 다르다
        self.assertEqual(found, [])

    def test_the_budget_stops_calls_once_spent(self):
        """🔴 공개 경로 전용 분당 상한. 크리에이터의 [집계]·답하기가 쓰는 분당 한도를 지킨다."""
        self.cfg = dataclasses.replace(self.cfg, suggest_per_min=2)
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        with mock.patch.object(gemini, "embed_once", return_value=([1.0, 0.0], 7)) as once:
            for _ in range(5):
                suggest.match(self.conn, self.cfg, self.source_id, "질문")
        self.assertEqual(once.call_count, 2)
        skipped = [r for r in self.stage_rows() if r["params"].get("skipped") == "budget"]
        self.assertEqual(len(skipped), 3)
        self.assertTrue(all(r["model"] is None for r in skipped), "부르지 않았는데 모델이 기록됐다")

    def test_zero_budget_turns_the_feature_off(self):
        self.cfg = dataclasses.replace(self.cfg, suggest_per_min=0)
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        found, once = self.run_match([1.0, 0.0])
        self.assertEqual(found, [])
        once.assert_not_called()

    def test_the_top_score_is_kept_even_when_nothing_is_picked(self):
        # 기준선을 다시 정할 때 "안 떴을 때 몇 점이었나" 가 필요하다.
        clip_id = self.make_clip()
        self.vector(clip_id, [1.0, 0.0])
        self.run_match([0.6, 0.8])
        params = self.stage_rows()[-1]["params"]
        self.assertEqual(params["picked"], [])
        self.assertAlmostEqual(params["top"], 0.6, places=3)
        self.assertEqual(params["top_clip_id"], clip_id)
