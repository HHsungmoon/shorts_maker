"""비용 추정 검증 (문서 §7).

🔴 여기서 지키는 핵심은 **thinking 토큰이 출력 단가로 계산되는가**다. 입력의 5배라
빠뜨리면 실제보다 훨씬 싸게 보이고, 그 숫자를 믿고 최적화 판단을 하게 된다.
"""

import unittest

from shorts_maker import pricing

from .support import make_config


class EstimateTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._dir = tempfile.TemporaryDirectory()
        self.cfg = make_config(
            Path(self._dir.name),
            price_input_usd_per_1m=1.0,
            price_output_usd_per_1m=10.0,
            usd_krw=1000.0,
        )

    def tearDown(self):
        self._dir.cleanup()

    def call(self, **kw):
        base = {"model": "m", "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0}
        base.update(kw)
        return base

    def test_bills_thinking_tokens_at_the_output_rate(self):
        result = pricing.estimate(
            [self.call(input_tokens=1_000_000, output_tokens=0, thinking_tokens=1_000_000)], self.cfg
        )
        # 입력 1M × $1 + (출력 0 + 사고 1M) × $10 = $11
        self.assertAlmostEqual(result["usd"], 11.0)
        self.assertEqual(result["billedOutputTokens"], 1_000_000)

    def test_converts_to_krw_with_the_configured_rate(self):
        result = pricing.estimate([self.call(input_tokens=1_000_000)], self.cfg)
        self.assertAlmostEqual(result["krw"], 1000.0)

    def test_reports_the_rate_it_used(self):
        # 숫자만 보여주면 언제 기준인지 알 수 없다.
        rate = pricing.estimate([], self.cfg)["rate"]
        self.assertEqual(rate["inputUsdPer1M"], 1.0)
        self.assertEqual(rate["outputUsdPer1M"], 10.0)

    def test_ignores_non_llm_stages(self):
        # stt/render 는 model 도 토큰도 없다. 호출 수에 섞이면 안 된다.
        result = pricing.estimate(
            [self.call(input_tokens=1000), {"stage": "stt", "model": None, "latency_ms": 100}], self.cfg
        )
        self.assertEqual(result["llmCalls"], 1)

    def test_handles_missing_token_fields(self):
        result = pricing.estimate([{"stage": "render", "model": None}], self.cfg)
        self.assertEqual(result["usd"], 0.0)
        self.assertEqual(result["llmCalls"], 0)


if __name__ == "__main__":
    unittest.main()


class BilledOutputTest(unittest.TestCase):
    def test_prefers_total_minus_input(self):
        # 🔴 구글은 output(사고 포함)으로 과금하는데, candidates 에 thoughts 가 이미 포함되는지가
        # SDK/모델마다 분명하지 않다. total - input 은 어느 쪽이든 맞는다.
        call = {"input_tokens": 1000, "output_tokens": 300, "thinking_tokens": 700, "total_tokens": 2000}
        self.assertEqual(pricing.billed_output_of(call), 1000)

    def test_falls_back_to_adding_when_total_is_missing(self):
        call = {"input_tokens": 1000, "output_tokens": 300, "thinking_tokens": 700}
        self.assertEqual(pricing.billed_output_of(call), 1000)

    def test_never_goes_negative(self):
        call = {"input_tokens": 5000, "total_tokens": 100}
        self.assertEqual(pricing.billed_output_of(call), 0)
