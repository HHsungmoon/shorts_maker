"""클립 안의 장면 경계 — 숏폼 편집이 장면마다 창을 두려고 쓴다(tease §5-10 "장면마다 창", 2026-11-07).

대담 영상(잡소리)은 강연이어도 편집자의 컷이 있다. 클립 4(30초)는 3명 → 말하는 사람 클로즈업 → 3명 → 2명 → 3명 →
클로즈업으로 다섯 번 바뀐다. 창 하나로는 한 장면에 맞추면 다른 장면이 어긋나서, 장면마다 창을 둔다.

**장면 점수**(ffmpeg select 의 `scene`)는 프레임마다 바로 앞 프레임과 그림이 얼마나 달라졌나를 0~1 로 매긴 값이다.
클립 4 실측(2026-11-07):

    컷 다섯     0.39 · 0.42 · 0.41 · 0.39 · 0.42   (8.27 · 10.94 · 13.56 · 19.28 · 25.16초 — 사람이 본 8 · 11 · 14 · 20 · 25초)
    컷 아닌 곳  중앙값 0.0004 · 가장 큰 것 0.043

그래서 문턱 0.2 — 위아래로 다섯 배 여유가 있다. 0.05~0.2 는 "애매한 곳" 으로 기록만 한다(문턱을 다시 잴 근거).

🔴 약점: 디졸브(서서히 겹쳐 바뀜)는 점수가 여러 프레임에 흩어져 놓칠 수 있고, 플래시는 잘못 잡을 수 있다. 그래서
경계는 **초기값**이다 — 편집 화면에서 사람이 지우고 나눈다. 고친 경계는 `layout.windows` 의 start 로 남는다.

🔴 경계 시각은 파일의 pts 에서 온다. LLM 은 이 단계에 없다.

이 모듈은 파이프라인 단계 `[2] 샷 검출`(FILM 몫, make_shorts §4)이 아니다 — 원본 전체가 아니라 **클립 구간만** 본다.
"""

import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ffmpeg

THRESHOLD = 0.2
# 이 위는 기록만 한다. 문턱 근처에 몰리는 영상이 나오면 여기서 보인다.
FLOOR = 0.05
# 이보다 짧은 장면은 만들지 않는다. 컷 직후 다시 컷(플래시 · 한두 프레임 끼어든 화면)은 앞 장면에 붙인다 —
# 0.5초 장면에 상자를 따로 잡을 사람은 없고, 잡아도 눈이 못 따라간다.
MIN_SCENE_SEC = 0.5


def detect(
    conn: psycopg.Connection, cfg: config.Config, *, source_id: int, clip_id: int, source_path: str,
    ranges: list[tuple[float, float]],
) -> dict:
    """클립(조각들)의 장면 경계. 돌려주는 것:

    - starts: 장면마다 클립 안의 시작 초(0 부터). 조각이 바뀌는 곳은 언제나 경계다 — 원본의 다른 자리다
    - cuts / weak: 찾은 컷과 애매한 곳 [{t, score}] (클립 안의 초)
    - headFlash / tailFlash: 클립 처음 · 끝에 다른 장면이 잠깐 끼어 있으면 그 길이(초). 클립 4 는 끝 0.05초(한 프레임)에
      다음 장면이 번쩍인다 — 장면으로 나누지는 않고 알리기만 한다
    - duration: 클립 길이(조각 합, 브릿지 제외)

    stage_calls 에 'scenes' 로 남긴다(실패도).
    """
    source = cfg.source_file(source_path)
    started = time.monotonic()
    cuts: list[dict] = []
    weak: list[dict] = []
    joins: list[float] = []
    offset = 0.0
    try:
        for index, (start, end) in enumerate(ranges):
            length = end - start
            if index:
                joins.append(round(offset, 3))
            for moment, score in ffmpeg.scene_scores(str(source), start, length, FLOOR, cfg.ffmpeg_bin):
                # 구간 첫 프레임의 점수는 "앞 프레임" 이 구간 밖이라 뜻이 없다.
                if moment <= 0 or moment >= length:
                    continue
                hit = {"t": round(offset + moment, 3), "score": round(score, 3)}
                (cuts if score >= THRESHOLD else weak).append(hit)
            offset += length
    except ffmpeg.FfmpegError as exc:
        _record(conn, source_id, clip_id, started, {"clip_id": clip_id}, str(exc)[-500:])
        conn.commit()
        raise
    duration = round(offset, 3)

    starts, head_flash, tail_flash = boundaries([c["t"] for c in cuts], joins, duration)
    result = {
        "starts": starts,
        "cuts": cuts,
        "weak": weak,
        "headFlash": head_flash,
        "tailFlash": tail_flash,
        "duration": duration,
    }
    _record(
        conn, source_id, clip_id, started,
        {"clip_id": clip_id, "duration_sec": duration, "threshold": THRESHOLD, "scenes": len(starts),
         "cuts": cuts, "weak": weak, "head_flash": head_flash, "tail_flash": tail_flash},
        None,
    )
    conn.commit()
    return result


def boundaries(cuts: list[float], joins: list[float], duration: float) -> tuple[list[float], float | None, float | None]:
    """컷 · 조각 이음매로 장면 시작들을 만든다. (starts, 처음 번쩍임 길이, 끝 번쩍임 길이).

    - 조각 이음매는 언제나 경계다
    - 클립 처음 MIN_SCENE_SEC 안의 컷 = 처음 몇 프레임이 앞 장면이다 → 번쩍임으로 알리고 나누지 않는다. 끝도 같다
    - 바로 앞 경계와 MIN_SCENE_SEC 안이면 버린다(플래시 · 같은 컷이 두 프레임에 걸쳐 잡힌 것)
    """
    head = tail = None
    starts = [0.0]
    for moment in sorted(set(cuts) | set(joins)):
        is_join = moment in joins
        if not is_join and moment < MIN_SCENE_SEC:
            head = round(moment, 3)
            continue
        if not is_join and duration - moment < MIN_SCENE_SEC:
            tail = round(duration - moment, 3)
            continue
        if moment - starts[-1] < MIN_SCENE_SEC:
            if is_join:
                # 이음매는 지킨다 — 바로 앞의 컷을 버리고 이음매를 경계로 둔다.
                starts[-1] = moment if len(starts) > 1 else starts[-1]
            continue
        starts.append(round(moment, 3))
    return starts, head, tail


def _record(
    conn: psycopg.Connection, source_id: int, clip_id: int, started: float, params: dict, error: str | None
) -> None:
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'scenes', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )
