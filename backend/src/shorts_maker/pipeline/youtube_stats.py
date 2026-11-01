"""유튜브 성과 — 올린 숏폼이 어떻게 되고 있나 (tease §7-6, 2026-11-01).

올린 숏폼(`clip_uploads` 의 DONE)마다 두 층을 읽어 저장한다.

| 층 | 어디서 | 늦음 |
|---|---|---|
| 지금 수치 — 조회수 · 좋아요 · 댓글 수 · 지금 공개 범위 | Data API `videos.list` | 바로 |
| 분석 — 조회 · 참여 조회 · 시청 시간 · 평균 시청 · 공유 · 구독(합계 + 날짜별) | Analytics `reports.query` | 48~72시간 |

🔴 **우리 시청자 퍼널(`answers/events.py`, `viewer_events`)과 섞지 않는다**(사용자 결정 2026-11-01). 시청자 페이지는
없어질 수 있다 — 그때 이 모듈만 남아도 돌아야 한다. 그래서 여기서 `events` 를 import 하지 않고, 두 숫자를 더하지 않는다.

🔴 사람이 [성과 가져오기] 를 누를 때만 유튜브를 부른다(잡). 탭은 DB 만 읽는다(`view`) — 보기 전용 방문자가 탭을
열 때마다 쿼터를 쓰지 않는다.

🔴 **지금 연결된 채널에 올린 것만** 읽는다. 분석은 `channel==MINE` 이라 다른 채널 영상은 못 읽는다.

못 하는 것: 숏폼별 원본 유입. 트래픽 소스에 "숏폼의 관련 동영상 링크에서 왔다" 는 값이 없다(tease §7-6).
"""

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import youtube
from . import upload, youtube_channel

# 🔴 Analytics 의 날(`day`)은 태평양 시간이다(공식 문서). 시작·끝 날짜도 같은 시계로 정한다 — 한국 날짜로
# 정하면 올린 날 오전(한국)의 조회가 "전날" 로 빠질 수 있다.
PACIFIC = ZoneInfo("America/Los_Angeles")

# 지표 → 저장 열. 합계와 날짜별이 같은 목록을 쓴다.
# 🔴 views 는 2025-03-31 부터 Shorts 재생이 시작될 때마다(다시 재생 포함) 센다. 예전 방식이 engagedViews 다.
# 평균 시청 길이·비율은 참여 조회 기준이다(2026-08-27 변경 이력).
COLUMN = {
    "views": "views",
    "engagedViews": "engaged_views",
    "estimatedMinutesWatched": "minutes_watched",
    "averageViewDuration": "avg_view_sec",
    "averageViewPercentage": "avg_view_pct",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
}
METRICS = tuple(COLUMN)
COLUMNS = tuple(COLUMN.values())

# 여러 숏폼을 더해도 뜻이 있는 것. 평균(길이·비율)은 더하지 않는다.
ADDITIVE = ("views", "engaged_views", "minutes_watched", "likes", "comments", "shares", "subscribers_gained")


class StatsError(RuntimeError):
    """사람이 고칠 수 있는 이유로 가져올 수 없다. 메시지를 그대로 화면에 보인다."""


def uploads_of(conn: psycopg.Connection, source_id: int) -> list[dict]:
    """이 원본에서 올린 숏폼(성공한 것), 먼저 올린 것부터."""
    return [
        dict(r)
        for r in conn.execute(
            """select id, clip_id, title, privacy, youtube_video_id, channel_id, created_at
                 from clip_uploads
                where source_id = %s and status = 'DONE' and youtube_video_id is not null
                order by id""",
            (source_id,),
        )
    ]


def check_ready(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """잡을 띄우기 **전에** 막을 것을 막는다(네트워크 없음). 연결된 채널을 돌려준다."""
    try:
        connected = youtube_channel.analytics_access(conn, cfg)
    except youtube_channel.NoAnalyticsAccess as exc:
        raise StatsError(str(exc)) from exc
    if not uploads_of(conn, source_id):
        raise StatsError("이 영상에서 유튜브에 올린 숏폼이 없습니다. 공개 탭에서 [유튜브에 올리기] 를 먼저 하세요")
    return connected


def refresh(conn: psycopg.Connection, cfg: config.Config, source_id: int, today: date | None = None) -> dict:
    """[성과 가져오기] — 이 원본의 숏폼 성과를 유튜브에서 다시 읽어 덮어쓴다.

    네트워크를 다 마친 뒤에 쓴다. 중간에 실패하면 아무것도 바꾸지 않고 `stage_calls` 에 이유만 남긴다 —
    반만 새 값인 탭은 어느 숫자가 언제 것인지 알 수 없다.
    """
    connected = check_ready(conn, cfg, source_id)
    uploads = uploads_of(conn, source_id)
    refresh_token = youtube_channel.refresh_token(conn)
    # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약).
    conn.commit()

    today = today or datetime.now(PACIFIC).date()
    started = time.monotonic()
    calls = 0
    try:
        token = youtube.access_token(cfg, refresh_token)
        channel_id = connected["channel_id"]
        if not channel_id:
            # 다시 연결하면 채널 id 가 비워진다(save_channel). 다른 채널 영상을 걸러야 하니 먼저 알아 둔다.
            mine = youtube.my_channel(token)
            calls += 1
            if mine is not None:
                youtube_channel.note_channel(conn, *mine)
                channel_id = mine[0]
        # 채널을 모르는 옛 기록(channel_id 없음)은 지금 채널 것으로 본다 — 아니면 분석 호출이 빈 값을 준다.
        ours = [u for u in uploads if not channel_id or not u["channel_id"] or u["channel_id"] == channel_id]
        now = youtube.video_stats(token, [u["youtube_video_id"] for u in ours])
        calls += -(-len(ours) // youtube.VIDEO_IDS_PER_CALL)
        fetched = []
        for one in ours:
            video_id = one["youtube_video_id"]
            if video_id not in now:
                # 유튜브에서 지웠거나 볼 수 없게 됐다. 분석도 부르지 않는다.
                fetched.append((one, None, None, []))
                continue
            # 올린 날(태평양)부터 오늘까지. 처리가 덜 된 최근 며칠은 유튜브가 알아서 뺀다.
            start = one["created_at"].astimezone(PACIFIC).date().isoformat()
            where = f"video=={video_id}"
            totals = youtube.analytics_report(token, start, today.isoformat(), METRICS, filters=where)
            daily = youtube.analytics_report(
                token, start, today.isoformat(), METRICS, dimensions="day", filters=where, sort="day"
            )
            calls += 2
            fetched.append((one, now[video_id], totals[0] if totals else None, daily))
    except Exception as exc:
        _record(conn, source_id, started, {"shorts": len(uploads), "calls": calls}, f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise

    refreshed = missing = 0
    through_days: list[date] = []
    for one, stats, totals, daily in fetched:
        if stats is None:
            missing += 1
        else:
            refreshed += 1
        through = _save(conn, one["id"], stats, totals, daily)
        if through is not None:
            through_days.append(through)

    result = {
        "shorts": len(uploads),
        "refreshed": refreshed,
        # 유튜브에서 찾지 못한 것(지웠거나 볼 수 없게 됐다).
        "missing": missing,
        # 지금 연결된 채널이 아닌 채널에 올린 것 — 읽을 수 없어 건너뛰었다.
        "otherChannel": len(uploads) - len(ours),
        # 분석 수치가 이 날(태평양)까지다. 아직 처리된 날이 없으면 None — 올린 지 이틀이 안 됐으면 정상이다.
        "through": max(through_days).isoformat() if through_days else None,
        "calls": calls,
    }
    _record(conn, source_id, started, result, None)
    conn.commit()
    return result


def _save(conn: psycopg.Connection, upload_id: int, stats, totals: dict | None, daily: list[dict]) -> date | None:
    """한 숏폼의 최신 줄과 날짜별 행을 덮어쓴다. 분석이 이 날까지라는 날짜를 돌려준다."""
    days = [(_day(row["day"]), row) for row in daily if row.get("day")]
    through = max((d for d, _ in days), default=None)
    found = stats is not None
    values = {column: _metric(totals, metric) for metric, column in COLUMN.items()}
    conn.execute(
        f"""insert into short_stats (upload_id, found, privacy, view_count, like_count, comment_count,
                                     {", ".join(COLUMNS)}, analytics_through, fetched_at)
            values (%s, %s, %s, %s, %s, %s, {", ".join(["%s"] * len(COLUMNS))}, %s, now())
            on conflict (upload_id) do update set
                found = excluded.found, privacy = excluded.privacy, view_count = excluded.view_count,
                like_count = excluded.like_count, comment_count = excluded.comment_count,
                {", ".join(f"{c} = excluded.{c}" for c in COLUMNS)},
                analytics_through = excluded.analytics_through, fetched_at = now()""",
        (
            upload_id, found,
            stats.privacy if found else None,
            stats.view_count if found else None,
            stats.like_count if found else None,
            stats.comment_count if found else None,
            *values.values(), through,
        ),
    )
    for day, row in days:
        conn.execute(
            f"""insert into short_stats_daily (upload_id, day, {", ".join(COLUMNS)})
                values (%s, %s, {", ".join(["%s"] * len(COLUMNS))})
                on conflict (upload_id, day) do update set
                    {", ".join(f"{c} = excluded.{c}" for c in COLUMNS)}""",
            (upload_id, day, *(_metric(row, metric) for metric in METRICS)),
        )
    return through


def _metric(row: dict | None, name: str):
    """응답에 그 지표가 없으면 None. 🔴 0 과 없음을 가른다 — "아직 처리 전" 을 0회로 보이면 거짓말이다."""
    if row is None:
        return None
    value = row.get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _record(conn: psycopg.Connection, source_id: int, started: float, params: dict, error: str | None) -> None:
    # 🔴 성과 가져오기도 stage_calls 에 남긴다(규약: 모든 단계). LLM 이 아니라 model·토큰은 null 이다.
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'analytics', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )


# ---------------------------------------------------------------- 탭에 줄 모양

def view(conn: psycopg.Connection, source_id: int) -> dict:
    """[유튜브 성과] 탭 — 이 원본에서 올린 숏폼마다 마지막으로 가져온 수치. 🔴 유튜브를 부르지 않는다."""
    rows = conn.execute(
        f"""select u.id, u.clip_id, u.title, u.privacy as uploaded_privacy, u.youtube_video_id, u.channel_id,
                   u.created_at,
                   s.upload_id is not null as fetched, s.found, s.privacy, s.view_count, s.like_count,
                   s.comment_count, {", ".join(f"s.{c}" for c in COLUMNS)}, s.analytics_through, s.fetched_at
              from clip_uploads u
              left join short_stats s on s.upload_id = u.id
             where u.source_id = %s and u.status = 'DONE' and u.youtube_video_id is not null
             order by u.id desc""",
        (source_id,),
    ).fetchall()
    ids = [r["id"] for r in rows]
    daily: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        for d in conn.execute(
            "select upload_id, day, views, engaged_views from short_stats_daily"
            " where upload_id = any(%s) order by day",
            (ids,),
        ):
            daily[d["upload_id"]].append(
                {"day": d["day"].isoformat(), "views": d["views"], "engagedViews": d["engaged_views"]}
            )

    connected = youtube_channel.channel(conn)
    channel_id = connected["channel_id"] if connected else None
    shorts = []
    for r in rows:
        stats = None
        if r["fetched"]:
            stats = {
                "found": r["found"],
                "privacy": r["privacy"],
                "viewCount": r["view_count"],
                "likeCount": r["like_count"],
                "commentCount": r["comment_count"],
                **{_camel(c): r[c] for c in COLUMNS},
                "analyticsThrough": r["analytics_through"].isoformat() if r["analytics_through"] else None,
                "fetchedAt": r["fetched_at"],
            }
        links = upload.links(r["youtube_video_id"])
        shorts.append({
            "uploadId": r["id"],
            "clipId": r["clip_id"],
            "title": r["title"],
            "uploadedAt": r["created_at"],
            "uploadedPrivacy": r["uploaded_privacy"],
            "videoId": r["youtube_video_id"],
            "shortsUrl": links["shorts_url"],
            "studioUrl": links["studio_url"],
            # 지금 연결된 채널이 아닌 곳에 올렸다 — [성과 가져오기] 가 건너뛴다.
            "otherChannel": bool(channel_id and r["channel_id"] and r["channel_id"] != channel_id),
            "stats": stats,
            "daily": daily[r["id"]],
        })

    found = [s["stats"] for s in shorts if s["stats"] and s["stats"]["found"]]
    totals = None
    if found:
        totals = {
            "shorts": len(found),
            "viewCount": _sum(found, "viewCount"),
            "likeCount": _sum(found, "likeCount"),
            "commentCount": _sum(found, "commentCount"),
            **{_camel(c): _sum(found, _camel(c)) for c in ADDITIVE},
        }
    fetched_at = [s["stats"]["fetchedAt"] for s in shorts if s["stats"]]
    through = [s["stats"]["analyticsThrough"] for s in shorts if s["stats"] and s["stats"]["analyticsThrough"]]
    return {
        "shorts": shorts,
        "totals": totals,
        "fetchedAt": max(fetched_at) if fetched_at else None,
        "through": max(through) if through else None,
        "channel": {
            "connected": connected is not None,
            "canAnalytics": bool(connected and connected["can_analytics"]),
            "channelTitle": connected["channel_title"] if connected else None,
        },
    }


def _sum(rows: list[dict], key: str):
    """더할 값이 하나도 없으면 None(아직 분석 전). 🔴 0 으로 보이면 "아무도 안 봤다" 로 읽힌다."""
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) if values else None


def _camel(column: str) -> str:
    head, *rest = column.split("_")
    return head + "".join(part.capitalize() for part in rest)
