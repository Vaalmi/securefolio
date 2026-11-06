"""Whether the stores and the worker are ready."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend import database, runtime_file
from backend.dependencies import AppToken

router = APIRouter()


class Health(BaseModel):
    version: str
    database: str
    worker: str


class Empty(BaseModel):
    pass


@router.get("/health", operation_id="get_health")
def get_health(request: Request, _: AppToken) -> Health:
    """Reports the readiness of the stores and the worker."""
    with database.read() as connection:
        connection.exec_driver_sql("SELECT 1")
    return Health(
        version=runtime_file.VERSION,
        database="ready",
        worker=request.app.state.supervisor.state,
    )


@router.post("/worker/restart", operation_id="restart_worker")
async def restart_worker(request: Request, _: AppToken) -> Empty:
    """Starts the worker again after it stopped for good."""
    supervisor = request.app.state.supervisor
    await supervisor.stop()
    supervisor.deaths.clear()
    supervisor.start()
    return Empty()
