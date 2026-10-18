"""The event stream: the engine emits events here, and the UI, CLI and tests subscribe."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable

from moot.core.events import Event

log = logging.getLogger(__name__)

_CLOSED = object()

Listener = Callable[[Event], None]


class EventStream:
    def __init__(self) -> None:
        self._history: list[Event] = []
        self._queues: list[asyncio.Queue[object]] = []
        self._listeners: list[Listener] = []
        self._last_seq = 0
        self._closed = False

    @classmethod
    def restore(cls, events: Iterable[Event], *, closed: bool, last_seq: int = 0) -> EventStream:
        """A stream holding saved events (their numbers may have gaps, since token deltas
        are not saved); new events are numbered after them and after `last_seq`."""
        stream = cls()
        for event in events:
            if event.seq <= stream._last_seq:
                raise ValueError("saved events must be in order of their numbers")
            stream._history.append(event)
            stream._last_seq = event.seq
        stream._last_seq = max(stream._last_seq, last_seq)
        stream._closed = closed
        return stream

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def listen(self, listener: Listener) -> None:
        """Call `listener` with every new event, as it is emitted (used to save them).
        A failing listener is logged and never stops the discussion."""
        self._listeners.append(listener)

    def emit(self, event: Event) -> Event:
        """Number the event, keep it in the history and deliver it to every subscriber."""
        if self._closed:
            raise RuntimeError("the event stream is closed")
        self._last_seq += 1
        event.seq = self._last_seq
        self._history.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                log.exception("an event listener failed on %s", event.type)
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
            for event in self._history:
                if event.seq > after:
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
