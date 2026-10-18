"""The built-in tools: web search, reading a web page, and a calculator.

Web search uses whichever service is set up in the environment: Tavily (TAVILY_API_KEY),
Brave (BRAVE_API_KEY) or a self-hosted SearXNG (SEARXNG_URL). Reading a page refuses
addresses on this computer and the local network, since what to open comes from a model.
The calculator evaluates arithmetic only; no code is run.
"""

from __future__ import annotations

import ast
import asyncio
import ipaddress
import math
import operator
import os
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from moot.tools.base import ToolError, ToolResult, ToolSpec

USER_AGENT = "Moot/0.1 (local discussion app)"
SEARCH_RESULTS = 5
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5

WEB_SEARCH = ToolSpec(
    id="web_search",
    name="web_search",
    description="Search the web. Returns titles, links and short extracts.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "what to search for"}},
        "required": ["query"],
    },
)
FETCH_PAGE = ToolSpec(
    id="fetch_page",
    name="fetch_page",
    description="Read a web page (http or https) as plain text.",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "the page's address"}},
        "required": ["url"],
    },
)
CALCULATOR = ToolSpec(
    id="calculator",
    name="calculator",
    description=(
        "Exact arithmetic: + - * / // % **, brackets, and sqrt, log, log10, exp, sin, cos, tan, "
        "abs, round, min, max, floor, ceil, pi, e. Example: (38 - 25) / 25 * 100"
    ),
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
)
BUILT_INS = {spec.id: spec for spec in (WEB_SEARCH, FETCH_PAGE, CALCULATOR)}


# ---------------------------------------------------------------------- web search


def search_service() -> str | None:
    """Which web search is set up: "tavily", "brave", "searxng" or None."""
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if os.environ.get("BRAVE_API_KEY"):
        return "brave"
    if os.environ.get("SEARXNG_URL"):
        return "searxng"
    return None


SEARCH_LABELS = {"tavily": "Tavily", "brave": "Brave Search", "searxng": "SearXNG"}
SEARCH_HELP = (
    "Set TAVILY_API_KEY (1,000 free searches a month), BRAVE_API_KEY or SEARXNG_URL in .env, "
    "then restart Moot."
)


async def web_search(client: httpx.AsyncClient, query: str) -> ToolResult:
    query = " ".join(str(query).split())[:400]
    if not query:
        raise ToolError("web_search needs a query.")
    service = search_service()
    if service is None:
        raise ToolError("Web search isn't set up. " + SEARCH_HELP)
    try:
        if service == "tavily":
            response = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": SEARCH_RESULTS, "search_depth": "basic"},
                headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
            )
            response.raise_for_status()
            items = [
                (r.get("title", ""), r.get("url", ""), r.get("content", ""))
                for r in response.json().get("results", [])
            ]
        elif service == "brave":
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": SEARCH_RESULTS},
                headers={
                    "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            items = [
                (r.get("title", ""), r.get("url", ""), r.get("description", ""))
                for r in response.json().get("web", {}).get("results", [])
            ]
        else:
            base = os.environ["SEARXNG_URL"].rstrip("/")
            response = await client.get(f"{base}/search", params={"q": query, "format": "json"})
            response.raise_for_status()
            items = [
                (r.get("title", ""), r.get("url", ""), r.get("content", ""))
                for r in response.json().get("results", [])
            ]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = " Check the API key." if status in (401, 403) else ""
        raise ToolError(
            f"{SEARCH_LABELS[service]} refused the search (HTTP {status}).{hint}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ToolError(
            f"{SEARCH_LABELS[service]} couldn't be reached ({type(exc).__name__})."
        ) from exc

    results = [(t.strip(), u.strip(), " ".join(s.split())[:400]) for t, u, s in items if u][
        :SEARCH_RESULTS
    ]
    summary = f'web search: "{query}"'
    if not results:
        return ToolResult(True, f'No results for "{query}".', summary)
    lines = [
        f"{i}. {title} ({url})\n   {snippet}" for i, (title, url, snippet) in enumerate(results, 1)
    ]
    return ToolResult(True, "\n".join(lines), summary, links=[u for _, u, _ in results])


# ---------------------------------------------------------------------- reading a page


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    BLOCK = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "table",
        "blockquote",
        "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).split("\n")]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return " ".join(parser.title.split()), parser.text()


async def _check_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("fetch_page opens http and https addresses only.")
    try:
        found = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    except OSError as exc:
        raise ToolError(f"{parsed.hostname} couldn't be found.") from exc
    for *_, address in found:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global:
            raise ToolError("Moot doesn't open addresses on this computer or the local network.")


async def fetch_page(client: httpx.AsyncClient, url: str, *, check=_check_public) -> ToolResult:
    url = str(url).strip()
    if not urlparse(url).scheme:
        url = "https://" + url
    for _ in range(MAX_REDIRECTS + 1):
        await check(url)
        try:
            async with client.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise ToolError(f"The page answered with HTTP {response.status_code}.")
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind not in (
                    "text/html",
                    "text/plain",
                    "application/xhtml+xml",
                    "application/json",
                    "",
                ):
                    raise ToolError(
                        f"That address is a {kind or 'file'}, not a page Moot can read."
                    )
                body = b""
                async for piece in response.aiter_bytes():
                    body += piece
                    if len(body) > MAX_PAGE_BYTES:
                        break
                text = body.decode(response.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as exc:
            raise ToolError(f"The page couldn't be read ({type(exc).__name__}).") from exc
        title, content = (
            html_to_text(text)
            if kind != "text/plain" and kind != "application/json"
            else ("", text)
        )
        host = urlparse(url).hostname or url
        header = f"{title} ({url})" if title else url
        return ToolResult(True, f"{header}\n{content}", f"read {host}", links=[url])
    raise ToolError("The page redirected too many times.")


# ---------------------------------------------------------------------- calculator

_OPERATORS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FUNCTIONS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def calculate(expression: str) -> float | int:
    """Arithmetic only. Raises ToolError for anything else."""
    text = str(expression).replace("×", "*").replace("÷", "/").replace("^", "**")
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,000 is a thousand
    if len(text) > 300:
        raise ToolError("That expression is too long.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"That isn't an arithmetic expression: {expression}") from exc

    def value(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return value(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name) and node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](value(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Pow) and (
                abs(right) > 1000 or abs(left) > 1e6 and right > 10
            ):
                raise ToolError("That power is too large.")
            return _OPERATORS[type(node.op)](left, right)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
            and not node.keywords
        ):
            return _FUNCTIONS[node.func.id](*(value(arg) for arg in node.args))
        raise ToolError(f"The calculator does arithmetic only: {expression}")

    try:
        result = value(tree)
    except ToolError:
        raise
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise ToolError(f"That can't be calculated: {exc}") from exc
    if isinstance(result, int) and abs(result) > 10**100:
        raise ToolError("The result is too large.")
    return result


def calculator(expression: str) -> ToolResult:
    result = calculate(expression)
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        shown = str(int(result))
    elif isinstance(result, float):
        shown = f"{result:.10g}"
    else:
        shown = str(result)
    return ToolResult(True, f"{expression} = {shown}", f"calculated {expression} = {shown}")
