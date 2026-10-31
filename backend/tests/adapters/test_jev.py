"""Jev 어댑터 — 응답 검증과 재시도.

🔴 여기서 지키는 건 둘이다. ① **빠진 판정을 숫자로 채우지 않는다** — 0 으로 채우면 "아니다" 와
"답을 못 받았다" 가 구분되지 않는다. ② **다시 부를 것만 다시 부른다** — 429·529 는 기다렸다 다시,
422(요청이 틀렸다)는 다시 보내도 같은 답이다.
"""

import email.message
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import jev

from ..support import make_config

NOUL = {"type": "noul", "instructions": "참인가"}
SCORE = {"type": "score", "instructions": "얼마나", "criteria": ["a", "b", "c", "d", "e"]}
CHOICE = {"type": "choice", "instructions": "어느 것", "criteria": {"L000": None, "L001": None}}


class ValidateTest(unittest.TestCase):
    def test_a_good_answer_set_comes_back_trimmed_to_what_we_use(self):
        checked = jev.validate(
            {"n": NOUL, "s": SCORE},
            {
                "n": {"type": "noul", "noul": 0.7},
                "s": {"type": "score", "score": 3.2, "confidence": 0.8, "legend": {}, "probabilities": {}},
            },
        )
        self.assertEqual(checked["n"], {"noul": 0.7})
        self.assertEqual(checked["s"], {"score": 3.2, "levels": 5, "confidence": 0.8})

    def test_a_missing_answer_is_an_error_not_a_zero(self):
        with self.assertRaises(jev.JevError):
            jev.validate({"n": NOUL, "m": NOUL}, {"n": {"type": "noul", "noul": 0.7}})

    def test_a_probability_outside_zero_to_one_is_refused(self):
        with self.assertRaises(jev.JevError):
            jev.validate({"n": NOUL}, {"n": {"type": "noul", "noul": 1.3}})

    def test_a_score_past_the_top_level_is_refused(self):
        with self.assertRaises(jev.JevError):
            jev.validate({"s": SCORE}, {"s": {"type": "score", "score": 4.5, "confidence": 0.5}})

    def test_choice_keeps_only_options_we_offered(self):
        # 🔴 화이트리스트 — 선택지 밖의 키를 받아 쓰면 없는 줄을 자르게 된다.
        checked = jev.validate({"c": CHOICE}, {"c": {
            "type": "choice", "choice": "L999", "confidence": 0.6,
            "probabilities": {"L000": 0.3, "L001": 0.6, "L999": 0.1},
        }})
        self.assertEqual(checked["c"]["probabilities"], {"L000": 0.3, "L001": 0.6})
        self.assertEqual(checked["c"]["choice"], "L001")

    def test_choice_with_no_offered_option_is_refused(self):
        with self.assertRaises(jev.JevError):
            jev.validate({"c": CHOICE}, {"c": {
                "type": "choice", "choice": "L999", "confidence": 0.6, "probabilities": {"L999": 1.0},
            }})

    def test_score_maps_onto_the_hundred_point_scale(self):
        self.assertEqual(jev.score_to_100(0.0, 5), 0)
        self.assertEqual(jev.score_to_100(2.0, 5), 50)
        self.assertEqual(jev.score_to_100(4.0, 5), 100)


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after:
        headers["retry-after"] = retry_after
    return urllib.error.HTTPError(jev.ENDPOINT, code, "x", headers, io.BytesIO(b'{"detail": "x"}'))


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


GOOD = {"model": "jev-1.14.0", "answers": {"n": {"type": "noul", "noul": 0.6}},
        "usage": {"input_tokens": 12, "output_tokens": 3}}


class SystemOneTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name), jev_api_key="test-key")
        sleep = mock.patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_no_key_fails_before_any_request(self):
        cfg = make_config(Path(self._tmp.name))
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(jev.JevError):
                jev.system_one(cfg, "state", {"n": NOUL})
        urlopen.assert_not_called()

    def test_rate_limit_is_retried(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[_http_error(429, "1"), _Response(GOOD)]) as urlopen:
            answers, usage, _, _ = jev.system_one(self.cfg, "state", {"n": NOUL})
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(answers["n"]["noul"], 0.6)
        self.assertEqual(usage["attempts"], 2)
        self.assertEqual(usage["model"], "jev-1.14.0")

    def test_overload_is_retried(self):
        with mock.patch("urllib.request.urlopen", side_effect=[_http_error(529), _Response(GOOD)]):
            answers, _, _, _ = jev.system_one(self.cfg, "state", {"n": NOUL})
        self.assertEqual(answers["n"]["noul"], 0.6)

    def test_a_malformed_request_is_not_retried(self):
        with mock.patch("urllib.request.urlopen", side_effect=[_http_error(422)]) as urlopen:
            with self.assertRaises(jev.JevError):
                jev.system_one(self.cfg, "state", {"n": NOUL})
        self.assertEqual(urlopen.call_count, 1)

    def test_the_pinned_model_is_what_gets_sent(self):
        # 🔴 별칭을 보내면 새 릴리스가 조용히 답한다 — 합격선은 버전마다 다시 잡는다.
        with mock.patch("urllib.request.urlopen", return_value=_Response(GOOD)) as urlopen:
            jev.system_one(self.cfg, "state", {"n": NOUL})
        sent = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(sent["model"], self.cfg.jev_model)
        self.assertNotIn("latest", sent["model"])


if __name__ == "__main__":
    unittest.main()
