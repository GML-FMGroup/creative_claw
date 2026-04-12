"""Local browser-based web chat channel for CreativeClaw."""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from websockets.exceptions import ConnectionClosed
from websockets.datastructures import Headers
from websockets.legacy.server import WebSocketServerProtocol, serve

from conf.channel import WebChannelConfig
from src.logger import logger
from src.runtime import InboundMessage
from src.runtime.workspace import looks_like_image, resolve_workspace_path, workspace_relative_path

from .base import BaseChannel
from .events import OutboundMessage


STATIC_PACKAGE = "src.webchat.static"
INDEX_FILE = "index.html"


@dataclass(slots=True)
class _ClientConnection:
    """Connected browser client information."""

    websocket: WebSocketServerProtocol
    session_id: str
    client_id: str


def _guess_content_type(filename: str) -> str:
    """Guess one response content type from a file name."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _normalize_static_path(raw_path: str) -> str:
    """Normalize one static asset path and reject traversal."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0] or "/"
    if path == "/":
        return INDEX_FILE
    pure = PurePosixPath(path.lstrip("/"))
    if pure.is_absolute() or ".." in pure.parts:
        return INDEX_FILE
    normalized = pure.as_posix()
    return normalized or INDEX_FILE


def _normalize_workspace_relative_path(raw_path: str) -> str | None:
    """Normalize one workspace asset URL path and reject traversal."""
    decoded = unquote(raw_path or "").strip()
    pure = PurePosixPath(decoded.lstrip("/"))
    if pure.is_absolute() or ".." in pure.parts:
        return None
    normalized = pure.as_posix().strip()
    return normalized or None


class WebChannel(BaseChannel):
    """Serve one local browser chat surface over HTTP and WebSocket."""

    name = "web"

    def __init__(
        self,
        *,
        config: WebChannelConfig,
        inbound_handler: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        super().__init__()
        self.config = config
        self.inbound_handler = inbound_handler
        self._server = None
        self._server_task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._sessions: dict[str, list[_ClientConnection]] = {}
        self._client_seq = 0
        self._host = config.host
        self._port = config.port

    @property
    def url(self) -> str:
        """Return the local browser URL."""
        return f"http://{self._host}:{self._port}"

    async def wait_until_started(self) -> None:
        """Wait until the HTTP/WebSocket server is listening."""
        await self._started.wait()

    async def start(self) -> None:
        """Start the Web chat service in the background."""
        if self._running:
            return
        self._running = True
        self._started = asyncio.Event()
        self._server_task = asyncio.create_task(self._run_server(), name="creative-claw-webchat")
        started_waiter = asyncio.create_task(self._started.wait(), name="creative-claw-webchat-started")
        done, pending = await asyncio.wait(
            {started_waiter, self._server_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for pending_task in pending:
            pending_task.cancel()
        if self._server_task in done and not self._started.is_set():
            self._running = False
            await self._server_task
            raise RuntimeError("Web chat server stopped before reporting readiness.")
        logger.info("CreativeClaw Web chat listening on {}", self.url)
        if self.config.open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(self.url)

    async def stop(self) -> None:
        """Stop the Web chat service and close active client sockets."""
        self._running = False
        connections = [conn for conns in self._sessions.values() for conn in conns]
        for conn in connections:
            with contextlib.suppress(Exception):
                await conn.websocket.close(code=1001, reason="server shutdown")
        self._sessions.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._server_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task
            self._server_task = None

    async def send(self, message: OutboundMessage) -> None:
        """Send one outbound message to browser clients."""
        await self._broadcast(message.chat_id, self._build_event(message))

    async def _run_server(self) -> None:
        """Run the combined HTTP and WebSocket server."""
        try:
            self._server = await serve(
                self._handle_websocket,
                self.config.host,
                self.config.port,
                process_request=self._process_request,
                max_size=2**20,
            )
            socket = self._server.sockets[0]
            host, port = socket.getsockname()[:2]
            self._host = host
            self._port = port
            self._started.set()
            await self._server.wait_closed()
        except Exception as exc:
            logger.opt(exception=exc).error("CreativeClaw Web chat failed during startup or runtime.")
            raise

    def _build_event(self, message: OutboundMessage) -> dict[str, Any]:
        """Convert one outbound message into a browser payload."""
        metadata = dict(message.metadata or {})
        payload_type = "assistant_message"
        if metadata.get("display_style") == "progress":
            payload_type = "progress"
        elif str(message.text or "").startswith("Error:"):
            payload_type = "error"

        return {
            "type": payload_type,
            "content": message.text,
            "format": "markdown",
            "artifacts": self._build_artifacts(message.artifact_paths),
            "metadata": metadata,
        }

    def _build_artifacts(self, artifact_paths: list[str]) -> list[dict[str, Any]]:
        """Build browser-facing artifact metadata for one outbound message."""
        artifacts: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for raw_path in artifact_paths:
            cleaned_path = str(raw_path or "").strip()
            if not cleaned_path:
                continue
            try:
                resolved = resolve_workspace_path(cleaned_path)
            except Exception:
                continue
            if not resolved.exists() or not resolved.is_file():
                continue
            relative_path = workspace_relative_path(resolved)
            url = f"/workspace/{quote(relative_path)}"
            if url in seen_urls:
                continue
            seen_urls.add(url)
            artifacts.append(
                {
                    "name": resolved.name,
                    "path": relative_path,
                    "url": url,
                    "isImage": looks_like_image(resolved),
                    "mimeType": _guess_content_type(resolved.name),
                }
            )
        return artifacts

    async def _broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        """Broadcast one payload to all browser tabs in the same session."""
        connections = list(self._sessions.get(session_id, []))
        if not connections:
            return
        encoded = json.dumps(payload, ensure_ascii=False)
        stale: list[_ClientConnection] = []
        for conn in connections:
            try:
                await conn.websocket.send(encoded)
            except ConnectionClosed:
                stale.append(conn)
        for conn in stale:
            self._drop_connection(conn)

    async def _handle_websocket(self, websocket: WebSocketServerProtocol) -> None:
        """Handle one browser websocket session."""
        parsed = urlparse(websocket.path)
        if parsed.path != "/ws":
            await websocket.close(code=1008, reason="invalid path")
            return

        query = parse_qs(parsed.query)
        session_id = (query.get("session_id") or ["default"])[0].strip() or "default"
        client_id = self._next_client_id()
        conn = _ClientConnection(websocket=websocket, session_id=session_id, client_id=client_id)
        self._sessions.setdefault(session_id, []).append(conn)
        await websocket.send(
            json.dumps(
                {
                    "type": "ready",
                    "sessionId": session_id,
                    "clientId": client_id,
                    "title": self.config.title,
                },
                ensure_ascii=False,
            )
        )
        try:
            async for raw in websocket:
                await self._handle_client_message(conn, raw)
        except ConnectionClosed:
            pass
        finally:
            self._drop_connection(conn)

    async def _handle_client_message(self, conn: _ClientConnection, raw: str) -> None:
        """Normalize one browser-originated message into an inbound runtime event."""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Invalid JSON payload"}, ensure_ascii=False)
            )
            return

        if payload.get("type") != "chat":
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Unsupported event type"}, ensure_ascii=False)
            )
            return

        content = str(payload.get("content") or "").strip()
        if not content:
            await conn.websocket.send(
                json.dumps({"type": "error", "message": "Message content is required"}, ensure_ascii=False)
            )
            return

        await self.inbound_handler(
            InboundMessage(
                channel=self.name,
                sender_id=conn.client_id,
                chat_id=conn.session_id,
                text=content,
                metadata={"client_id": conn.client_id},
            )
        )

    async def _process_request(
        self,
        path: str,
        _request_headers: Headers,
    ) -> tuple[HTTPStatus, list[tuple[str, str]], bytes] | None:
        """Serve static assets and workspace files from the websocket server."""
        parsed = urlparse(path)
        if parsed.path == "/ws":
            return None

        if parsed.path.startswith("/workspace/"):
            return self._serve_workspace_asset(parsed.path.removeprefix("/workspace/"))

        resolved_path = _normalize_static_path(parsed.path)
        if not self._asset_exists(resolved_path):
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )

        body = self._read_asset(resolved_path)
        headers = [
            ("Content-Type", self._content_type_header(resolved_path)),
            ("Cache-Control", "no-cache"),
        ]
        if resolved_path == INDEX_FILE:
            body = body.replace(b"__CREATIVE_CLAW_TITLE__", self.config.title.encode("utf-8"))
        return HTTPStatus.OK, headers, body

    def _serve_workspace_asset(self, raw_relative_path: str) -> tuple[HTTPStatus, list[tuple[str, str]], bytes]:
        """Serve one file from the CreativeClaw workspace."""
        normalized = _normalize_workspace_relative_path(raw_relative_path)
        if normalized is None:
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        try:
            resolved = resolve_workspace_path(normalized)
        except Exception:
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        if not resolved.exists() or not resolved.is_file():
            return (
                HTTPStatus.NOT_FOUND,
                [("Content-Type", "text/plain; charset=utf-8")],
                b"Not Found",
            )
        return (
            HTTPStatus.OK,
            [("Content-Type", self._content_type_header(resolved.name)), ("Cache-Control", "no-cache")],
            resolved.read_bytes(),
        )

    def _asset_exists(self, relative_path: str) -> bool:
        """Return whether one packaged static asset exists."""
        return resources.files(STATIC_PACKAGE).joinpath(relative_path).is_file()

    def _read_asset(self, relative_path: str) -> bytes:
        """Read one packaged static asset."""
        return resources.files(STATIC_PACKAGE).joinpath(relative_path).read_bytes()

    def _content_type_header(self, filename: str) -> str:
        """Build one response content type header."""
        content_type = _guess_content_type(filename)
        if filename.endswith((".html", ".js", ".css", ".json", ".svg")):
            return f"{content_type}; charset=utf-8"
        return content_type

    def _next_client_id(self) -> str:
        """Generate the next browser client identifier."""
        self._client_seq += 1
        return f"web-client-{self._client_seq}"

    def _drop_connection(self, conn: _ClientConnection) -> None:
        """Remove one closed browser connection from the session registry."""
        session_connections = self._sessions.get(conn.session_id)
        if not session_connections:
            return
        self._sessions[conn.session_id] = [item for item in session_connections if item is not conn]
        if not self._sessions[conn.session_id]:
            self._sessions.pop(conn.session_id, None)
