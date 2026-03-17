from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings


def apply_validation_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(error["msg"] for error in exc.errors())
        return JSONResponse(status_code=422, content={"detail": message})


def add_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin, "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
