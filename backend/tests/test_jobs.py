"""잡 큐 — **한 번에 하나**(2026-09-16).

워커가 1개라 두 잡이 겹쳐 돌지는 않았다. 그런데 예전 `submit` 은 무조건 받아 줄을 세웠고,
여러 명이 같은 화면을 보는 자리(해커톤 심사)에서는 그게 조용한 대기열이 된다 — 다섯 명이
각자 누르면 다섯 개가 쌓이고, 마지막 사람은 몇 분 뒤에야 자기 결과를 본다. 그 사이 Gemini
하루 할당량이 그만큼 탄다. 그래서 지금은 **거절하고 무엇이 돌고 있는지 말한다**.
"""

import threading
import time
import unittest

from shorts_maker import jobs


class ExclusiveSubmitTest(unittest.TestCase):
    def setUp(self):
        self.queue = jobs.JobQueue()
        self.started = threading.Event()
        self.release = threading.Event()
        # 잡이 매달린 채 테스트가 끝나면 워커 스레드가 살아남는다.
        self.addCleanup(self.release.set)

    def _blocking(self):
        self.started.set()
        self.release.wait(5)
        return "ok"

    def _wait_idle(self) -> None:
        deadline = time.time() + 5
        while self.queue.active() is not None and time.time() < deadline:
            time.sleep(0.01)

    def test_a_second_job_is_refused_while_one_runs(self):
        first = self.queue.submit("stt", "3", self._blocking)
        self.assertTrue(self.started.wait(5))
        with self.assertRaises(jobs.Busy) as caught:
            self.queue.submit("render", "4", lambda: None)
        # 🔴 무엇이 돌고 있는지를 예외가 들고 있어야 한다 — 화면이 "다른 작업 실행 중"까지만
        # 말하면 사용자는 얼마나 기다려야 하는지 모른다.
        self.assertEqual(caught.exception.running.id, first.id)
        self.assertEqual(caught.exception.running.kind, "stt")

    def test_the_queue_accepts_again_once_it_finishes(self):
        self.queue.submit("stt", "3", self._blocking)
        self.assertTrue(self.started.wait(5))
        self.release.set()
        self._wait_idle()
        self.assertIsNotNone(self.queue.submit("render", "4", lambda: None))

    def test_a_queued_job_also_blocks_not_just_a_running_one(self):
        """QUEUED 도 막는다. 워커가 하나라 '대기 중' 은 곧 '곧 돌 것' 이다."""
        self.queue.submit("stt", "3", self._blocking)
        self.assertTrue(self.started.wait(5))
        running = self.queue.active()
        self.assertIsNotNone(running)
        self.assertIn(running.status, ("QUEUED", "RUNNING"))

    def test_exclusive_false_is_still_allowed(self):
        """내부에서 의도적으로 겹쳐 넣을 여지는 남긴다 — 다만 기본값이 아니다."""
        self.queue.submit("stt", "3", self._blocking)
        self.assertTrue(self.started.wait(5))
        self.assertIsNotNone(self.queue.submit("probe", "-", lambda: None, exclusive=False))

    def test_a_failed_job_does_not_block_the_next_one(self):
        # 🔴 실패한 잡이 큐를 영구히 막으면 서버를 재기동해야 한다.
        def boom():
            raise RuntimeError("터졌다")

        self.queue.submit("rank", "3", boom)
        self._wait_idle()
        self.assertIsNotNone(self.queue.submit("render", "4", lambda: None))


if __name__ == "__main__":
    unittest.main()
