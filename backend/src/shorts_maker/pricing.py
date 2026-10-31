"""토큰 → 비용 추정 (문서 §7).

🔴 추정이다. 단가는 모델마다 다르고 자주 바뀐다(문서도 "가격은 자주 바뀐다"고 적어놨다).
그래서 계산에 쓴 단가를 결과에 함께 실어 화면이 같이 보여주게 한다 — 숫자만 보여주면
언제 기준인지 알 수 없다.

🔴 **thinking 토큰은 출력 단가로 과금된다.** 입력의 5배라 여기서 비용이 튄다(§7).
따로 세지 않으면 왜 비싼지 알 수 없어서 stage_calls 에 별도 컬럼으로 두었다.
"""

from . import config


def billed_output_of(call: dict) -> int:
    """출력으로 과금되는 토큰 수.

    🔴 구글의 과금 단위는 "input" 과 "output(사고 토큰 포함)" 둘뿐이다. 그런데
    candidates_token_count 에 thoughts 가 이미 포함되는지가 SDK/모델마다 분명하지 않아,
    단순히 더하면 이중으로 셀 수 있다. **total - input 으로 구하면 어느 쪽이든 맞는다.**
    total 이 없는 옛 기록만 더하기로 되돌아간다.
    """
    total = call.get("total_tokens")
    if total:
        return max(0, int(total) - int(call.get("input_tokens") or 0))
    return int(call.get("output_tokens") or 0) + int(call.get("thinking_tokens") or 0)


def is_jev(call: dict) -> bool:
    """Jev 호출인가. 🔴 단계 이름이 아니라 **모델 이름**으로 가른다 — `judge` 는 두 엔진이 같이 쓴다."""
    return str(call.get("model") or "").startswith("jev-")


def estimate(calls: list[dict], cfg: config.Config) -> dict:
    """🔴 Jev 행은 Jev 단가로 센다(입력만, 출력 무료). Gemini 단가로 세면 Jev 판정 수십 회가 Gemini
    수십 회처럼 보여 "Jev 로 바꿨더니 비싸졌다" 는 거짓 숫자가 나온다."""
    gemini_calls = [c for c in calls if not is_jev(c)]
    jev_calls = [c for c in calls if is_jev(c)]
    input_tokens = sum(c.get("input_tokens") or 0 for c in calls)
    output_tokens = sum(c.get("output_tokens") or 0 for c in calls)
    thinking_tokens = sum(c.get("thinking_tokens") or 0 for c in calls)
    billed_output = sum(billed_output_of(c) for c in gemini_calls)
    jev_input = sum(c.get("input_tokens") or 0 for c in jev_calls)

    usd = (
        (input_tokens - jev_input) / 1_000_000 * cfg.price_input_usd_per_1m
        + billed_output / 1_000_000 * cfg.price_output_usd_per_1m
        + jev_input / 1_000_000 * cfg.price_jev_input_usd_per_1m
    )
    llm_calls = [c for c in calls if c.get("model") and (c.get("input_tokens") or 0) > 0]
    return {
        "llmCalls": len(llm_calls),
        "jevCalls": sum(1 for c in llm_calls if is_jev(c)),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "thinkingTokens": thinking_tokens,
        "billedOutputTokens": billed_output,
        "usd": round(usd, 6),
        "krw": round(usd * cfg.usd_krw, 2),
        "rate": {
            "inputUsdPer1M": cfg.price_input_usd_per_1m,
            "outputUsdPer1M": cfg.price_output_usd_per_1m,
            "jevInputUsdPer1M": cfg.price_jev_input_usd_per_1m,
            "usdKrw": cfg.usd_krw,
            "model": cfg.gemini_model,
            "jevModel": cfg.jev_model,
        },
    }
