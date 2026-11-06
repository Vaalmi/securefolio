"""Builds the app and runs it on a free loopback port."""

import contextlib
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import (
    agent_service,
    auth,
    dependencies,
    logs,
    mcp_server,
    model_budget,
    model_store,
    netguard,
    runtime_file,
    settings,
    sources,
    vault,
    vault_lock,
    vault_move,
)
from backend.migrations import run as migrations
from backend.routes import collections as collection_routes
from backend.routes import documents as document_routes
from backend.routes import errors, health, imports, jobs, keys
from backend.routes import library as library_routes
from backend.routes import memories as memory_routes
from backend.routes import notes as note_routes
from backend.routes import search as search_routes
from backend.routes import settings as settings_routes
from backend.routes import sources as source_routes
from backend.routes import threads as thread_routes
from backend.worker_supervisor import WorkerSupervisor, worker_command

API_PREFIX = "/api"
PORT_ENV = "SECUREFOLIO_PORT"
NO_WORKER_ENV = "SECUREFOLIO_NO_WORKER"


def create_app(port: int, tokens: auth.Tokens, write_runtime: bool = True) -> FastAPI:
    server = mcp_server.build()
    mcp_app = server.streamable_http_app(json_response=True, stateless_http=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        migrations.upgrade()
        model_budget.set_reranker_loaded(False)  # this process has loaded nothing yet
        sources.ensure_library_source()
        netguard.install(lambda: bool(settings.get(settings.OFFLINE)))
        await app.state.agent.open()
        model_store.choose_local_model()
        model_store.resume_at_startup()
        if not os.environ.get(NO_WORKER_ENV):
            app.state.supervisor.start()
        if write_runtime:
            runtime_file.write(port, tokens)
        async with server.session_manager.run():
            yield
        if write_runtime:
            runtime_file.remove()
        await app.state.agent.close()
        await app.state.supervisor.stop()

    app = FastAPI(
        title="SecureFolio",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.port = port
    app.state.tokens = tokens
    app.state.supervisor = WorkerSupervisor(worker_command())
    app.state.agent = agent_service.Service()
    app.add_middleware(dependencies.MaintenanceGate)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(dependencies.allowed_origins()),
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Added last, so it runs first.
    app.add_middleware(dependencies.LoopbackGate, mcp_prefix=mcp_server.MCP_PREFIX)
    errors.install(app)
    for module in (
        health,
        imports,
        search_routes,
        library_routes,
        document_routes,
        source_routes,
        jobs,
        collection_routes,
        note_routes,
        thread_routes,
        memory_routes,
        keys,
        settings_routes,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    app.mount("/", mcp_app)
    return app


def free_port() -> int:
    with contextlib.closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    moved = vault_move.adopt_docsynth_library()  # first: before anything opens it
    vault.use_model_cache()
    logs.configure("backend")
    held = vault_lock.acquire(moved)  # released by the OS when this process ends
    port = int(os.environ.get(PORT_ENV) or free_port())
    uvicorn.run(
        create_app(port, auth.new_tokens()),
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("SECUREFOLIO_SERVER_LOG", "warning"),
    )
    held.release()


if __name__ == "__main__":
    main()
