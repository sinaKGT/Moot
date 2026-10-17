"""Small text helpers for cleaning up model output."""

from __future__ import annotations


class NamePrefixStripper:
    """Remove a leading "Name:" that models sometimes add, even while text is streaming.

    Text is held back only while it could still turn out to be the prefix, so normal
    answers stream without delay.
    """

    def __init__(self, name: str) -> None:
        self._prefixes = [p.casefold() for p in (f"{name}:", f"**{name}:**", f"**{name}**:")]
        self._buffer = ""
        self._done = False

    def feed(self, text: str) -> str:
        if self._done:
            return text
        self._buffer += text
        candidate = self._buffer.lstrip()
        folded = candidate.casefold()
        for prefix in self._prefixes:
            if folded.startswith(prefix):
                self._done = True
                return candidate[len(prefix) :].lstrip()
        if not candidate or any(prefix.startswith(folded) for prefix in self._prefixes):
            return ""  # could still become a prefix; wait for more text
        self._done = True
        return candidate

    def flush(self) -> str:
        """Release anything still held back (the stream has ended)."""
        if self._done:
            return ""
        self._done = True
        return self._buffer.lstrip()
