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


class TagFilter:
    """Split streamed text into what the audience sees and hidden sections.

    `<think>...</think>` (reasoning some models write) is dropped, and `<notes>...</notes>`
    (the agent's private notes) is captured. Text that might be the start of a tag is held
    back until it is clear, so tags split across chunks are still caught.
    """

    def __init__(
        self, *, hide: tuple[str, ...] = ("think",), capture: tuple[str, ...] = ("notes",)
    ) -> None:
        self._hide = set(hide)
        self._capture = set(capture)
        self._tags = [*hide, *capture]
        self._buffer = ""
        self._inside: str | None = None
        self.captured: dict[str, str] = {}

    def feed(self, text: str) -> str:
        self._buffer += text
        visible: list[str] = []
        while True:
            if self._inside is None:
                position, tag = self._find(self._buffer, [f"<{t}>" for t in self._tags])
                if tag is None:
                    keep = self._partial_suffix(self._buffer, [f"<{t}>" for t in self._tags])
                    visible.append(self._buffer[: len(self._buffer) - keep])
                    self._buffer = self._buffer[len(self._buffer) - keep :]
                    return "".join(visible)
                visible.append(self._buffer[:position])
                self._buffer = self._buffer[position + len(tag) :]
                self._inside = tag[1:-1].lower()
            else:
                closing = f"</{self._inside}>"
                position, tag = self._find(self._buffer, [closing])
                if tag is None:
                    keep = self._partial_suffix(self._buffer, [closing])
                    self._store(self._buffer[: len(self._buffer) - keep])
                    self._buffer = self._buffer[len(self._buffer) - keep :]
                    return "".join(visible)
                self._store(self._buffer[:position])
                self._buffer = self._buffer[position + len(closing) :]
                self._inside = None

    def flush(self) -> str:
        """End of stream: release held-back text (an unclosed section stays hidden)."""
        remainder, self._buffer = self._buffer, ""
        if self._inside is None:
            return remainder
        self._store(remainder)
        return ""

    def _store(self, text: str) -> None:
        if self._inside in self._capture and text:
            self.captured[self._inside] = self.captured.get(self._inside, "") + text

    @staticmethod
    def _find(text: str, needles: list[str]) -> tuple[int, str | None]:
        folded = text.lower()
        best: tuple[int, str | None] = (-1, None)
        for needle in needles:
            index = folded.find(needle.lower())
            if index != -1 and (best[1] is None or index < best[0]):
                best = (index, text[index : index + len(needle)])
        return best

    @staticmethod
    def _partial_suffix(text: str, needles: list[str]) -> int:
        """Length of the longest end of `text` that could be the start of a needle."""
        folded = text.lower()
        longest = 0
        for needle in needles:
            for size in range(min(len(needle) - 1, len(folded)), 0, -1):
                if needle.lower().startswith(folded[-size:]):
                    longest = max(longest, size)
                    break
        return longest


class TrailingSpaceHolder:
    """Hold back whitespace at the end of the streamed text until more text follows, so the
    blank lines before a hidden section (like the private notes) never reach the audience."""

    def __init__(self) -> None:
        self._held = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        combined = self._held + text
        visible = combined.rstrip()
        self._held = combined[len(visible) :]
        return visible

    def flush(self) -> str:
        """End of stream: trailing whitespace is dropped."""
        self._held = ""
        return ""
