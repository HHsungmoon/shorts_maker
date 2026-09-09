"""구간 검색 — 채널 교차 안내가 **조용히 퇴화하지 않는가** (tease §3-4, §5-3 b).

이 파일이 있는 이유. "이 영상엔 없지만 ep.N 에서 다룹니다" 는 데모에서 가장 똑똑해 보이는
장면인데, 두 곳에서 조용히 죽을 수 있었다:

  1. `_best_elsewhere` 는 **이미 임베딩된** 구간만 본다. 자동 인덱싱이 답하는 그 영상에만
     걸려 있어서, 다른 편을 손으로 인덱싱하지 않으면 일반 문구가 떴다
  2. 발행되지 않은 영상을 골라 저장하면 시청자 화면이 그걸 버린다(`and s.published`) —
     DB 엔 추천이 있는데 화면엔 안 보인다

둘 다 **실패가 아니라 퇴화**다. 예외도 로그도 없어서 화면만 보고는 알 수 없다. 그래서 테스트가
지킨다. Gemini 는 부르지 않는다(mock) — 여기서 보는 건 임베딩 품질이 아니라 **무엇을 언제
인덱싱하고 무엇을 고르는가** 다.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from shorts_maker.adapters import gemini
from shorts_maker.answers import embeddings, retrieval

from ..support import DbTestCase, make_config


def fake_embeddings(mapping: dict[str, list[float]], calls: list[str] | None = None):
    """텍스트 → 벡터. `calls` 를 주면 실제로 임베딩된 텍스트가 순서대로 쌓인다."""

    def embed(cfg, texts, task_type):
        if calls is not None:
            calls.extend(texts)
        return [mapping[text] for text in texts], 1, 1

    return embed


class RetrievalTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 2차원이면 코사인을 손으로 계산할 수 있다.
        self.cfg = dataclasses.replace(make_config(Path(self._tmp.name)), embed_dim=2)

    def source(self, title: str, channel: str | None, published: bool, fingerprint: str) -> int:
        return self.conn.execute(
            """insert into sources (title, content_type, path, fingerprint, status, channel, published)
               values (%s, 'LECTURE', 'a.mp4', %s, 'DONE', %s, %s) returning id""",
            (title, fingerprint, channel, published),
        ).fetchone()["id"]

    def segment(self, source_id: int, description: str) -> int:
        chunk = self.conn.execute(
            "select id from chunks where source_id = %s limit 1", (source_id,)
        ).fetchone()
        if chunk is None:
            chunk_id = self.conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
        else:
            chunk_id = chunk["id"]
        next_idx = self.conn.execute(
            "select coalesce(max(idx) + 1, 0) as n from segments where chunk_id = %s", (chunk_id,)
        ).fetchone()["n"]
        return self.conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                     start_utterance_idx, end_utterance_idx)
               values (%s, %s, 0, 30, %s, 0, 3) returning id""",
            (chunk_id, next_idx, description),
        ).fetchone()["id"]

    def indexed_sources(self) -> set[int]:
        rows = self.conn.execute(
            """select distinct ch.source_id from embeddings e
               join segments sg on sg.id = e.ref_id
               join chunks ch on ch.id = sg.chunk_id
               where e.kind = 'segment' and e.task_type = %s""",
            (embeddings.DOCUMENT,),
        ).fetchall()
        return {r["source_id"] for r in rows}


class SiblingIndexingTest(RetrievalTestCase):
    def test_a_published_sibling_on_the_same_channel_gets_indexed(self):
        # 🔴 이게 없으면 안내가 조용히 일반 문구로 떨어진다.
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("지난 편", "쏘카", True, "sha256:b")
        self.segment(other, "다른 편의 구간")
        with mock.patch.object(gemini, "embed_texts", side_effect=fake_embeddings({"다른 편의 구간": [1.0, 0.0]})):
            done = retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine)
        self.assertEqual(done, [other])
        self.assertEqual(self.indexed_sources(), {other})

    def test_an_unpublished_sibling_is_left_alone(self):
        # 시청자에게 가리킬 수 없는 영상이다 — 임베딩 값을 쓸 이유가 없다.
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("미발행", "쏘카", False, "sha256:b")
        self.segment(other, "미발행 구간")
        with mock.patch.object(gemini, "embed_texts", autospec=True) as embed:
            done = retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine)
        self.assertEqual(done, [])
        embed.assert_not_called()

    def test_another_channel_is_left_alone(self):
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("남의 채널", "다른회사", True, "sha256:b")
        self.segment(other, "남의 구간")
        with mock.patch.object(gemini, "embed_texts", autospec=True) as embed:
            self.assertEqual(retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine), [])
        embed.assert_not_called()

    def test_a_source_with_no_channel_has_no_siblings(self):
        # 채널을 모르면 "같은 채널" 이 성립하지 않는다. 채널 없는 영상끼리 묶으면 남의 영상을 가리킨다.
        mine = self.source("채널 없음", None, True, "sha256:a")
        other = self.source("지난 편", "쏘카", True, "sha256:b")
        self.segment(other, "다른 편의 구간")
        with mock.patch.object(gemini, "embed_texts", autospec=True) as embed:
            self.assertEqual(retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine), [])
        embed.assert_not_called()

    def test_an_already_indexed_sibling_is_not_embedded_twice(self):
        # 임베딩은 캐시된다 — 영상당 한 번이라는 게 비용 논거의 근거다.
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("지난 편", "쏘카", True, "sha256:b")
        self.segment(other, "다른 편의 구간")
        fake = fake_embeddings({"다른 편의 구간": [1.0, 0.0]})
        with mock.patch.object(gemini, "embed_texts", side_effect=fake):
            retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine)
        with mock.patch.object(gemini, "embed_texts", autospec=True) as embed:
            self.assertEqual(retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine), [])
        embed.assert_not_called()

    def test_an_embedding_failure_does_not_kill_the_answer_path(self):
        """🔴 안내는 부수 작업이다. 할당량이 떨어졌다고 답할 수 있는 질문을 못 답하면 더 나쁘다."""
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("지난 편", "쏘카", True, "sha256:b")
        self.segment(other, "다른 편의 구간")
        with mock.patch.object(gemini, "embed_texts", side_effect=gemini.GeminiError("할당량 소진")):
            done = retrieval.ensure_siblings_indexed(self.conn, self.cfg, mine)
        self.assertEqual(done, [])


class BestElsewhereTest(RetrievalTestCase):
    """어느 영상을 가리키는가. 🔴 화면이 버릴 영상을 고르면 안내가 조용히 사라진다."""

    def cluster(self, source_id: int, text: str = "기술 스택이 궁금합니다") -> dict:
        row = self.conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, %s) returning *",
            (source_id, text),
        ).fetchone()
        return dict(row)

    def index(self, source_id: int, description: str, vector: list[float]) -> None:
        segment_id = self.segment(source_id, description)
        embeddings.store(self.conn, self.cfg, "segment", [segment_id], [vector], embeddings.DOCUMENT)

    def test_an_unpublished_video_is_never_suggested(self):
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        hidden = self.source("미발행", "쏘카", False, "sha256:b")
        # 질문과 완전히 같은 방향이라 발행 여부만 아니면 반드시 뽑힌다.
        self.index(hidden, "미발행 구간", [1.0, 0.0])
        found, score = retrieval._best_elsewhere(
            self.conn, self.cfg, self.cluster(mine), _query(self.cfg)
        )
        self.assertIsNone(found)
        self.assertEqual(score, 0.0)

    def test_a_published_video_on_the_same_channel_is_suggested(self):
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        other = self.source("지난 편", "쏘카", True, "sha256:b")
        self.index(other, "지난 편 구간", [1.0, 0.0])
        found, score = retrieval._best_elsewhere(
            self.conn, self.cfg, self.cluster(mine), _query(self.cfg)
        )
        self.assertEqual(found, other)
        self.assertGreater(score, 0.9)

    def test_the_closest_of_two_published_videos_wins(self):
        mine = self.source("이번 편", "쏘카", True, "sha256:a")
        near = self.source("가까운 편", "쏘카", True, "sha256:b")
        far = self.source("먼 편", "쏘카", True, "sha256:c")
        self.index(near, "가까운 구간", [1.0, 0.0])
        self.index(far, "먼 구간", [0.0, 1.0])
        found, _ = retrieval._best_elsewhere(
            self.conn, self.cfg, self.cluster(mine), _query(self.cfg)
        )
        self.assertEqual(found, near)


def _query(cfg):
    """[1, 0] 방향 검색어 벡터. 2차원이라 유사도를 손으로 확인할 수 있다."""
    return np.array([[1.0, 0.0]], dtype=np.float32)


if __name__ == "__main__":
    unittest.main()
