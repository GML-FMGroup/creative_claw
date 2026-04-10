"""Lightweight background process session manager for builtin exec tools."""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_MAX_OUTPUT_CHARS = 200_000
_MAX_PENDING_CHARS = 30_000
_TRUNCATED_BANNER = "... (truncated earlier output)\n"


@dataclass(slots=True)
class ProcessSessionSnapshot:
    """Serializable view of one managed process session."""

    session_id: str
    command: str
    cwd: str
    scope_key: str | None
    started_at: float
    status: str
    pid: int | None
    exited: bool
    exit_code: int | None


@dataclass(slots=True)
class ProcessSession:
    """Mutable in-memory process session state."""

    session_id: str
    command: str
    cwd: str
    scope_key: str | None
    started_at: float
    process: subprocess.Popen[str]
    exited: bool = False
    exit_code: int | None = None
    kill_requested: bool = False
    output: str = ""
    pending_output: str = ""
    output_truncated: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_event: threading.Event = field(default_factory=threading.Event)


class ProcessSessionManager:
    """Manage background shell sessions started from builtin tools."""

    def __init__(self) -> None:
        self._sessions: dict[str, ProcessSession] = {}
        self._lock = threading.Lock()

    def start_session(
        self,
        *,
        command: str,
        cwd: Path,
        scope_key: str | None = None,
    ) -> ProcessSession:
        """Start one background shell command."""
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        session = ProcessSession(
            session_id=str(uuid.uuid4()),
            command=command,
            cwd=str(cwd),
            scope_key=scope_key,
            started_at=time.time(),
            process=process,
        )
        with self._lock:
            self._sessions[session.session_id] = session

        self._start_reader(session, process.stdout, stream_name="stdout")
        self._start_reader(session, process.stderr, stream_name="stderr")
        self._start_watcher(session)
        return session

    def list_sessions(self, *, scope_key: str | None = None) -> list[ProcessSessionSnapshot]:
        """List all sessions visible to one scope."""
        with self._lock:
            sessions = list(self._sessions.values())

        snapshots: list[ProcessSessionSnapshot] = []
        for session in sessions:
            if not self._scope_allows(session.scope_key, scope_key):
                continue
            with session.lock:
                snapshots.append(
                    ProcessSessionSnapshot(
                        session_id=session.session_id,
                        command=session.command,
                        cwd=session.cwd,
                        scope_key=session.scope_key,
                        started_at=session.started_at,
                        status=self._status_for(session),
                        pid=session.process.pid,
                        exited=session.exited,
                        exit_code=session.exit_code,
                    )
                )
        snapshots.sort(key=lambda item: item.started_at, reverse=True)
        return snapshots

    def poll_session(
        self,
        session_id: str,
        *,
        timeout_ms: int = 0,
        scope_key: str | None = None,
    ) -> dict[str, object] | None:
        """Return and clear pending output for one session."""
        session = self._lookup(session_id, scope_key=scope_key)
        if session is None:
            return None

        wait_ms = max(0, int(timeout_ms))
        if wait_ms > 0:
            should_wait = False
            with session.lock:
                should_wait = not session.exited and not session.pending_output
                if should_wait:
                    session.output_event.clear()
            if should_wait:
                session.output_event.wait(wait_ms / 1000.0)

        with session.lock:
            output = session.pending_output
            session.pending_output = ""
            return {
                "session_id": session.session_id,
                "status": self._status_for(session),
                "output": output,
                "exited": session.exited,
                "exit_code": session.exit_code,
                "pid": session.process.pid,
            }

    def get_log(
        self,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
        scope_key: str | None = None,
    ) -> dict[str, object] | None:
        """Return one paginated combined output log."""
        session = self._lookup(session_id, scope_key=scope_key)
        if session is None:
            return None

        safe_offset = max(0, int(offset))
        safe_limit = max(1, int(limit))
        with session.lock:
            lines = session.output.splitlines()
            paged = lines[safe_offset : safe_offset + safe_limit]
            return {
                "session_id": session.session_id,
                "status": self._status_for(session),
                "lines": paged,
                "offset": safe_offset,
                "limit": safe_limit,
                "has_more": safe_offset + safe_limit < len(lines),
                "exited": session.exited,
                "exit_code": session.exit_code,
            }

    def write_session(
        self,
        session_id: str,
        data: str,
        *,
        scope_key: str | None = None,
        eof: bool = False,
    ) -> bool:
        """Write one chunk to process stdin."""
        session = self._lookup(session_id, scope_key=scope_key)
        if session is None:
            return False
        if session.process.stdin is None:
            return False
        with session.lock:
            if session.exited:
                return False
            try:
                session.process.stdin.write(data)
                session.process.stdin.flush()
                if eof:
                    session.process.stdin.close()
                return True
            except Exception:
                return False

    def kill_session(self, session_id: str, *, scope_key: str | None = None) -> bool:
        """Terminate one running process session."""
        session = self._lookup(session_id, scope_key=scope_key)
        if session is None:
            return False
        with session.lock:
            if session.exited:
                return True
            session.kill_requested = True
        try:
            session.process.terminate()
            session.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            session.process.kill()
        except Exception:
            return False
        return True

    def remove_session(self, session_id: str, *, scope_key: str | None = None) -> bool:
        """Remove one finished session from the manager."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            if not self._scope_allows(session.scope_key, scope_key):
                return False
            with session.lock:
                if not session.exited:
                    return False
            self._sessions.pop(session_id, None)
        return True

    def _lookup(self, session_id: str, *, scope_key: str | None = None) -> ProcessSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        if not self._scope_allows(session.scope_key, scope_key):
            return None
        return session

    @staticmethod
    def _scope_allows(session_scope: str | None, requested_scope: str | None) -> bool:
        if requested_scope is None:
            return True
        return session_scope == requested_scope

    @staticmethod
    def _status_for(session: ProcessSession) -> str:
        if not session.exited:
            return "running"
        if session.kill_requested:
            return "killed"
        return "exited" if int(session.exit_code or 0) == 0 else "failed"

    def _start_reader(
        self,
        session: ProcessSession,
        stream,
        *,
        stream_name: str,
    ) -> None:
        def _reader() -> None:
            if stream is None:
                return
            try:
                for chunk in iter(stream.readline, ""):
                    if not chunk:
                        break
                    self._append_output(session, chunk, stream_name=stream_name)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()

    def _start_watcher(self, session: ProcessSession) -> None:
        def _watcher() -> None:
            exit_code = session.process.wait()
            with session.lock:
                session.exited = True
                session.exit_code = exit_code
                session.output_event.set()

        thread = threading.Thread(target=_watcher, daemon=True)
        thread.start()

    def _append_output(self, session: ProcessSession, chunk: str, *, stream_name: str) -> None:
        normalized = chunk.replace("\r\n", "\n").replace("\r", "\n")
        if stream_name == "stderr":
            labeled = "".join(
                f"[stderr] {line}" if line else line
                for line in normalized.splitlines(keepends=True)
            )
        else:
            labeled = normalized

        with session.lock:
            session.output += labeled
            if len(session.output) > _MAX_OUTPUT_CHARS:
                session.output = _TRUNCATED_BANNER + session.output[-(_MAX_OUTPUT_CHARS - len(_TRUNCATED_BANNER)) :]
                session.output_truncated = True
            session.pending_output += labeled
            if len(session.pending_output) > _MAX_PENDING_CHARS:
                session.pending_output = session.pending_output[-_MAX_PENDING_CHARS:]
            session.output_event.set()


_PROCESS_SESSION_MANAGER: ProcessSessionManager | None = None


def get_process_session_manager() -> ProcessSessionManager:
    """Return one process-session manager singleton."""
    global _PROCESS_SESSION_MANAGER
    if _PROCESS_SESSION_MANAGER is None:
        _PROCESS_SESSION_MANAGER = ProcessSessionManager()
    return _PROCESS_SESSION_MANAGER
