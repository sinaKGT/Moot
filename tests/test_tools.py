"""Tools for agents: the built-ins, MCP servers, and the research step before a turn."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fakes import FakeProvider, default_script
from fastapi.testclient import TestClient

from moot.core import ModelRef, Persona, Round, RoundPlan, RoundType, SessionConfig, SessionState
from moot.core.events import ToolUsed
from moot.engine import DiscussionEngine, ProviderPool
from moot.server import SessionManager, create_app
from moot.storage import export_markdown
from moot.tools import McpHub, McpServerConfig, ToolBox, ToolError
from moot.tools.builtins import (
    BUILT_INS,
    _check_public,
    calculate,
    calculator,
    fetch_page,
    html_to_text,
    web_search,
)
from moot.tools.mcp import load_configs

MODEL = ModelRef(provider="fake", model="fake-model")


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def no_check(url):
    return None


# ------------------------------------------------------------------ calculator


def test_the_calculator_does_arithmetic_only():
    assert calculate("(38 - 25) / 25 * 100") == 52
    assert calculate("2^10 + sqrt(16) - abs(-3)") == 1025
    assert calculate("1,000 × 3") == 3000
    assert calculator("round(pi, 2)").text == "round(pi, 2) = 3.14"
    assert calculator("10 / 4").summary == "calculated 10 / 4 = 2.5"
    for bad in (
        "__import__('os').system('x')",
        "().__class__",
        "open('x')",
        "9**99999",
        "x + 1",
        "1/0",
    ):
        with pytest.raises(ToolError):
            calculate(bad)


# ------------------------------------------------------------------ reading a page


def test_pages_become_plain_text():
    title, text = html_to_text(
        "<html><head><title>Car-free Oslo</title><style>p{}</style></head><body>"
        "<script>alert(1)</script><h1>Results</h1><p>Deaths fell to <b>zero</b>.</p></body></html>"
    )
    assert title == "Car-free Oslo"
    assert text == "Results\nDeaths fell to zero."


def test_pages_on_this_computer_or_the_local_network_are_refused():
    for url in (
        "http://127.0.0.1:8765/api",
        "http://localhost/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
    ):
        with pytest.raises(ToolError):
            asyncio.run(_check_public(url))


def test_reading_a_page_follows_redirects_and_checks_each_address():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        if request.url.path == "/new":
            return httpx.Response(200, html="<title>New</title><p>Hello</p>")
        if request.url.path == "/file":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def go():
        async with client_for(handler) as client:
            page = await fetch_page(client, "https://example.org/old", check=no_check)
            assert page.ok and page.text == "New (https://example.org/new)\nHello"
            assert page.summary == "read example.org" and page.links == ["https://example.org/new"]
            with pytest.raises(ToolError, match="application/pdf"):
                await fetch_page(client, "https://example.org/file", check=no_check)
            with pytest.raises(ToolError, match="HTTP 404"):
                await fetch_page(client, "example.org/missing", check=no_check)

            checked = []

            async def refuse_second(url):
                checked.append(url)
                if len(checked) > 1:
                    raise ToolError(
                        "Moot doesn't open addresses on this computer or the local network."
                    )

            with pytest.raises(ToolError, match="local network"):
                await fetch_page(client, "https://example.org/old", check=refuse_second)

    asyncio.run(go())


# ------------------------------------------------------------------ web search


def test_web_search_uses_the_service_that_is_set_up(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization") or request.headers.get(
            "x-subscription-token"
        )
        if "tavily" in request.url.host:
            body = {
                "results": [
                    {"title": "Oslo", "url": "https://a.example", "content": "Deaths fell."}
                ]
            }
        elif "brave" in request.url.host:
            body = {
                "web": {
                    "results": [
                        {
                            "title": "Ghent",
                            "url": "https://b.example",
                            "description": "Traffic down.",
                        }
                    ]
                }
            }
        else:
            body = {
                "results": [
                    {"title": "Madrid", "url": "https://c.example", "content": "Ring road."}
                ]
            }
        return httpx.Response(200, json=body)

    async def search(query):
        async with client_for(handler) as client:
            return await web_search(client, query)

    for name in ("TAVILY_API_KEY", "BRAVE_API_KEY", "SEARXNG_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ToolError, match="isn't set up"):
        asyncio.run(search("x"))

    monkeypatch.setenv("SEARXNG_URL", "http://search.local/")
    assert "Madrid" in asyncio.run(search("ring road")).text
    assert seen["url"].startswith("http://search.local/search?q=ring+road&format=json")

    monkeypatch.setenv("BRAVE_API_KEY", "b-key")
    found = asyncio.run(search("ghent"))
    assert found.links == ["https://b.example"] and seen["auth"] == "b-key"

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-key")
    found = asyncio.run(search("  oslo   deaths "))
    assert found.text == "1. Oslo (https://a.example)\n   Deaths fell."
    assert found.summary == 'web search: "oslo deaths"' and seen["auth"] == "Bearer tvly-key"


def test_a_rejected_key_is_explained(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "wrong")

    async def go():
        async with client_for(lambda request: httpx.Response(401)) as client:
            await web_search(client, "x")

    with pytest.raises(ToolError, match="Check the API key"):
        asyncio.run(go())


# ------------------------------------------------------------------ MCP


def demo_server():
    from mcp.server import MCPServer

    server = MCPServer("demo")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def fail(reason: str) -> str:
        """Always fails."""
        raise ValueError(reason)

    return server


def test_an_mcp_server_connects_lists_its_tools_and_runs_them(tmp_path):
    path = tmp_path / "mcp.json"

    async def go():
        hub = McpHub(path)
        config = McpServerConfig(command="demo-server", env={"TOKEN": "secret"})
        connection = await hub.put("demo", config, target=demo_server())
        assert connection.status == "connected", connection.error
        specs = hub.specs()
        assert sorted(specs) == ["mcp:demo:add", "mcp:demo:fail"]
        assert specs["mcp:demo:add"].name == "demo.add"
        assert "a" in specs["mcp:demo:add"].parameters["properties"]
        added = await hub.call("mcp:demo:add", {"a": 2, "b": 3})
        assert (added.ok, added.text, added.summary) == (True, "5", "used demo.add (a: 2, b: 3)")
        failed = await hub.call("mcp:demo:fail", {"reason": "nope"})
        assert (
            not failed.ok
            and failed.summary == "used demo.fail (reason: nope) (it reported an error)"
        )
        with pytest.raises(ToolError, match="no MCP server called other"):
            await hub.call("mcp:other:x", {})
        await hub.shutdown()

    asyncio.run(go())
    saved = load_configs(path)
    assert saved["demo"].command == "demo-server" and saved["demo"].env == {"TOKEN": "secret"}
    assert json.loads(path.read_text())["mcpServers"]["demo"]["command"] == "demo-server"


def test_a_server_that_fails_to_start_is_reported_not_fatal():
    async def go():
        hub = McpHub(None)
        connection = await hub.put(
            "broken", McpServerConfig(command="no-such-command-for-moot-tests")
        )
        assert connection.status == "failed" and connection.error
        assert hub.specs() == {}
        with pytest.raises(ToolError, match="isn't connected"):
            await hub.call("mcp:broken:x", {})
        assert await hub.remove("broken") and not await hub.remove("broken")
        await hub.shutdown()

    asyncio.run(go())


def test_server_settings_are_checked():
    with pytest.raises(ValueError, match="either a command"):
        McpServerConfig()
    with pytest.raises(ValueError, match="either a command"):
        McpServerConfig(command="x", url="https://a.example/mcp")
    with pytest.raises(ValueError, match="http"):
        McpServerConfig(url="ftp://a.example")


# ------------------------------------------------------------------ in a discussion


def test_unknown_tools_are_refused_on_an_agent():
    assert Persona(name="Ava", tools=["calculator", "mcp:demo:add", "calculator"]).tools == [
        "calculator",
        "mcp:demo:add",
    ]
    with pytest.raises(ValueError, match="unknown tool"):
        Persona(name="Ava", tools=["shell"])


def test_an_agent_researches_before_it_speaks():
    asks = []

    def script(request):
        last = request.messages[-1].content
        if "Before you speak, you may use your tools" in last:
            asks.append(last)
            if len(asks) == 1:
                return [
                    json.dumps(
                        {"tool": "calculator", "arguments": {"expression": "(38-25)/25*100"}}
                    )
                ]
            return ['{"tool": null}']
        return default_script(request)

    provider = FakeProvider(script)
    config = SessionConfig(
        topic="Is Berlin much dearer than Lisbon?",
        agents=[Persona(name="Ava", tools=["calculator", "web_search"]), Persona(name="Ben")],
        round_plan=RoundPlan(rounds=[Round(type=RoundType.OPENING)]),
        default_model=MODEL,
    )
    state = SessionState.new(config)
    engine = DiscussionEngine(
        state,
        providers=ProviderPool(factory=lambda preset: provider),
        moderator_enabled=False,
        tools=ToolBox(McpHub(None)),
    )
    asyncio.run(engine.run())

    assert len(asks) == 2  # one tool use, then ready
    assert "calculator:" in asks[0] and "web_search" not in asks[0]  # no search service set up
    assert "(38-25)/25*100 = 52" in asks[1]
    ava_turn = next(
        r.messages[-1].content
        for r in provider.requests
        if r.messages[0].content.startswith("You are Ava")
        and "It is your turn" in r.messages[-1].content
        and "Before you speak" not in r.messages[-1].content
    )
    assert "What you found with your tools" in ava_turn and "= 52" in ava_turn
    [used] = [e for e in engine.events.history if isinstance(e, ToolUsed)]
    assert (used.tool, used.ok, used.summary) == (
        "calculator",
        True,
        "calculated (38-25)/25*100 = 52",
    )
    ava, ben = (m for m in state.messages if m.speaker_kind.value == "agent")
    assert [t.summary for t in ava.tools_used] == ["calculated (38-25)/25*100 = 52"]
    assert ben.tools_used == [] and used.message_id == ava.id
    assert "*Ava looked up: calculated (38-25)/25*100 = 52*" in export_markdown(state)


# ------------------------------------------------------------------ the API


def test_the_api_lists_tools_and_manages_mcp_servers(monkeypatch):
    for name in ("TAVILY_API_KEY", "BRAVE_API_KEY", "SEARXNG_URL"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(SessionManager(provider_factory=lambda preset: FakeProvider()))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        overview = client.get("/api/tools").json()
        tools = {t["id"]: t for t in overview["built_in"]}
        assert sorted(tools) == sorted(BUILT_INS)
        assert (
            not tools["web_search"]["available"] and "TAVILY_API_KEY" in tools["web_search"]["note"]
        )
        assert tools["calculator"]["available"] and overview["mcp"] == []

        put = client.put(
            "/api/tools/mcp/broken",
            json={"command": "no-such-command-for-moot-tests", "env": {"KEY": "secret"}},
        )
        assert put.status_code == 200
        body = put.json()
        assert (
            body["status"] == "failed" and body["env_names"] == ["KEY"] and "secret" not in put.text
        )
        assert client.put("/api/tools/mcp/Bad Name", json={"command": "x"}).status_code == 400
        assert client.put("/api/tools/mcp/x", json={}).status_code == 422
        assert [s["name"] for s in client.get("/api/tools").json()["mcp"]] == ["broken"]
        assert client.post("/api/tools/mcp/broken/reconnect").json()["status"] == "failed"
        assert client.delete("/api/tools/mcp/broken").status_code == 204
        assert client.delete("/api/tools/mcp/broken").status_code == 404
