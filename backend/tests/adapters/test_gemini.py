"""일시 실패 판별 검증.

🔴 여기서 지키는 건 **무엇을 다시 부르지 않는가**다. 4xx 를 재시도하면 잘못된 요청을
반복하며 돈만 쓰고, 5xx 를 재시도하지 않으면 구글의 몇 초짜리 과부하에 잡 전체가 죽는다.
"""

import unittest

from shorts_maker.adapters import gemini


class Boom(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


class IsTransientTest(unittest.TestCase):
    def test_retries_overload_and_server_errors(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(gemini.is_transient(Boom("boom", code)))

    def test_does_not_retry_client_errors(self):
        for code in (400, 401, 403, 404, 422):
            with self.subTest(code=code):
                self.assertFalse(gemini.is_transient(Boom("nope", code)))

    def test_reads_the_status_out_of_the_message_when_there_is_no_attribute(self):
        # google-genai 의 예외 클래스는 버전마다 바뀐다. 속성이 없어도 알아봐야 한다.
        exc = Boom(
            "ServerError: 503 UNAVAILABLE. {'error': {'code': 503, "
            "'message': 'This model is currently experiencing high demand.'}}"
        )
        self.assertTrue(gemini.is_transient(exc))

    def test_unknown_failures_are_not_retried(self):
        self.assertFalse(gemini.is_transient(Boom("connection reset by peer")))

    def test_backoff_grows_but_stays_bounded(self):
        """🔴 상한이 있어야 한다 — 잡 워커가 하나라 한 호출이 큐 전체를 멈춘다."""
        waits = [gemini.BASE_BACKOFF_SEC * (2 ** (n - 1)) for n in range(1, gemini.MAX_ATTEMPTS)]
        self.assertEqual(waits, sorted(waits))
        # 넉넉하되 무한하지 않게. 503 이 몇 분 이어지는 걸 겪고 14초에서 늘렸다(2026-09-09).
        self.assertGreater(sum(waits), 30)
        self.assertLess(sum(waits), 120)



class DailyQuotaTest(unittest.TestCase):
    """🔴 429 라고 다 같은 429 가 아니다.

    분당 한도는 몇 초 기다리면 풀리지만 **일일 한도는 내일까지 안 풀린다.** 거기에 백오프
    재시도를 걸면 14초를 버리고 요청 3번을 더 쓰고도 똑같이 실패한다 — 무료 등급은 하루
    20회라 그 3번이 아깝다(2026-09-06에 겪었다).
    """

    class Failure(Exception):
        code = 429

    def test_a_daily_quota_error_is_not_retried(self):
        exc = self.Failure(
            "429 RESOURCE_EXHAUSTED ... quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        )
        self.assertTrue(gemini.is_daily_quota(exc))
        self.assertFalse(gemini.is_transient(exc))

    def test_a_burst_limit_is_still_retried(self):
        exc = self.Failure("429 RESOURCE_EXHAUSTED ... quotaId: GenerateRequestsPerMinutePerProject")
        self.assertFalse(gemini.is_daily_quota(exc))
        self.assertTrue(gemini.is_transient(exc))

    def test_other_statuses_are_never_daily_quota(self):
        class ServerError(Exception):
            code = 503

        self.assertFalse(gemini.is_daily_quota(ServerError("503 per day something")))

    def test_the_daily_quota_error_explains_what_to_do(self):
        # 재시도해도 소용없다는 걸 사람이 읽고 알아야 한다 — 모델 교체·결제·대기 셋 중 하나다.
        for hint in ("모델", "결제", "자정"):
            self.assertIn(hint, gemini.DAILY_QUOTA_HINT)

    def test_a_per_minute_embed_limit_is_not_mistaken_for_the_daily_one(self):
        """🔴 2026-09-09 실측. 임베딩이 분당 한도(100)에 걸렸는데 "오늘은 안 풀린다" 로 죽었다.

        옛 표식에 `free_tier_requests` 가 있었고, 분당 한도의 메트릭 이름
        `embed_content_free_tier_requests` 에 그 조각이 들어 있어서 일일로 읽혔다.
        무료 등급의 **모든** 한도 메시지가 저 조각을 갖고 있으니 애초에 일일을 가리키지 않았다.
        """
        exc = self.Failure(
            "429 RESOURCE_EXHAUSTED. Quota exceeded for metric:"
            " generativelanguage.googleapis.com/embed_content_free_tier_requests, limit: 100."
            " quotaId: EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier."
            " retryDelay: 36s"
        )
        self.assertFalse(gemini.is_daily_quota(exc))
        self.assertTrue(gemini.is_transient(exc))

    def test_a_free_tier_daily_limit_is_still_daily(self):
        # 반대 방향도 지킨다 — 무료 등급 일일 한도는 여전히 재시도하지 않는다.
        exc = self.Failure(
            "429 RESOURCE_EXHAUSTED. metric: generate_content_free_tier_requests."
            " quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        )
        self.assertTrue(gemini.is_daily_quota(exc))
        self.assertFalse(gemini.is_transient(exc))


class RetryDelayTest(unittest.TestCase):
    """🔴 서버가 "36초 뒤에" 라고 하면 그대로 기다린다.

    우리 백오프는 2·4·8초로 총 14초다. 분당 한도는 창이 끝나야 풀리므로, 36초를 기다려야 하는
    상황에서 네 번 다 실패하고 끝난다 — **기다릴 줄 몰라서** 실패하는 셈이다.
    """

    class Failure(Exception):
        code = 429

    def test_the_server_delay_is_used_when_present(self):
        exc = self.Failure("429 ... {'retryDelay': '36s'} ...")
        self.assertEqual(gemini.retry_after_sec(exc), 36.0)
        self.assertEqual(gemini.backoff_sec(exc, 1), 36.0)

    def test_a_quoteless_form_is_also_read(self):
        self.assertEqual(gemini.retry_after_sec(self.Failure('retryDelay: 7s')), 7.0)

    def test_without_a_server_delay_the_exponential_backoff_stands(self):
        exc = self.Failure("429 no hint")
        self.assertIsNone(gemini.retry_after_sec(exc))
        self.assertEqual(gemini.backoff_sec(exc, 1), gemini.BASE_BACKOFF_SEC)
        self.assertEqual(gemini.backoff_sec(exc, 3), gemini.BASE_BACKOFF_SEC * 4)

    def test_an_absurd_server_delay_is_capped(self):
        # 잡 하나가 무한히 붙어 있으면 워커가 하나뿐인 큐가 막힌다(jobs.py).
        exc = self.Failure("retryDelay: 3600s")
        self.assertEqual(gemini.retry_after_sec(exc), gemini.MAX_SERVER_WAIT_SEC)


if __name__ == "__main__":
    unittest.main()
