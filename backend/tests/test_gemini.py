"""일시 실패 판별 검증.

🔴 여기서 지키는 건 **무엇을 다시 부르지 않는가**다. 4xx 를 재시도하면 잘못된 요청을
반복하며 돈만 쓰고, 5xx 를 재시도하지 않으면 구글의 몇 초짜리 과부하에 잡 전체가 죽는다.
"""

import unittest

from shorts_maker import gemini


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

    def test_backoff_grows(self):
        waits = [gemini.BASE_BACKOFF_SEC * (2 ** (n - 1)) for n in range(1, gemini.MAX_ATTEMPTS)]
        self.assertEqual(waits, sorted(waits))
        self.assertLess(sum(waits), 60)


if __name__ == "__main__":
    unittest.main()
