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


class JobQueue:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shorts-worker")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, target: str, fn: Callable[[], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, target=target)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, fn)
        return job

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
            for job in self._jobs.values():
                if job.status in ("QUEUED", "RUNNING"):
                    return job
        return None
