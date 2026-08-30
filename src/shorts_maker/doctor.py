"""실행 전제를 한 번에 확인한다.

A0 에 이걸 먼저 두는 이유: 파이프라인을 몇 시간 짜고 나서 API 키나 ffmpeg 때문에
막히면 그 시간이 통째로 낭비된다. 막힐 곳을 맨 앞으로 당긴다.
"""

from pathlib import Path

from . import config, ffmpeg, gemini
from .db import store

OK = "  ok"
FAIL = "FAIL"


def _line(status: str, label: str, detail: str) -> None:
    print(f"[{status}] {label:<16} {detail}")


def _check_ffmpeg(cfg: config.Config) -> bool:
    ok = True
    for binary in (cfg.ffmpeg_bin, "ffprobe"):
        try:
            _line(OK, Path(binary).name, ffmpeg.version(binary))
        except ffmpeg.FfmpegError as exc:
            _line(FAIL, Path(binary).name, str(exc))
            ok = False
            return ok

    # 🔴 자막 번인에는 libass 가 필요하다(§9-9). Homebrew 기본 ffmpeg 에는 없어서, 이걸
    # 확인하지 않으면 렌더 단계에 가서야 실패한다.
    if ffmpeg.has_filter("ass", cfg.ffmpeg_bin):
        available = ffmpeg.font_available(cfg.subtitle_font)
        if available is True:
            _line(OK, "자막(libass)", f"ass 필터 · 폰트 '{cfg.subtitle_font}'")
        elif available is False:
            _line(FAIL, "자막 폰트",
                  f"'{cfg.subtitle_font}' 없음 — 자막이 네모(□)로 찍힌다. SHORTS_SUBTITLE_FONT 확인")
            ok = False
        else:
            _line(OK, "자막(libass)",
                  f"ass 필터 · 폰트 '{cfg.subtitle_font}' (fc-match 가 없어 확인 불가)")
    else:
        _line(FAIL, "자막(libass)",
              f"{cfg.ffmpeg_bin} 에 ass 필터가 없다 — SHORTS_FFMPEG 를 libass 포함 빌드로 지정한다 "
              "(macOS: brew install ffmpeg-full)")
        ok = False
    return ok


def _check_db(cfg: config.Config) -> bool:
    # doctor 는 확인만 한다 — 스키마를 만드는 건 `sm db init` 의 일이다.
    try:
        with store.connect(cfg.db_path) as conn:
            version = store.schema_version(conn)
            missing = [t for t in store.TABLES if t not in set(store.existing_tables(conn))]
    except Exception as exc:
        _line(FAIL, "sqlite", f"{cfg.db_path}: {exc}")
        return False

    if missing:
        _line(FAIL, "sqlite", f"schema v{version} · 테이블 {len(missing)}개 없음 — `sm db init` 실행")
        return False
    _line(OK, "sqlite", f"{cfg.db_path} (schema v{version}, 테이블 {len(store.TABLES)}개)")
    return True


def _check_gemini(cfg: config.Config) -> bool:
    if not cfg.gemini_api_key:
        _line(FAIL, "gemini key", "GEMINI_API_KEY 가 비어 있다 (.env.example 참고)")
        return False
    _line(OK, "gemini key", f"...{cfg.gemini_api_key[-4:]}")

    try:
        model_ids = gemini.generative_model_ids(cfg)
    except Exception as exc:
        _line(FAIL, "gemini models", str(exc))
        return False

    # 문서 §7 의 모델명/가격은 2026-08 기준 추정치다. 실제로 부를 수 있는 ID 를 여기서 확정한다.
    flash = [m for m in model_ids if "flash" in m]
    _line(OK, "gemini models", f"{len(model_ids)}개 사용 가능 · flash 계열: {', '.join(flash[:6]) or '없음'}")

    if cfg.gemini_model not in model_ids:
        _line(FAIL, "gemini model", f"{cfg.gemini_model} 은 목록에 없다 — SHORTS_GEMINI_MODEL 을 위 목록에서 고른다")
        return False

    try:
        result = gemini.ping(cfg)
    except Exception as exc:
        _line(FAIL, "gemini call", f"{cfg.gemini_model}: {exc}")
        return False
    _line(
        OK,
        "gemini call",
        f"{cfg.gemini_model} · in={result['input_tokens']} out={result['output_tokens']} "
        f"thinking={result['thinking_tokens']}",
    )
    return True


def run(cfg: config.Config) -> int:
    print(f"repo   {config.REPO_ROOT}")
    print(f"work   {cfg.work_dir}")
    print(f"source {cfg.source_dir}\n")

    results = [_check_ffmpeg(cfg), _check_db(cfg), _check_gemini(cfg)]
    print()
    if all(results):
        print("전제 확인 완료 — A1(소재 확보)로 진행 가능")
        return 0
    print("전제가 갖춰지지 않았다 — 위 FAIL 항목을 해결하고 다시 실행한다")
    return 1
