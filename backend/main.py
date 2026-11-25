from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.admin_routes import router as admin_router
from app.ai_routes import router as ai_router
from app.auth import load_signing_key
from app.auth_routes import router as auth_router
from app.request_routes import router as request_router
from app.reviewer_analytics import router as analytics_router
from app.reviewer_routes import router as reviewer_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.jwt_signing_key = load_signing_key()
    yield


app = FastAPI(title="FieldFix AI 2.0 API", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(request_router)
app.include_router(reviewer_router)
app.include_router(ai_router)
app.include_router(analytics_router)
app.include_router(admin_router)


@app.get("/")
async def identify_api() -> dict[str, str]:
    return {"name": app.title}


@app.get("/api/health")
async def read_health() -> dict[str, str]:
    return {"status": "ok", "service": "fieldfix-api"}
