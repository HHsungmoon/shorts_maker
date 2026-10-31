"""라이브 채팅 다시보기 → 질문 (tease §3-5, 2026-10-31).

끝난 라이브의 채팅에서 질문 꼴인 것만 골라 `questions` 에 `origin = 'live_chat'` 으로 넣는다. 그 뒤는
우리 페이지 질문과 똑같다 — [집계] 가 묶고, [답하기] 가 숏폼을 만든다.

🔴 크리에이터가 누를 때만 돈다. 🔴 거르기는 **코드가 한다(LLM 0회).** 채팅 수백 개를 모델에 보내 고르게
하면 매번 결과가 흔들리고 값이 든다. 애매한 것은 남겨 두고 [집계] · 사람의 거절에 맡긴다.

🔴 **작성자 이름을 남기지 않는다.** 받은 파일에는 이름이 들어 있어 읽자마자 지운다. 사람을 구분할 필요는
있다(같은 사람이 열 번 물은 것을 열 명으로 세면 안 된다) — 채널 id 의 해시만 `viewer_id` 로 둔다.
"""

import collections
import hashlib
import re
import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import ytdlp

# 질문 꼴. 물음표(전각 포함) · 의문형 어미 · 묻는 말.
# 쏘카 LIVE(2026-10-31) 464개에 대보면 약 90개가 걸린다. 방송 자체를 묻는 말("다시보기 올라오나요?")도
# 걸리는데 일부러 둔다 — [집계] 가 한데 묶고 영상에 답이 없으면 "답할 구간 없음" 으로 끝난다(tease §3-4).
QUESTION = re.compile(r"[?？]|나요|까요|가요|인가|는지|은지|ㄴ지|어떻게|어떤|어디|언제|얼마|궁금|뭔가요|뭐예요|무엇")

# "저도 궁금합니다" — 질문이 아니라 동의다. 앞의 질문에 좋아요를 누른 것과 같은데 누구 것인지 모른다.
ME_TOO = re.compile(r"^[(\[]?\s*(저도|나도|저두|저도요)\s*(궁금|요)")

# 이보다 짧으면 질문이 될 수 없다("궁금!", "언제?"). 공백 뺀 글자 수.
MIN_CHARS = 6

# 🔴 **같은 글을 이만큼 이상 반복한 계정은 안내 계정으로 본다.** 다시보기에는 모더레이터 표식이 빠져 오고,
# 안내를 쓰는 계정이 채널 주인과 다르다. 쏘카 LIVE 실측: 안내 계정은 55개 중 같은 글이 15개("궁금한 점을
# 채팅창에 남겨주시면…" 이 질문 꼴이라 그대로 두면 클러스터 하나를 차지한다), 가장 많이 쓴 시청자는 96개 중 2개.
ANNOUNCER_REPEATS = 5


class LiveChatError(RuntimeError):
    """사람이 고칠 수 있는 이유로 가져올 수 없다. 메시지를 그대로 화면에 보인다."""


def viewer_id(author_id: str) -> str:
    """채팅 작성자를 가리키는 값. 🔴 채널 id 를 그대로 두지 않는다 — 그 채널을 다시 찾아갈 수 있다."""
    return "yt:" + hashlib.sha256(author_id.encode()).hexdigest()[:16]


def pick(messages: list[ytdlp.ChatMessage]) -> tuple[list[ytdlp.ChatMessage], dict[str, int]]:
    """질문으로 들일 메시지와, 뺀 것의 이유별 개수. 순서는 채팅 순서 그대로다."""
    repeats: collections.Counter[str] = collections.Counter()
    for author, count in collections.Counter((m.author_id, m.text) for m in messages).items():
        if count > 1:
            repeats[author[0]] += count
    announcers = {author for author, count in repeats.items() if count >= ANNOUNCER_REPEATS}

    kept: list[ytdlp.ChatMessage] = []
    skipped: collections.Counter[str] = collections.Counter()
    seen: set[tuple[str, str]] = set()
    for message in messages:
        text = message.text
        if message.staff:
            reason = "staff"
        elif message.author_id in announcers:
            reason = "announcer"
        elif text.startswith("@"):
            # 누군가에게 단 답글. 운영자의 답이거나("@경진김 이력서는 자유양식입니다") 앞 말을 잇는 말이라
            # 혼자서는 질문이 안 된다.
            reason = "reply"
        elif len("".join(text.split())) < MIN_CHARS:
            reason = "short"
        elif ME_TOO.match(text):
            reason = "me_too"
        elif not QUESTION.search(text):
            reason = "not_question"
        elif (message.author_id, text) in seen:
            # 답이 없자 같은 질문을 또 쓴 것. 한 사람의 한 질문이다.
            reason = "duplicate"
        else:
            seen.add((message.author_id, text))
            kept.append(message)
            continue
        skipped[reason] += 1
    return kept, dict(skipped)


def import_replay(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """채팅 다시보기를 받아 질문을 넣는다. 몇 번 불러도 같다(메시지 id 로 중복을 막는다)."""
    source = conn.execute("select youtube_id from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise LiveChatError(f"source {source_id} 없음")
    if not source["youtube_id"]:
        raise LiveChatError("유튜브 영상 id 가 없는 원본입니다. 영상 준비 탭에서 유튜브 주소를 먼저 넣어 주세요")
    # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약). 긴 방송이면 받는 데 몇십 초가 걸린다.
    conn.commit()

    started = time.monotonic()
    path = None
    try:
        path, owner_id = ytdlp.fetch_live_chat(cfg, source["youtube_id"])
        messages = ytdlp.parse_live_chat(path, owner_id)
    except ytdlp.DownloadError as exc:
        _record(conn, source_id, started, {"youtube_id": source["youtube_id"]}, f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise
    finally:
        # 🔴 이름이 든 원본은 남기지 않는다(모듈 머리 주석).
        if path is not None:
            path.unlink(missing_ok=True)

    kept, skipped = pick(messages)
    inserted = 0
    for message in kept:
        row = conn.execute(
            """insert into questions (source_id, text, viewer_id, origin, external_id, asked_at_sec)
               values (%s, %s, %s, 'live_chat', %s, %s)
               on conflict (source_id, origin, external_id) where external_id is not null do nothing
               returning id""",
            (source_id, message.text, viewer_id(message.author_id), message.id, message.offset_sec),
        ).fetchone()
        inserted += row is not None

    result = {
        "messages": len(messages),
        "questions": len(kept),
        "inserted": inserted,
        # 지난번에 이미 가져온 것. 다시 눌렀을 때 0 이 아니라 이 숫자가 늘어야 "중복이 막혔다" 가 보인다.
        "already": len(kept) - inserted,
        "skipped": skipped,
    }
    _record(conn, source_id, started, result, None)
    conn.commit()
    return result


def _record(conn: psycopg.Connection, source_id: int, started: float, params: dict, error: str | None) -> None:
    # 🔴 가져오기도 stage_calls 에 남긴다(규약: 모든 단계). LLM 이 아니라 model·토큰은 null 이다.
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'livechat', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )
