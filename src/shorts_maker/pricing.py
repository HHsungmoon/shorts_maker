"""토큰 → 비용 추정 (문서 §7).

🔴 추정이다. 단가는 모델마다 다르고 자주 바뀐다(문서도 "가격은 자주 바뀐다"고 적어놨다).
그래서 계산에 쓴 단가를 결과에 함께 실어 화면이 같이 보여주게 한다 — 숫자만 보여주면
언제 기준인지 알 수 없다.

🔴 **thinking 토큰은 출력 단가로 과금된다.** 입력의 5배라 여기서 비용이 튄다(§7).
따로 세지 않으면 왜 비싼지 알 수 없어서 stage_calls 에 별도 컬럼으로 두었다.
"""

from . import config


def estimate(calls: list[dict], cfg: config.Config) -> dict:
    input_tokens = sum(c.get("input_tokens") or 0 for c in calls)
    output_tokens = sum(c.get("output_tokens") or 0 for c in calls)
    thinking_tokens = sum(c.get("thinking_tokens") or 0 for c in calls)
    billed_output = output_tokens + thinking_tokens

    usd = (
        input_tokens / 1_000_000 * cfg.price_input_usd_per_1m
        + billed_output / 1_000_000 * cfg.price_output_usd_per_1m
    )
    llm_calls = [c for c in calls if c.get("model") and (c.get("input_tokens") or 0) > 0]
    return {
        "llmCalls": len(llm_calls),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "thinkingTokens": thinking_tokens,
        "billedOutputTokens": billed_output,
        "usd": round(usd, 6),
        "krw": round(usd * cfg.usd_krw, 2),
        "rate": {
            "inputUsdPer1M": cfg.price_input_usd_per_1m,
            "outputUsdPer1M": cfg.price_output_usd_per_1m,
            "usdKrw": cfg.usd_krw,
            "model": cfg.gemini_model,
        },
    }
