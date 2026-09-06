import argparse
import dataclasses
import json
import sys
from pathlib import Path

import psycopg

from .. import config, doctor
from ..adapters import ffmpeg, gemini
from ..answers import clusters, embeddings
from ..pipeline import cutting, ingest, ranking, render, segmentation, stt
from ..db import store


# 🔴 database_url 은 출력하지 않는다 — 비밀번호가 들어 있다. 어디에 붙었는지는 doctor 가 호스트·DB 명만 보여준다.
DB_DOWN_HINT = "Postgres 에 붙지 못했다 — docker compose up -d db"


def _cmd_db_init(cfg: config.Config) -> int:
    # 마이그레이션 파일을 번호 순으로 적용한다(store.apply_schema). 이미 적용된 건 건너뛰므로 여러 번 돌려도 된다.
    with store.connect(cfg.database_url) as conn:
        version = store.apply_schema(conn)
        tables = store.existing_tables(conn)
    print(f"schema v{version} · 테이블 {len(tables)}개: {', '.join(tables)}")
    return 0


def _cmd_db_status(cfg: config.Config) -> int:
    # SQLite 시절의 "파일이 있나" 검사는 접속 시도로 바뀌었다. 실패의 대부분은 db 컨테이너가 안 떠 있는 것.
    try:
        with store.connect(cfg.database_url) as conn:
            version = store.schema_version(conn)
            counts = store.row_counts(conn)
    except psycopg.OperationalError:
        print(DB_DOWN_HINT)
        return 1
    print(f"schema v{version}")
    if not counts:
        print("테이블 없음 — `sm db init` 으로 만든다")
        return 1
    for table, n in counts.items():
        print(f"  {table:<17} {n}")
    return 0


def _cmd_db_reset(cfg: config.Config, confirmed: bool) -> int:
    if not confirmed:
        # 되돌릴 수 없는 삭제라 기본값으로 두지 않는다. A 단계에선 자주 쓰지만 자주 쓴다고
        # 안전해지는 건 아니다.
        print("public 스키마를 통째로 지우고(drop schema public cascade) 다시 만든다 — 되돌릴 수 없다."
              " 실행하려면 --yes 를 붙인다")
        return 1
    store.reset(cfg.database_url)
    print("public 스키마 삭제 완료 — 되돌릴 수 없다")
    return _cmd_db_init(cfg)


def _cmd_source_add(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        source_id = ingest.add_source(
            conn, cfg, args.path, args.title, args.type, args.origin, args.context,
            stt.check_language(args.language),
        )
        row = conn.execute("select * from sources where id = %s", (source_id,)).fetchone()
    minutes, seconds = divmod(int(row["duration_sec"]), 60)
    print(f"source {source_id} 등록: {row['title']}")
    print(f"  type   {row['content_type']}")
    print(f"  길이   {minutes}분 {seconds}초")
    print(f"  path   {cfg.source_file(row['path'])}")
    print(f"  origin {row['origin'] or '-'}")
    return 0


def _cmd_source_list(cfg: config.Config) -> int:
    with store.connect(cfg.database_url) as conn:
        rows = conn.execute(
            "select id, title, content_type, duration_sec, status from sources order by id"
        ).fetchall()
    if not rows:
        print("등록된 원본 없음 — `sm source add` 로 추가한다")
        return 1
    for r in rows:
        minutes = int(r["duration_sec"] or 0) // 60
        print(f"  [{r['id']}] {r['title']}  ({r['content_type']}, {minutes}분, {r['status']})")
    return 0


def _cmd_chunk_add(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        made = ingest.add_chunks(
            conn, cfg, args.source_id, args.start, args.end, replace=args.replace
        )
        rows = conn.execute(
            "select idx, start_sec, end_sec from chunks where source_id = %s order by idx",
            (args.source_id,),
        ).fetchall()
    # 🔴 청크 개수는 메모리 상한이 정한다 — 사용자가 고르는 값이 아니다(ingest.plan_chunks).
    print(f"조각 {len(made)}개 생성")
    for row in rows:
        print(f"  [{row['idx']}] {row['start_sec']:.0f}s ~ {row['end_sec']:.0f}s"
              f" ({(row['end_sec'] - row['start_sec']) / 60:.0f}분)")
    return 0


def _cmd_chunk_list(cfg: config.Config, source_id: int | None) -> int:
    query = "select * from chunks"
    params: tuple = ()
    if source_id is not None:
        query += " where source_id = %s"
        params = (source_id,)
    with store.connect(cfg.database_url) as conn:
        rows = conn.execute(query + " order by source_id, idx", params).fetchall()
    if not rows:
        print("청크 없음 — `sm chunk add` 로 만든다")
        return 1
    for r in rows:
        span = (r["end_sec"] - r["start_sec"]) / 60
        print(f"  [{r['id']}] source {r['source_id']} idx {r['idx']}  "
              f"{r['start_sec']:.0f}s~{r['end_sec']:.0f}s ({span:.1f}분)  {r['path']}")
    return 0


def _cmd_stt_run(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        results = stt.run_for_source(
            conn, cfg, args.source_id, args.model, args.force, args.prompt, args.language
        )
        total = conn.execute(
            """select count(*) as n from utterances u join chunks c on c.id = u.chunk_id
               where c.source_id = %s""",
            (args.source_id,),
        ).fetchone()["n"]
    if not results:
        print(f"이미 전사돼 있다 (발화 {total}개) — 다시 하려면 --force")
        return 0
    seconds = sum(r.transcribe_ms for r in results) / 1000
    print(f"source {args.source_id} · {results[0].model} · 조각 {len(results)}개")
    print(f"  발화   {total}개")
    print(f"  전사   {seconds:.0f}초")
    return 0


def _cmd_stt_show(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        rows = conn.execute(
            "select * from utterances where chunk_id = %s order by idx limit %s",
            (args.chunk_id, args.limit),
        ).fetchall()
        total = conn.execute(
            "select count(*) as n from utterances where chunk_id = %s", (args.chunk_id,)
        ).fetchone()["n"]
    if not rows:
        print(f"chunk {args.chunk_id} 에 발화 없음 — `sm stt run {args.chunk_id}`")
        return 1
    for r in rows:
        flag = " ⚠" if (r["avg_logprob"] or 0) < -1.0 else ""
        print(f"  [{r['idx']:>3}] {r['start_sec']:>7.1f}~{r['end_sec']:>7.1f}  {r['text']}{flag}")
    print(f"\n  총 {total}개 (⚠ = avg_logprob < -1.0, 환각 의심)")
    return 0


def _cmd_segment_run(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        segments = segmentation.run_for_source(conn, cfg, args.source_id)
    print(f"source {args.source_id} · 구간 {len(segments)}개")
    for segment in segments:
        length = segment["end_sec"] - segment["start_sec"]
        print(f"  [{segment['idx']}] {segment['start_sec']:.0f}~{segment['end_sec']:.0f}s ({length:.0f}s)")
        print(f"      {segment['description']}")
    return 0


def _cmd_segment_list(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        rows = conn.execute(
            "select * from segments where chunk_id = %s order by idx", (args.chunk_id,)
        ).fetchall()
    if not rows:
        print(f"chunk {args.chunk_id} 에 구간 없음 — `sm segment run {args.chunk_id}`")
        return 1
    for r in rows:
        print(f"  [{r['idx']}] {r['start_sec']:.0f}~{r['end_sec']:.0f}s  {r['description']}")
    return 0


def _cmd_segment_add(cfg: config.Config, args) -> int:
    """LLM 없이 구간을 직접 만든다. 관리자가 직접 지점을 고르는 경로이기도 하다."""
    with store.connect(cfg.database_url) as conn:
        bounds = conn.execute(
            "select min(idx) as lo, max(idx) as hi from utterances where chunk_id = %s", (args.chunk_id,)
        ).fetchone()
        if bounds["lo"] is None:
            raise segmentation.SegmentationError(f"chunk {args.chunk_id} 에 발화가 없다")
        if not (bounds["lo"] <= args.from_utterance <= args.to_utterance <= bounds["hi"]):
            raise segmentation.SegmentationError(
                f"발화 범위가 잘못됐다: {args.from_utterance}~{args.to_utterance}"
                f" (허용 {bounds['lo']}~{bounds['hi']})"
            )
        span = conn.execute(
            "select min(start_sec) as s, max(end_sec) as e from utterances"
            " where chunk_id = %s and idx between %s and %s",
            (args.chunk_id, args.from_utterance, args.to_utterance),
        ).fetchone()
        idx = conn.execute(
            "select coalesce(max(idx) + 1, 0) as n from segments where chunk_id = %s", (args.chunk_id,)
        ).fetchone()["n"]
        segment_id = conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                     describe_model, start_utterance_idx, end_utterance_idx)
               values (%s, %s, %s, %s, %s, 'manual', %s, %s) returning id""",
            (args.chunk_id, idx, span["s"], span["e"], args.summary,
             args.from_utterance, args.to_utterance),
        ).fetchone()["id"]
        conn.commit()
    print(f"segment {segment_id} (idx {idx}) · {span['s']:.0f}~{span['e']:.0f}s"
          f" · 발화 {args.from_utterance}~{args.to_utterance}")
    return 0


def _cmd_run_create(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        run_id = conn.execute(
            "insert into runs (source_id, status) values (%s, 'DONE') returning id", (args.source_id,)
        ).fetchone()["id"]
        conn.commit()
    print(f"run {run_id} (수동 — rank 없이 만든 run)")
    return 0


def _cmd_rank_run(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        run_id = ranking.run_for_source(conn, cfg, args.source_id, args.criteria)
        row = conn.execute("select ranked from runs where id = %s", (run_id,)).fetchone()
    data = row["ranked"]  # jsonb — psycopg 가 dict 로 준다
    print(f"run {run_id}")
    print("  순위:")
    for r in data["ranked"]:
        print(f"    [{r['idx']}] {r['score']}점 — {r['reason']}")
    if data["excluded"]:
        print("  자립성 관문에서 제외:")
        for e in data["excluded"]:
            print(f"    [{e['idx']}] {e['reason']}")
    return 0


def _cmd_clip_add(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        manual = (args.from_utterance, args.to_utterance)
        clip_id = cutting.run_for_segment(conn, cfg, args.run_id, args.segment_id, None, manual)
        row = conn.execute("select * from clips where id = %s", (clip_id,)).fetchone()
    print(f"clip {clip_id} · {row['start_sec']:.1f}~{row['end_sec']:.1f}s"
          f" ({row['end_sec'] - row['start_sec']:.0f}초)")
    return 0


def _cmd_render(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        out = render.run_for_clip(conn, cfg, args.clip_id, args.force, not args.no_subtitles)
        call = conn.execute(
            "select latency_ms, params from stage_calls where stage = 'render' order by id desc limit 1"
        ).fetchone()
    size_mb = out.stat().st_size / 1024 / 1024
    params = call["params"] or {}  # jsonb — 이미 dict, 비-LLM 단계라도 render 는 params 를 남긴다
    print(f"clip {args.clip_id} 렌더 완료")
    print(f"  {out}")
    print(f"  {size_mb:.1f} MB · {call['latency_ms'] / 1000:.1f}s")
    print(f"  자막 {'번인 ' + str(params.get('cues')) + '장' if params.get('subtitles') else '없음'}")
    return 0


def _cmd_clip_list(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        rows = conn.execute(
            "select cl.*, sg.description from clips cl join segments sg on sg.id = cl.segment_id"
            " order by cl.id"
        ).fetchall()
    if not rows:
        print("클립 없음")
        return 1
    for r in rows:
        mark = "렌더됨" if r["rendered"] else "미렌더"
        print(f"  [{r['id']}] {r['start_sec']:.0f}~{r['end_sec']:.0f}s"
              f" ({r['end_sec'] - r['start_sec']:.0f}초) {mark}  {r['reason']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sm", description="긴 영상 → 숏폼 클립 파이프라인")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="실행 전제(ffmpeg·Gemini·Postgres)가 갖춰졌는지 확인한다")
    sub.add_parser("serve", help="웹 서버를 띄운다 (API + 화면, 기본 127.0.0.1:8100)")
    sub.add_parser("models", help="쓸 수 있는 Gemini 모델을 나열한다")

    db = sub.add_parser("db", help="Postgres 스키마 관리")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="마이그레이션을 적용한다 (idempotent, 서버 기동 시에도 자동으로 된다)")
    db_sub.add_parser("status", help="스키마 버전과 테이블별 행 수를 본다")
    reset = db_sub.add_parser("reset", help="public 스키마를 통째로 지우고 다시 만든다 (되돌릴 수 없다)")
    reset.add_argument("--yes", action="store_true", help="삭제에 동의한다")

    source = sub.add_parser("source", help="원본 영상 등록")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_add = source_sub.add_parser("add", help="로컬 파일을 원본으로 등록한다")
    source_add.add_argument("path", help=f"SHORTS_SOURCE_DIR 하위 경로")
    source_add.add_argument("--title", required=True)
    source_add.add_argument("--type", default="LECTURE", choices=("LECTURE", "FILM"))
    source_add.add_argument("--origin", help="출처 URL/설명 — 권리 감사용, 나중엔 못 되찾는다")
    source_add.add_argument("--context", help="줄거리/강연 개요. rank 의 자립성 판단에 쓰인다")
    source_add.add_argument("--language", help="ko/en/ja/zh. 비우면 STT 가 자동 감지")
    source_sub.add_parser("list", help="등록된 원본 목록")

    chunk = sub.add_parser("chunk", help="처리할 조각 추출")
    chunk_sub = chunk.add_subparsers(dest="chunk_command", required=True)
    chunk_add = chunk_sub.add_parser("add", help="영상 전체를 분석용 조각으로 나눈다")
    chunk_add.add_argument("source_id", type=int)
    chunk_add.add_argument("--start", type=float, help="분석 시작 초 (기본: 0)")
    chunk_add.add_argument("--end", type=float, help="분석 끝 초 (기본: 영상 끝)")
    chunk_add.add_argument(
        "--replace", action="store_true",
        help="기존 조각과 그 아래(발화·구간·클립·run)를 전부 지우고 다시 나눈다",
    )
    chunk_list = chunk_sub.add_parser("list", help="청크 목록")
    chunk_list.add_argument("source_id", type=int, nargs="?")

    stt_parser = sub.add_parser("stt", help="음성 인식 (문서 §4-[1])")
    stt_sub = stt_parser.add_subparsers(dest="stt_command", required=True)
    stt_run = stt_sub.add_parser("run", help="영상 전체를 전사한다 (조각을 순서대로)")
    stt_run.add_argument("source_id", type=int)
    stt_run.add_argument("--model", help="whisper 모델 (기본: SHORTS_WHISPER_MODEL)")
    stt_run.add_argument("--force", action="store_true", help="기존 발화를 지우고 다시 한다")
    stt_run.add_argument("--prompt", help="도메인 어휘를 물려준다 (고유명사 교정용, 짧게)")
    stt_run.add_argument("--language", help="ko/en/ja/zh. 비우면 원본 설정, 원본도 없으면 자동 감지")
    stt_show = stt_sub.add_parser("show", help="전사 결과를 본다")
    stt_show.add_argument("chunk_id", type=int)
    stt_show.add_argument("--limit", type=int, default=20)

    seg = sub.add_parser("segment", help="주제 단위 구간 분할 (문서 §4-[3])")
    seg_sub = seg.add_subparsers(dest="segment_command", required=True)
    seg_run = seg_sub.add_parser("run", help="영상 전체를 주제 단위로 나눈다 (Gemini)")
    seg_run.add_argument("source_id", type=int)
    seg_run.add_argument("--force", action="store_true", help="기존 구간을 지우고 다시 한다")
    seg_list = seg_sub.add_parser("list", help="구간 목록")
    seg_list.add_argument("chunk_id", type=int)

    seg_add = seg_sub.add_parser("add", help="구간을 직접 만든다 (LLM 없이)")
    seg_add.add_argument("chunk_id", type=int)
    seg_add.add_argument("--from-utterance", type=int, required=True, dest="from_utterance")
    seg_add.add_argument("--to-utterance", type=int, required=True, dest="to_utterance")
    seg_add.add_argument("--summary", required=True)

    rank = sub.add_parser("rank", help="명장면 선정 (문서 §4-[5])")
    rank_sub = rank.add_subparsers(dest="rank_command", required=True)
    rank_run = rank_sub.add_parser("run", help="구간에 점수를 매긴다 (Gemini)")
    rank_run.add_argument("source_id", type=int)
    rank_run.add_argument("--criteria", help="기준 프롬프트 (§6-1 의 [가변] 부분)")

    run_p = sub.add_parser("run", help="Run 관리")
    run_sub = run_p.add_subparsers(dest="run_command", required=True)
    run_create = run_sub.add_parser("create", help="rank 없이 빈 run 을 만든다 (수동 경로)")
    run_create.add_argument("source_id", type=int)

    clip = sub.add_parser("clip", help="클립")
    clip_sub = clip.add_subparsers(dest="clip_command", required=True)
    clip_add = clip_sub.add_parser("add", help="발화 범위로 클립을 직접 만든다")
    clip_add.add_argument("--run", type=int, required=True, dest="run_id")
    clip_add.add_argument("--segment", type=int, required=True, dest="segment_id")
    clip_add.add_argument("--from-utterance", type=int, required=True, dest="from_utterance")
    clip_add.add_argument("--to-utterance", type=int, required=True, dest="to_utterance")
    clip_sub.add_parser("list", help="클립 목록")

    render_p = sub.add_parser("render", help="9:16 리프레이밍 + 렌더 (문서 §4-[7])")
    render_p.add_argument("clip_id", type=int)
    render_p.add_argument("--force", action="store_true")
    render_p.add_argument("--no-subtitles", action="store_true", dest="no_subtitles",
                          help="자막 번인을 끈다 (기본: 켬 — 문서 §9-9)")

    answers_p = sub.add_parser("answers", help="시청자 질문 묶기 (update_plan M3)")
    answers_sub = answers_p.add_subparsers(dest="answers_command", required=True)
    agg = answers_sub.add_parser("aggregate", help="[집계] — 미분류 질문을 묶는다 (LLM 1회 + 임베딩)")
    agg.add_argument("source_id", type=int)
    agg_list = answers_sub.add_parser("list", help="클러스터와 수요를 본다")
    agg_list.add_argument("source_id", type=int)
    index_p = answers_sub.add_parser("index", help="구간 설명을 검색용으로 임베딩한다 (M5 준비)")
    index_p.add_argument("source_id", type=int)
    ev = answers_sub.add_parser(
        "eval-cluster",
        help="eval/questions.json 을 넣고 집계를 돌려 θ 를 튜닝한다 (🔴 실제 API 호출이 나간다)",
    )
    ev.add_argument("source_id", type=int)
    ev.add_argument("--theta", type=float, help="이번 실행에만 쓸 임계값 (기본: SHORTS_CLUSTER_THETA)")
    ev.add_argument("--keep", action="store_true",
                    help="끝나고 넣은 질문·클러스터를 지우지 않는다 (화면으로 확인할 때)")

    args = parser.parse_args(argv)
    cfg = config.load()

    try:
        return _dispatch(parser, cfg, args)
    except (
        ingest.IngestError,
        ffmpeg.FfmpegError,
        gemini.GeminiError,
        stt.SttError,
        segmentation.SegmentationError,
        ranking.RankingError,
        cutting.CuttingError,
        render.RenderError,
        store.SchemaError,
        store.SourceBusy,
        clusters.ClusterError,
        embeddings.EmbeddingError,
    ) as exc:
        # 예상된 실패는 트레이스백 없이 한 줄로 알린다 — 사용자가 고칠 수 있는 종류다.
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        # 거의 모든 명령이 DB 를 만진다. db 컨테이너가 안 떠 있을 때 트레이스백 대신 할 일을 알린다.
        # 🔴 exc 문자열에 URL 은 안 들어가지만 그래도 database_url 은 찍지 않는다.
        print(f"오류: {DB_DOWN_HINT} ({exc})", file=sys.stderr)
        return 1


EVAL_PATH = Path(__file__).resolve().parents[3] / "eval" / "questions.json"


def _cmd_answers_aggregate(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        result = clusters.aggregate(conn, cfg, args.source_id)
    print(f"미분류 {result['pending']}개 → 배정 {result['assigned']}개 · 새 클러스터 {result['newClusters']}개 (θ={result['theta']})")
    # 목록은 보여주기만 한다. 🔴 그 반환값을 그대로 쓰면 "묶을 게 없어서 0개" 일 때 집계가 성공했는데도
    # 종료 코드가 1이 된다 — 스크립트에서 실패로 읽힌다.
    _cmd_answers_list(cfg, args)
    return 0


def _cmd_answers_list(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        found = clusters.demand(conn, args.source_id)
        loose = clusters.unclustered(conn, args.source_id)
        if not found and not loose:
            print(f"source {args.source_id} 에 질문이 없다 — 시청자 화면(/watch)에서 남기거나 `sm answers eval-cluster`")
            return 1
        for cluster in found:
            print(f"\n[{cluster['id']}] {cluster['canonical_text']}")
            print(f"     질문 {cluster['question_count']} · 좋아요 {cluster['like_count']} · {cluster['status']}")
            for question in clusters.questions_of(conn, cluster["id"]):
                print(f"       ♥{question['likes']} {question['text']}")
        if loose:
            print(f"\n아직 안 묶인 질문 {len(loose)}개 — `sm answers aggregate {args.source_id}`")
            for question in loose:
                print(f"       {question['text']}")
    return 0


def _cmd_answers_index(cfg: config.Config, args) -> int:
    with store.connect(cfg.database_url) as conn:
        added = embeddings.index_segments(conn, cfg, args.source_id)
    print(f"구간 임베딩 {added}개 새로 만듦 (이미 있던 것은 건너뜀)")
    return 0


def _cmd_answers_eval(cfg: config.Config, args) -> int:
    """θ 튜닝. eval/questions.json 을 넣고 집계한 뒤 "붙어야 할 것이 붙었나"를 표로 본다.

    🔴 실제 API 호출이 나간다(LLM 1회 + 임베딩). 그리고 기본값은 **끝나고 지운다** — 평가용
    질문이 진짜 시청자 질문과 섞이면 수요 순위가 거짓이 된다.
    """
    if not EVAL_PATH.is_file():
        print(f"오류: 평가 세트가 없다: {EVAL_PATH}", file=sys.stderr)
        return 1
    payload = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    rows = payload["questions"]
    if args.theta is not None:
        cfg = dataclasses.replace(cfg, cluster_theta=args.theta)

    with store.connect(cfg.database_url) as conn:
        if conn.execute("select 1 from sources where id = %s", (args.source_id,)).fetchone() is None:
            print(f"source {args.source_id} 없음", file=sys.stderr)
            return 1
        # 🔴 이 소스에 이미 진짜 질문이 있으면 집계가 그것도 함께 묶는다. 평가가 끝나면 원래대로
        # 되돌려야 한다 — 안 그러면 평가용 문장에 맞춰 지어진 대표 문장에 실제 질문이 남는다.
        before = {
            r["id"]: r["cluster_id"]
            for r in conn.execute(
                "select id, cluster_id from questions where source_id = %s", (args.source_id,)
            )
        }
        cluster_watermark = conn.execute(
            "select coalesce(max(id), 0) as m from question_clusters"
        ).fetchone()["m"]
        if before:
            print(f"⚠ 이 소스에 실제 질문 {len(before)}개가 있다 — 평가 뒤 원래 소속으로 되돌린다"
                  f"{' (--keep 이라 되돌리지 않는다)' if args.keep else ''}")
        inserted = []
        for index, row in enumerate(rows):
            got = conn.execute(
                "insert into questions (source_id, text, viewer_id) values (%s, %s, %s) returning id",
                (args.source_id, row["text"], f"eval-{index:03d}"),
            ).fetchone()["id"]
            inserted.append((got, row["group"]))
        conn.commit()

        result = clusters.aggregate(conn, cfg, args.source_id)
        placed = {
            r["id"]: r["cluster_id"]
            for r in conn.execute("select id, cluster_id from questions where id = any(%s)",
                                  ([qid for qid, _ in inserted],))
        }
        names = {
            r["id"]: r["canonical_text"]
            for r in conn.execute("select id, canonical_text from question_clusters where source_id = %s",
                                  (args.source_id,))
        }

        groups: dict[str, list[int | None]] = {}
        for (qid, group) in inserted:
            groups.setdefault(group, []).append(placed.get(qid))

        print(f"\nθ={cfg.cluster_theta} · 질문 {len(rows)} → 클러스터 {result['newClusters']}개\n")
        print(f"{'기대 그룹':<12} {'뭉침':<6} 배정된 클러스터")
        split = merged = 0
        cluster_to_groups: dict[int, set[str]] = {}
        for group, cluster_ids in sorted(groups.items()):
            unique = {c for c in cluster_ids if c is not None}
            for cid in unique:
                cluster_to_groups.setdefault(cid, set()).add(group)
            mark = "OK" if len(unique) == 1 else f"쪼개짐{len(unique)}"
            if len(unique) != 1:
                split += 1
            labels = " / ".join(f"[{cid}] {names.get(cid, '?')[:24]}" for cid in sorted(unique))
            print(f"{group:<12} {mark:<6} {labels}")
        for cid, owners in sorted(cluster_to_groups.items()):
            if len(owners) > 1:
                merged += 1
                print(f"\n🔴 [{cid}] {names.get(cid, '?')} ← 서로 다른 그룹이 섞였다: {', '.join(sorted(owners))}")
        print(f"\n쪼개진 그룹 {split} · 섞인 클러스터 {merged}  (둘 다 0 이면 이 θ 가 맞다)")

        if not args.keep:
            # 순서가 중요하다: 질문 삭제 → 클러스터 삭제 → 소속 복원.
            # 클러스터를 지우면 남은 질문의 cluster_id 가 null 이 되므로(on delete set null)
            # 복원을 먼저 하면 그게 도로 지워진다.
            conn.execute("delete from questions where id = any(%s)", ([qid for qid, _ in inserted],))
            conn.execute(
                """delete from question_clusters where id > %s and source_id = %s and status = 'OPEN'""",
                (cluster_watermark, args.source_id),
            )
            for question_id, cluster_id in before.items():
                conn.execute(
                    "update questions set cluster_id = %s where id = %s", (cluster_id, question_id)
                )
            conn.commit()
            after = conn.execute(
                "select count(*) as n from questions where source_id = %s", (args.source_id,)
            ).fetchone()["n"]
            print(f"평가용 질문과 이번에 만든 클러스터를 지웠다 (남은 질문 {after}개 = 원래 {len(before)}개). 남기려면 --keep")
    return 0


def _dispatch(parser: argparse.ArgumentParser, cfg: config.Config, args) -> int:
    if args.command == "doctor":
        return doctor.run(cfg)
    if args.command == "models":
        rows = gemini.model_catalog(cfg)
        print(f"generateContent 가능한 모델 {len(rows)}개 · 현재 설정: {cfg.gemini_model}\n")
        for row in rows:
            mark = " ←현재" if row["id"] == cfg.gemini_model else ""
            limits = f"in {row['input_limit'] or '-'} / out {row['output_limit'] or '-'}"
            print(f"  {row['id']:<42} {limits:<24} {row['label']}{mark}")
        if cfg.gemini_model not in {r["id"] for r in rows}:
            print(f"\n⚠ 설정된 {cfg.gemini_model} 은 목록에 없다 — SHORTS_GEMINI_MODEL 을 위에서 고른다")
        return 0
    if args.command == "serve":
        from ..http import server

        server.serve()
        return 0
    if args.command == "db":
        if args.db_command == "init":
            return _cmd_db_init(cfg)
        if args.db_command == "status":
            return _cmd_db_status(cfg)
        if args.db_command == "reset":
            return _cmd_db_reset(cfg, args.yes)
    if args.command == "source":
        if args.source_command == "add":
            return _cmd_source_add(cfg, args)
        if args.source_command == "list":
            return _cmd_source_list(cfg)
    if args.command == "segment":
        if args.segment_command == "run":
            return _cmd_segment_run(cfg, args)
        if args.segment_command == "list":
            return _cmd_segment_list(cfg, args)
        if args.segment_command == "add":
            return _cmd_segment_add(cfg, args)
    if args.command == "rank" and args.rank_command == "run":
        return _cmd_rank_run(cfg, args)
    if args.command == "run" and args.run_command == "create":
        return _cmd_run_create(cfg, args)
    if args.command == "clip":
        if args.clip_command == "add":
            return _cmd_clip_add(cfg, args)
        if args.clip_command == "list":
            return _cmd_clip_list(cfg, args)
    if args.command == "answers":
        if args.answers_command == "aggregate":
            return _cmd_answers_aggregate(cfg, args)
        if args.answers_command == "list":
            return _cmd_answers_list(cfg, args)
        if args.answers_command == "index":
            return _cmd_answers_index(cfg, args)
        if args.answers_command == "eval-cluster":
            return _cmd_answers_eval(cfg, args)
    if args.command == "render":
        return _cmd_render(cfg, args)
    if args.command == "stt":
        if args.stt_command == "run":
            return _cmd_stt_run(cfg, args)
        if args.stt_command == "show":
            return _cmd_stt_show(cfg, args)
    if args.command == "chunk":
        if args.chunk_command == "add":
            return _cmd_chunk_add(cfg, args)
        if args.chunk_command == "list":
            return _cmd_chunk_list(cfg, args.source_id)

    parser.error(f"알 수 없는 명령: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
