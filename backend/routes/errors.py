"""Turns a refused move into its status, with one line of text."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.refusals import Refused


def install(app: FastAPI) -> None:
    @app.exception_handler(Refused)
    async def refused(_request: Request, error: Refused) -> JSONResponse:
        return JSONResponse({"detail": error.detail}, status_code=error.status)
