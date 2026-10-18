"""The live event stream, as server-sent events (text/event-stream).

Each event carries its sequence number as the SSE id, so a browser that reconnects
continues where it stopped (Last-Event-ID). A comment line every few seconds keeps idle
connections open.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from moot.engine import EventStream

HEARTBEAT_SECONDS = 15.0
RETRY_MILLISECONDS = 3000


async def event_source(
    stream: EventStream, *, after: int = 0, heartbeat: float = HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """SSE text for every event after `after`, until the discussion ends."""
    events = aiter(stream.subscribe(after=after))
    pending: asyncio.Future | None = None
    yield f"retry: {RETRY_MILLISECONDS}\n\n"
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=heartbeat)
            if not done:
                yield ": ping\n\n"
                continue
            future, pending = pending, None
            try:
                event = future.result()
            except StopAsyncIteration:
                return
            yield f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await events.aclose()
