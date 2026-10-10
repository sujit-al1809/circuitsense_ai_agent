"""Session storage and optional S3 persistence of uploads.

``SessionStore`` is the persistence boundary. The MVP ships ``InMemorySessionStore``:
sessions live in process memory (bounded LRU) and are lost on restart, and they are
not shared between multiple server processes/containers. A DynamoDB/Redis store can
implement the same protocol later without touching the API layer.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.schemas.chat import ChatMessage
from app.schemas.circuit import CircuitAnalysis, Measurement

logger = logging.getLogger(__name__)


@dataclass
class Session:
    analysis: CircuitAnalysis
    preview_png: bytes | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def circuit_id(self) -> str:
        return self.analysis.circuit_id


class SessionStore(Protocol):
    name: str

    def save(self, session: Session) -> None: ...

    def get(self, circuit_id: str) -> Session | None: ...

    def count(self) -> int: ...


class InMemorySessionStore:
    name = "in-memory"

    def __init__(self, max_sessions: int = 100):
        self.max_sessions = max_sessions
        self._data: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def save(self, session: Session) -> None:
        session.updated_at = datetime.now(timezone.utc)
        with self._lock:
            self._data[session.circuit_id] = session
            self._data.move_to_end(session.circuit_id)
            while len(self._data) > self.max_sessions:
                evicted, _ = self._data.popitem(last=False)
                logger.info("evicted session %s (store limit %d)", evicted, self.max_sessions)

    def get(self, circuit_id: str) -> Session | None:
        with self._lock:
            session = self._data.get(circuit_id)
            if session is not None:
                self._data.move_to_end(circuit_id)
            return session

    def count(self) -> int:
        with self._lock:
            return len(self._data)


class UploadArchive(Protocol):
    def put(self, circuit_id: str, filename: str, data: bytes, content_type: str) -> str | None: ...


class NoopUploadArchive:
    def put(self, circuit_id: str, filename: str, data: bytes, content_type: str) -> str | None:
        return None


class S3UploadArchive:
    """Stores original uploads in S3 (server-side encrypted). Failures are logged, never fatal."""

    def __init__(self, bucket: str, prefix: str, client: Any):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client

    def put(self, circuit_id: str, filename: str, data: bytes, content_type: str) -> str | None:
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)[:120] or "upload"
        key = f"{self.prefix}/{circuit_id}/{safe_name}"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        except Exception as exc:  # botocore errors vary; keep the request alive
            logger.warning("S3 upload of %s failed: %s", key, exc)
            return None
        return f"s3://{self.bucket}/{key}"
