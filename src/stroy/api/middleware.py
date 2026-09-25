from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("stroy.http")


class RequestContextMiddleware:
    """Attach a correlation ID and emit one structured log event per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        incoming = headers.get("x-request-id", "")
        request_id = incoming if 0 < len(incoming) <= 128 else str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("ascii", "ignore")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            event = {
                "event": "http_request",
                "request_id": request_id,
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            logger.info(json.dumps(event, separators=(",", ":")))
