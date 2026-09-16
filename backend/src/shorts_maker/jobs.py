"""잡 큐 (문서 §10 C2).

🔴 **동시 1건**을 정책이 아니라 구조로 강제한다 — 워커 스레드가 하나뿐이라 두 잡이
겹칠 수 없다. 영상 처리와 STT 는 CPU 를 다 먹어서, 동시에 돌면 API 응답이 같이 죽는다.

잡 상태는 메모리에만 있다. 프로세스가 죽으면 사라지지만 DB 의 결과물은 남는다 —
잡은 진행 표시용이고 정본은 언제나 DB 다.
"""

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    target: str
    status: str = "QUEUED"
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "createdAt": self.created_at,
            "finishedAt": self.finished_at,
        }


class Busy(RuntimeError):
    """이미 도는 잡이 있어 새 잡을 받지 않았다. `running` 이 그 잡이다."""

    def __init__(self, running: Job) -> None:
        super().__init__(f"이미 실행 중이다: {running.kind}({running.target})")
        self.running = running


class JobQueue:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shorts-worker")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, target: str, fn: Callable[[], Any], exclusive: bool = True) -> Job:
        """잡을 띄운다. 이미 도는 잡이 있으면 `Busy` 다(2026-09-16).

        🔴 예전에는 무조건 받아 큐에 쌓았다. 워커가 하나뿐이라 안전하기는 했지만, 여러 명이
        같은 화면을 보는 상황에서는 **다섯 명이 각자 누른 다섯 개가 조용히 줄을 서고** 마지막
        사람은 몇 분 뒤에야 자기 결과를 본다. 무엇보다 그 사이 Gemini 하루 할당량이 탄다.
        거절하고 무엇이 돌고 있는지 말해 주는 편이 정직하다.

        🔴 검사와 등록이 **같은 락 안**이어야 한다. 밖에서 `active()` 로 보고 나서 넣으면 동시에
        누른 둘이 둘 다 통과한다 — 이 함수가 존재하는 이유가 바로 그 경합이다.
        """
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, target=target)
        with self._lock:
            if exclusive:
                running = self._active_locked()
                if running is not None:
                    raise Busy(running)
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, fn)
        return job

    def _active_locked(self) -> Job | None:
        """부르는 쪽이 `self._lock` 을 쥔다."""
        for job in self._jobs.values():
            if job.status in ("QUEUED", "RUNNING"):
                return job
        return None

    def _run(self, job: Job, fn: Callable[[], Any]) -> None:
        with self._lock:
            job.status = "RUNNING"
        try:
            result = fn()
            with self._lock:
                job.status, job.result = "DONE", result
        except Exception as exc:
            with self._lock:
                job.status = "FAILED"
                job.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            with self._lock:
                job.finished_at = datetime.now(timezone.utc).isoformat()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]

    def active(self) -> Job | None:
        with self._lock:
            return self._active_locked()
