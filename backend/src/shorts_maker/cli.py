import argparse
import json
import sys

from . import config, cutting, doctor, ffmpeg, gemini, ingest, ranking, render, segmentation, stt
from .db import store


def _cmd_db_init(cfg: config.Config) -> int:
    with store.connect(cfg.db_path) as conn:
        version = store.apply_schema(conn)
        tables = store.existing_tables(conn)
    print(f"{cfg.db_path}")
    print(f"schema v{version} · 테이블 {len(tables)}개: {', '.join(tables)}")
    return 0


def _cmd_db_status(cfg: config.Config) -> int:
    if not cfg.db_path.exists():
        print(f"{cfg.db_path} 없음 — `sm db init` 으로 만든다")
        return 1
    with store.connect(cfg.db_path) as conn:
        version = store.schema_version(conn)
        counts = store.row_counts(conn)
    print(f"{cfg.db_path} · schema v{version}")
    if not counts:
        print("테이블 없음 — `sm db init` 으로 만든다")
        return 1
    for table, n in counts.items():
        print(f"  {table:<13} {n}")
    return 0


def _cmd_db_reset(cfg: config.Config, confirmed: bool) -> int:
    if not confirmed:
        # 되돌릴 수 없는 삭제라 기본값으로 두지 않는다. A 단계에선 자주 쓰지만 자주 쓴다고
        # 안전해지는 건 아니다.
        print(f"{cfg.db_path} 를 삭제하고 다시 만든다. 실행하려면 --yes 를 붙인다")
        return 1
    store.reset(cfg.db_path)
    print(f"{cfg.db_path} 삭제 완료")
    return _cmd_db_init(cfg)


def _cmd_source_add(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        source_id = ingest.add_source(
            conn, cfg, args.path, args.title, args.type, args.origin, args.context,
            stt.check_language(args.language),
        )
        row = conn.execute("select * from sources where id = ?", (source_id,)).fetchone()
    minutes, seconds = divmod(int(row["duration_sec"]), 60)
    print(f"source {source_id} 등록: {row['title']}")
    print(f"  type   {row['content_type']}")
    print(f"  길이   {minutes}분 {seconds}초")
    print(f"  path   {row['path']}")
    print(f"  origin {row['origin'] or '-'}")
    return 0


def _cmd_source_list(cfg: config.Config) -> int:
    with store.connect(cfg.db_path) as conn:
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
    with store.connect(cfg.db_path) as conn:
        chunk_id = ingest.add_chunk(conn, cfg, args.source_id, args.start, args.end)
        row = conn.execute("select * from chunks where id = ?", (chunk_id,)).fetchone()
        latency = conn.execute(
            "select latency_ms from stage_calls where stage = 'chunk' order by id desc limit 1"
        ).fetchone()["latency_ms"]
    print(f"chunk {chunk_id} (idx {row['idx']}) 생성: {row['start_sec']:.0f}s ~ {row['end_sec']:.0f}s")
    print(f"  path {row['path']}")
    print(f"  추출 {latency / 1000:.1f}s")
    return 0


def _cmd_chunk_list(cfg: config.Config, source_id: int | None) -> int:
    query = "select * from chunks"
    params: tuple = ()
    if source_id is not None:
        query += " where source_id = ?"
        params = (source_id,)
    with store.connect(cfg.db_path) as conn:
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
    with store.connect(cfg.db_path) as conn:
        result = stt.run_for_chunk(
            conn, cfg, args.chunk_id, args.model, args.force, args.prompt, args.language
        )
        audio_sec = conn.execute(
            "select end_sec - start_sec as d from chunks where id = ?", (args.chunk_id,)
        ).fetchone()["d"]
    speed = audio_sec / (result.transcribe_ms / 1000) if result.transcribe_ms else 0
    total = sum(r["end_sec"] - r["start_sec"] for r in result.rows)
    print(f"chunk {args.chunk_id} · {result.model}")
    print(f"  발화     {len(result.rows)}개 (버림 {result.skipped})")
    print(f"  언어     {result.language} ({result.language_probability:.2%})")
    print(f"  모델로딩 {result.load_ms / 1000:.1f}s")
    print(f"  추론     {result.transcribe_ms / 1000:.1f}s  ({speed:.1f}x 실시간, 오디오 {audio_sec:.0f}s)")
    print(f"  발화시간 {total:.0f}s / {audio_sec:.0f}s ({total / audio_sec:.0%})")
    return 0


def _cmd_stt_show(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        rows = conn.execute(
            "select * from utterances where chunk_id = ? order by idx limit ?",
            (args.chunk_id, args.limit),
        ).fetchall()
        total = conn.execute(
            "select count(*) as n from utterances where chunk_id = ?", (args.chunk_id,)
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
    with store.connect(cfg.db_path) as conn:
        specs = segmentation.run_for_chunk(conn, cfg, args.chunk_id, args.force)
        call = conn.execute(
            "select input_tokens, output_tokens, thinking_tokens, latency_ms from stage_calls"
            " where stage = 'segment' order by id desc limit 1"
        ).fetchone()
        rows = conn.execute(
            "select * from segments where chunk_id = ? order by idx", (args.chunk_id,)
        ).fetchall()
    print(f"chunk {args.chunk_id} · 구간 {len(specs)}개")
    for r in rows:
        span = r["end_sec"] - r["start_sec"]
        mark = " ⚠짧음" if span < 30 else ""
        print(f"  [{r['idx']}] {r['start_sec']:.0f}~{r['end_sec']:.0f}s ({span:.0f}s, "
              f"발화 {r['start_utterance_idx']}~{r['end_utterance_idx']}){mark}")
        print(f"      {r['description']}")
    print(f"\n  토큰 in={call['input_tokens']} out={call['output_tokens']} "
          f"thinking={call['thinking_tokens']} · {call['latency_ms'] / 1000:.1f}s")
    return 0


def _cmd_segment_list(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        rows = conn.execute(
            "select * from segments where chunk_id = ? order by idx", (args.chunk_id,)
        ).fetchall()
    if not rows:
        print(f"chunk {args.chunk_id} 에 구간 없음 — `sm segment run {args.chunk_id}`")
        return 1
    for r in rows:
        print(f"  [{r['idx']}] {r['start_sec']:.0f}~{r['end_sec']:.0f}s  {r['description']}")
    return 0


def _cmd_segment_add(cfg: config.Config, args) -> int:
    """LLM 없이 구간을 직접 만든다. 관리자가 직접 지점을 고르는 경로이기도 하다."""
    with store.connect(cfg.db_path) as conn:
        bounds = conn.execute(
            "select min(idx) as lo, max(idx) as hi from utterances where chunk_id = ?", (args.chunk_id,)
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
            " where chunk_id = ? and idx between ? and ?",
            (args.chunk_id, args.from_utterance, args.to_utterance),
        ).fetchone()
        idx = conn.execute(
            "select coalesce(max(idx) + 1, 0) as n from segments where chunk_id = ?", (args.chunk_id,)
        ).fetchone()["n"]
        cursor = conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                     describe_model, start_utterance_idx, end_utterance_idx)
               values (?, ?, ?, ?, ?, 'manual', ?, ?)""",
            (args.chunk_id, idx, span["s"], span["e"], args.summary,
             args.from_utterance, args.to_utterance),
        )
        conn.commit()
    print(f"segment {cursor.lastrowid} (idx {idx}) · {span['s']:.0f}~{span['e']:.0f}s"
          f" · 발화 {args.from_utterance}~{args.to_utterance}")
    return 0


def _cmd_run_create(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        cursor = conn.execute(
            "insert into runs (source_id, status) values (?, 'DONE')", (args.source_id,)
        )
        conn.commit()
    print(f"run {cursor.lastrowid} (수동 — rank 없이 만든 run)")
    return 0


def _cmd_rank_run(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        run_id = ranking.run_for_source(conn, cfg, args.source_id, args.criteria, None)
        row = conn.execute("select ranked from runs where id = ?", (run_id,)).fetchone()
    data = json.loads(row["ranked"])
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
    with store.connect(cfg.db_path) as conn:
        manual = (args.from_utterance, args.to_utterance)
        clip_id = cutting.run_for_segment(conn, cfg, args.run_id, args.segment_id, None, manual)
        row = conn.execute("select * from clips where id = ?", (clip_id,)).fetchone()
    print(f"clip {clip_id} · {row['start_sec']:.1f}~{row['end_sec']:.1f}s"
          f" ({row['end_sec'] - row['start_sec']:.0f}초)")
    return 0


def _cmd_render(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
        out = render.run_for_clip(conn, cfg, args.clip_id, args.force, not args.no_subtitles)
        call = conn.execute(
            "select latency_ms, params from stage_calls where stage = 'render' order by id desc limit 1"
        ).fetchone()
    size_mb = out.stat().st_size / 1024 / 1024
    params = json.loads(call["params"] or "{}")
    print(f"clip {args.clip_id} 렌더 완료")
    print(f"  {out}")
    print(f"  {size_mb:.1f} MB · {call['latency_ms'] / 1000:.1f}s")
    print(f"  자막 {'번인 ' + str(params.get('cues')) + '장' if params.get('subtitles') else '없음'}")
    return 0


def _cmd_clip_list(cfg: config.Config, args) -> int:
    with store.connect(cfg.db_path) as conn:
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

    sub.add_parser("doctor", help="실행 전제(ffmpeg·Gemini·DB)가 갖춰졌는지 확인한다")
    sub.add_parser("serve", help="웹 서버를 띄운다 (API + 화면, 기본 127.0.0.1:8100)")
    sub.add_parser("models", help="쓸 수 있는 Gemini 모델을 나열한다")

    db = sub.add_parser("db", help="스키마 관리")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="스키마를 적용한다 (idempotent)")
    db_sub.add_parser("status", help="스키마 버전과 테이블별 행 수를 본다")
    reset = db_sub.add_parser("reset", help="DB 파일을 지우고 다시 만든다")
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
    chunk_add = chunk_sub.add_parser("add", help="구간을 잘라 청크로 만든다")
    chunk_add.add_argument("source_id", type=int)
    chunk_add.add_argument("--start", type=float, required=True, help="소스 절대 초")
    chunk_add.add_argument("--end", type=float, required=True, help="소스 절대 초")
    chunk_list = chunk_sub.add_parser("list", help="청크 목록")
    chunk_list.add_argument("source_id", type=int, nargs="?")

    stt_parser = sub.add_parser("stt", help="음성 인식 (문서 §4-[1])")
    stt_sub = stt_parser.add_subparsers(dest="stt_command", required=True)
    stt_run = stt_sub.add_parser("run", help="청크를 전사한다")
    stt_run.add_argument("chunk_id", type=int)
    stt_run.add_argument("--model", help="whisper 모델 (기본: SHORTS_WHISPER_MODEL)")
    stt_run.add_argument("--force", action="store_true", help="기존 발화를 지우고 다시 한다")
    stt_run.add_argument("--prompt", help="도메인 어휘를 물려준다 (고유명사 교정용, 짧게)")
    stt_run.add_argument("--language", help="ko/en/ja/zh. 비우면 원본 설정, 원본도 없으면 자동 감지")
    stt_show = stt_sub.add_parser("show", help="전사 결과를 본다")
    stt_show.add_argument("chunk_id", type=int)
    stt_show.add_argument("--limit", type=int, default=20)

    seg = sub.add_parser("segment", help="주제 단위 구간 분할 (문서 §4-[3])")
    seg_sub = seg.add_subparsers(dest="segment_command", required=True)
    seg_run = seg_sub.add_parser("run", help="트랜스크립트를 주제 단위로 나눈다 (Gemini)")
    seg_run.add_argument("chunk_id", type=int)
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
    ) as exc:
        # 예상된 실패는 트레이스백 없이 한 줄로 알린다 — 사용자가 고칠 수 있는 종류다.
        print(f"오류: {exc}", file=sys.stderr)
        return 1


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
        from . import api

        api.serve()
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
