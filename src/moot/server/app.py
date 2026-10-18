"""The Moot server: a REST API for setting up and steering discussions, and a live event
stream for watching them. The app (UI) will be served from here too.

Run it with `moot serve`; the API is documented at /docs while it runs.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from moot import __version__
from moot.core import (
    GLOBAL_RULES,
    RULE_PRESET_TEXT,
    TEMPLATES,
    HumanModerator,
    ModelRef,
    Persona,
    PollKind,
    PollSpec,
    RoundPlan,
    SessionConfig,
    SessionRules,
    SessionState,
    starter_personas,
    winner_poll_spec,
)
from moot.demo import DEMO_TOPIC, demo_config
from moot.memory import AllianceMonitor, alliance_monitor, argument_map
from moot.providers import PRESETS, ProviderError
from moot.server.security import LOCAL_HOSTS, LocalOnly
from moot.server.sessions import SessionManager, SessionNotFound, SessionRecord
from moot.server.sse import event_source

STATIC_DIR = Path(__file__).parent / "static"  # the built app (frontend/ builds into it)

# ------------------------------------------------------------------ API models


class SessionSummary(BaseModel):
    id: str
    topic: str
    status: str
    created_at: datetime
    current_round: int | None
    rounds: int
    agents: list[str]
    moderator: str
    human_moderator: bool
    waiting_for: str | None = Field(description="what the engine waits for from a human moderator")
    error: str | None = None


class SessionView(BaseModel):
    summary: SessionSummary
    state: SessionState


class ProviderInfo(BaseModel):
    id: str
    label: str
    local: bool
    base_url: str | None
    api_key_env: str | None
    key_set: bool = Field(description="whether its API key is set (the key itself is never sent)")


class ModelEntry(BaseModel):
    id: str
    name: str | None = None
    context_length: int | None = None


class TemplateInfo(BaseModel):
    name: str
    plan: RoundPlan


class ExampleTeam(BaseModel):
    """The ready-made panel the team builder starts from."""

    topic: str
    agents: list[Persona]
    round_plan: RoundPlan
    rules: SessionRules


class Library(BaseModel):
    personas: list[Persona]
    templates: dict[str, TemplateInfo]
    rule_presets: dict[str, str]
    global_rules: list[str]
    example: ExampleTeam


class DemoRequest(BaseModel):
    topic: str = DEMO_TOPIC
    provider: str
    model: str
    background: str = ""
    moderator: HumanModerator | None = Field(
        default=None, description="moderate it yourself; leave out for the AI moderator"
    )


class SayRequest(BaseModel):
    text: str = Field(min_length=1)
    to: list[str] = Field(default_factory=list, description="agents to answer, besides @mentions")


class FloorRequest(BaseModel):
    agent: str | None = Field(default=None, description="name or id; leave out for a fair pick")


class ReminderRequest(BaseModel):
    agent: str
    text: str = Field(min_length=1)


class PollRequest(BaseModel):
    question: str | None = Field(
        default=None, description="leave out for 'who made the strongest case?'"
    )
    options: list[str] = Field(default_factory=list)
    kind: PollKind | None = None

    def spec(self) -> PollSpec:
        if self.kind is PollKind.WINNER or (not self.question and not self.options):
            winner = winner_poll_spec()
            return (
                winner
                if not self.question
                else PollSpec(question=self.question, kind=PollKind.WINNER)
            )
        return PollSpec(question=self.question or "", options=self.options)


class MapSection(BaseModel):
    title: str
    items: list[str]


# ------------------------------------------------------------------ helpers


def summarize(record: SessionRecord) -> SessionSummary:
    state, config = record.state, record.state.config
    return SessionSummary(
        id=state.id,
        topic=config.topic,
        status=state.status.value,
        created_at=state.created_at,
        current_round=state.current_round,
        rounds=len(config.round_plan),
        agents=[agent.name for agent in config.agents],
        moderator=config.moderator_name,
        human_moderator=isinstance(config.moderator, HumanModerator),
        waiting_for=record.engine.waiting_for,
        error=record.error,
    )


def manager_of(request: Request) -> SessionManager:
    return request.app.state.manager


Manager = Annotated[SessionManager, Depends(manager_of)]


def session_of(session_id: str, manager: Manager) -> SessionRecord:
    try:
        return manager.get(session_id)
    except SessionNotFound:
        raise HTTPException(404, f"No session with id {session_id}") from None


Session = Annotated[SessionRecord, Depends(session_of)]


@contextlib.contextmanager
def engine_errors() -> Iterator[None]:
    """Turn the engine's refusals into HTTP errors: bad input 400, wrong moment 409."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None


# ------------------------------------------------------------------ the app


def create_app(
    manager: SessionManager | None = None,
    *,
    allowed_hosts: Sequence[str] | None = LOCAL_HOSTS,
    allowed_origins: Sequence[str] = (),
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """The server app. `allowed_hosts=None` accepts any host (serving on the network).

    Every route is async, so it runs on the same event loop as the discussions: the
    engine is not thread-safe.
    """
    manager = manager or SessionManager()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await manager.shutdown()

    app = FastAPI(
        title="Moot",
        version=__version__,
        summary="Moderated multi-agent discussions: set them up, steer them, watch them live.",
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.add_middleware(LocalOnly, hosts=allowed_hosts, origins=allowed_origins)

    @app.get("/", include_in_schema=False)
    async def home() -> Response:
        """The app, once built; until then, the test page."""
        index = static_dir / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text("utf-8"))
        return RedirectResponse("/dev")

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> Response:
        icon = static_dir / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        raise HTTPException(404)

    app.mount(
        "/assets", StaticFiles(directory=static_dir / "assets", check_dir=False), name="assets"
    )

    @app.get("/dev", include_in_schema=False)
    async def dev_page() -> HTMLResponse:
        return HTMLResponse(files("moot.server").joinpath("dev.html").read_text("utf-8"))

    # -------------------------------------------------------------- general

    @app.get("/api/health", tags=["general"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/providers", tags=["models"])
    async def providers() -> list[ProviderInfo]:
        return [
            ProviderInfo(
                id=p.id,
                label=p.label,
                local=p.local,
                base_url=p.base_url,
                api_key_env=p.api_key_env,
                key_set=bool(p.api_key_env and os.environ.get(p.api_key_env)),
            )
            for p in PRESETS.values()
        ]

    @app.get("/api/providers/{preset_id}/models", tags=["models"])
    async def models(
        preset_id: str,
        manager: Manager,
        base_url: str | None = None,
    ) -> list[ModelEntry]:
        """The models a provider offers right now (for local servers: the loaded ones)."""
        with engine_errors():
            provider = manager.provider(preset_id, base_url)
        try:
            found = await provider.list_models()
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from None
        finally:
            await provider.aclose()
        return [ModelEntry(**asdict(model)) for model in found]

    @app.get("/api/library", tags=["general"])
    async def library() -> Library:
        """Starter personas, round-plan templates, rule presets and a ready-made panel."""
        demo = demo_config(DEMO_TOPIC, ModelRef(provider="openrouter", model="any"))
        return Library(
            example=ExampleTeam(
                topic=demo.topic, agents=demo.agents, round_plan=demo.round_plan, rules=demo.rules
            ),
            personas=starter_personas(),
            templates={
                key: TemplateInfo(name=name, plan=make()) for key, (name, make) in TEMPLATES.items()
            },
            rule_presets={preset.value: text for preset, text in RULE_PRESET_TEXT.items()},
            global_rules=list(GLOBAL_RULES),
        )

    # -------------------------------------------------------------- sessions

    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions(manager: Manager) -> list[SessionSummary]:
        return [summarize(record) for record in manager.all()]

    @app.post("/api/sessions", status_code=201, tags=["sessions"])
    async def create_session(config: SessionConfig, manager: Manager) -> SessionSummary:
        """Set up a discussion from a full configuration. Start it with /start."""
        with engine_errors():
            return summarize(manager.create(config))

    @app.post("/api/demo", status_code=201, tags=["sessions"])
    async def create_demo(request: DemoRequest, manager: Manager) -> SessionSummary:
        """Set up the ready-made demo (Ava, Ben and Cleo) on any topic and model."""
        model = ModelRef(provider=request.provider, model=request.model)
        with engine_errors():
            config = demo_config(request.topic, model, request.background, request.moderator)
            return summarize(manager.create(config))

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def get_session(record: Session) -> SessionView:
        """Everything about a session: messages, argument graph, notebooks, alliances, polls."""
        return SessionView(summary=summarize(record), state=record.state)

    @app.delete("/api/sessions/{session_id}", status_code=204, tags=["sessions"])
    async def delete_session(
        record: Session,
        manager: Manager,
    ) -> Response:
        """Remove a session, ending it at once if it is running."""
        await manager.delete(record.id)
        return Response(status_code=204)

    # -------------------------------------------------------------- controls

    def control(path: str, action: str, doc: str) -> None:
        async def endpoint(
            record: Session,
            manager: Manager,
        ) -> SessionSummary:
            with engine_errors():
                if action == "start":
                    manager.start(record.id)
                else:
                    getattr(record.engine, action)()
            return summarize(record)

        endpoint.__doc__ = doc
        app.post(
            f"/api/sessions/{{session_id}}/{path}",
            tags=["controls"],
            name=action,
            operation_id=action,
        )(endpoint)

    control("start", "start", "Start the discussion; watch it on /events.")
    control("pause", "pause", "Pause after the current turn.")
    control("resume", "resume", "Carry on after a pause.")
    control("stop", "stop", "Stop after the current turn (no conclusion).")
    control("finish", "finish", "End normally after the current turn, skipping rounds left.")
    control("next-round", "next_round", "Start the next round, or end this one and move on.")
    control("end-round", "end_round", "End the current round after the current turn.")

    @app.post("/api/sessions/{session_id}/messages", status_code=202, tags=["controls"])
    async def say(request: SayRequest, record: Session) -> SessionSummary:
        """Say something; agents you @mention (or list in `to`) answer right away."""
        with engine_errors():
            record.engine.say(request.text, to=request.to)
        return summarize(record)

    @app.post("/api/sessions/{session_id}/floor", status_code=202, tags=["controls"])
    async def give_floor(request: FloorRequest, record: Session) -> SessionSummary:
        """Let an agent speak next."""
        with engine_errors():
            record.engine.give_floor(request.agent)
        return summarize(record)

    @app.post("/api/sessions/{session_id}/reminders", status_code=202, tags=["controls"])
    async def remind(request: ReminderRequest, record: Session) -> SessionSummary:
        """Remind an agent of something, publicly."""
        with engine_errors():
            record.engine.remind(request.agent, request.text)
        return summarize(record)

    @app.post("/api/sessions/{session_id}/polls", status_code=202, tags=["controls"])
    async def open_poll(request: PollRequest, record: Session) -> SessionSummary:
        """Run a poll; send an empty body to ask who made the strongest case."""
        with engine_errors():
            record.engine.open_poll(request.spec())
        return summarize(record)

    # -------------------------------------------------------------- watching

    @app.get("/api/sessions/{session_id}/events", tags=["watching"])
    async def events(
        record: Session,
        after: Annotated[int, Query(ge=0, description="skip events up to this number")] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """The live event stream (server-sent events), replayed from the start."""
        if last_event_id and last_event_id.isdigit():
            after = max(after, int(last_event_id))
        return StreamingResponse(
            event_source(record.engine.events, after=after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sessions/{session_id}/alliances", tags=["watching"])
    async def alliances(
        record: Session,
        up_to_round: Annotated[
            int | None, Query(ge=0, description="as it was after this round")
        ] = None,
    ) -> AllianceMonitor:
        """Who backs whom: declared alliances, detected alignment and sides."""
        return alliance_monitor(record.state, up_to_round)

    @app.get("/api/sessions/{session_id}/argument-map", tags=["watching"])
    async def map_sections(record: Session) -> list[MapSection]:
        """The argument map in words: positions, claims, challenges, concessions."""
        return [
            MapSection(title=title, items=items)
            for title, items in argument_map(record.state).sections()
        ]

    return app
