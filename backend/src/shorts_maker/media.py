"""서버에 저장된 원본 영상 관리 — 목록·용량·삭제.

🔴 삭제는 **실제로 지운다.** 원본 한 편이 1.3GB 라 DB 행만 지우면 디스크는 그대로 차 있고,
정작 화면에서는 사라져서 아무도 눈치채지 못한다. 그래서 파생물까지 함께 지운다.
"""

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class MediaError(RuntimeError):
    pass


@dataclass
class Removal:
    files: list[str]
    freed_bytes: int
    source_id: int | None


def resolve(cfg: config.Config, name: str) -> Path:
    """파일명을 SOURCE_DIR 안으로 가둔다.

    🔴 삭제 API 라 경로 탈출이 곧 임의 파일 삭제다. 이름에 구분자가 있으면 아예 거절한다.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise MediaError(f"파일명이 올바르지 않다: {name!r}")
    root = cfg.source_dir.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise MediaError(f"입력은 {root} 바로 아래여야 한다")
    return path


def disk_usage(cfg: config.Config) -> dict:
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(cfg.work_dir)
    return {
        "totalBytes": usage.total,
        "usedBytes": usage.used,
        "freeBytes": usage.free,
        "sourcesBytes": sum(f.stat().st_size for f in _video_files(cfg)),
        "workBytes": sum(f.stat().st_size for f in cfg.work_dir.rglob("*") if f.is_file()),
    }


def _video_files(cfg: config.Config) -> list[Path]:
    if not cfg.source_dir.is_dir():
        return []
    return sorted(
        f for f in cfg.source_dir.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_SUFFIXES
    )


def listing(conn: sqlite3.Connection, cfg: config.Config) -> list[dict]:
    """저장된 영상과, 그것이 등록된 원본인지를 함께 돌려준다.

    등록되지 않은 파일도 보여준다 — scp 로 올려두고 등록을 안 한 상태가 실제로 생기고,
    그 파일도 디스크를 먹는다.
    """
    registered = {
        cfg.source_file(row["path"]).name: dict(row)
        for row in conn.execute("select id, title, path, duration_sec, status from sources")
    }

    items = []
    for path in _video_files(cfg):
        source = registered.get(path.name)
        items.append(
            {
                "name": path.name,
                "sizeBytes": path.stat().st_size,
                "sourceId": source["id"] if source else None,
                "title": source["title"] if source else None,
                "durationSec": source["duration_sec"] if source else None,
            }
        )
    return items


def derived_paths(conn: sqlite3.Connection, cfg: config.Config, source_id: int) -> list[Path]:
    """그 원본에서 파생된 파일 경로를 모은다. **DB 행을 지우기 전에** 불러야 한다.

    원본 삭제(delete) 와 청크 교체(ingest.add_chunk replace) 가 같이 쓴다 — 청크를 갈아끼우면
    발화·구간·클립이 전부 무효라 파일도 함께 치운다.
    """
    paths: list[Path] = []
    for row in conn.execute("select path from chunks where source_id = ?", (source_id,)):
        if row["path"]:
            paths.append(cfg.work_file(row["path"]))
    for row in conn.execute(
        """select cl.path from clips cl
           join segments sg on sg.id = cl.segment_id
           join chunks ch on ch.id = sg.chunk_id
           where ch.source_id = ?""",
        (source_id,),
    ):
        if row["path"]:
            clip_path = cfg.work_file(row["path"])
            paths.append(clip_path)
            # 자막 파일은 DB 에 없다 — 클립 경로에서 유도한다.
            paths.append(clip_path.with_suffix(".ass"))
    for row in conn.execute(
        """select sg.id from segments sg join chunks ch on ch.id = sg.chunk_id
           where ch.source_id = ?""",
        (source_id,),
    ):
        paths.append(cfg.work_dir / "previews" / f"segment{row['id']:04d}.mp4")
    return paths


def find_source_id(conn: sqlite3.Connection, cfg: config.Config, path: Path) -> int | None:
    """정규화한 경로로 대조한다.

    🔴 문자열 비교로 하면 심볼릭 링크나 상대경로 차이만으로 못 찾고, 그 경우 **DB 행은 남고
    파일만 지워지는** 최악의 상태가 된다(테스트가 잡았다). 절대경로를 저장하던 시절엔 레포를
    옮기는 것만으로 이 상태가 됐다 — 지금은 상대경로라 source_dir 기준으로 되찾는다.
    """
    for row in conn.execute("select id, path from sources"):
        try:
            if cfg.source_file(row["path"]).resolve() == path:
                return row["id"]
        except OSError:
            continue
    return None


def remove_files(cfg: config.Config, targets: list[Path]) -> tuple[list[str], int]:
    """목록의 파일을 지우고 (지운 경로들, 확보한 바이트) 를 돌려준다.

    🔴 source_dir·work_dir 밖은 건드리지 않는다. DB 에 이상한 경로가 들어 있어도 여기서 막힌다.
    없는 파일은 조용히 건너뛴다 — 렌더 전 클립처럼 파일이 아직 없는 행이 정상적으로 있다.
    """
    removed: list[str] = []
    freed = 0
    work_root = cfg.work_dir.resolve()
    source_root = cfg.source_dir.resolve()
    for target in targets:
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if not (resolved.is_relative_to(work_root) or resolved.is_relative_to(source_root)):
            continue
        if not resolved.is_file():
            continue
        freed += resolved.stat().st_size
        resolved.unlink()
        removed.append(str(resolved))
    return removed, freed


def delete(conn: sqlite3.Connection, cfg: config.Config, name: str) -> Removal:
    path = resolve(cfg, name)
    source_id = find_source_id(conn, cfg, path)

    targets = [path]
    if source_id is not None:
        targets += derived_paths(conn, cfg, source_id)
        # DB 는 cascade 로 chunks·utterances·segments·runs·clips 까지 지운다.
        conn.execute("delete from sources where id = ?", (source_id,))
        conn.commit()

    removed, freed = remove_files(cfg, targets)
    return Removal(files=removed, freed_bytes=freed, source_id=source_id)
