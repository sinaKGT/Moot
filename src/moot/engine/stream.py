"""The event stream: the engine emits events here, and the UI, CLI and tests subscribe."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from moot.core.events import Event

_CLOSED = object()


class EventStream:
    def __init__(self) -> None:
        self._history: list[Event] = []
        self._queues: list[asyncio.Queue[object]] = []
        self._closed = False

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    @property
    def closed(self) -> bool:
        return self._closed

    def emit(self, event: Event) -> Event:
        """Number the event, keep it in the history and deliver it to every subscriber."""
        if self._closed:
            raise RuntimeError("the event stream is closed")
        event.seq = len(self._history) + 1
        self._history.append(event)
        for queue in self._queues:
            queue.put_nowait(event)
        return event

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for queue in self._queues:
            queue.put_nowait(_CLOSED)

    async def subscribe(self, *, replay: bool = True, after: int = 0) -> AsyncIterator[Event]:
        """Yield events as they happen until the stream closes.

        With `replay`, events emitted before subscribing are delivered first, so a
        subscriber never misses the start of a discussion. `after` skips the events up to
        that sequence number, for a viewer that reconnects.
        """
        queue: asyncio.Queue[object] = asyncio.Queue()
        if replay:
            for event in self._history[max(after, 0) :]:
                queue.put_nowait(event)
        if self._closed:
            queue.put_nowait(_CLOSED)
        else:
            self._queues.append(queue)
        try:
            while True:
                item = await queue.get()
                if item is _CLOSED:
                    return
                yield item  # type: ignore[misc]
        finally:
            if queue in self._queues:
                self._queues.remove(queue)
