"""FastAPI application factory.

``create_app`` wires a validated :class:`Settings` instance into a fresh app;
``create_dev_app`` reads the process environment. The module-level ``app`` is the
bare health-check instance used by tooling that expects no business routes.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine

from app.core.config import Settings
from app.core.env import load_env
from app.core.errors import APIError, api_error
from app.core.middleware import auth_rate_limit
from app.core.observability import request_log
from app.routers.auth import router as auth_router
from app.routers.avatar_assets import router as avatar_assets_router
from app.routers.avatars import router as avatar_router
from app.routers.chat import router as chat_router
from app.routers.config import router as config_router
from app.routers.diet import router as diet_router
from app.routers.health import router as health_router
from app.routers.media import router as media_router
from app.routers.motion import router as motion_router
from app.routers.pets import router as pet_router
from app.routers.quota import router as quota_router
from app.routers.screening import router as screening_router
from app.routers.tasks import router as task_router
from app.routers.vision import router as vision_router
from app.routers.weights import router as weight_router

load_env()

TITLE = '绒星 API（本地联调）'
ROUTERS = (
    auth_router,
    pet_router,
    avatar_router,
    avatar_assets_router,
    motion_router,
    task_router,
    media_router,
    config_router,
    screening_router,
    health_router,
    diet_router,
    weight_router,
    chat_router,
    quota_router,
    vision_router,
)


def health():
    return {'status': 'ok'}


def create_app(settings: Settings) -> FastAPI:
    """Build the API instance around already-validated settings."""

    @asynccontextmanager
    async def lifespan(instance: FastAPI):
        engine = create_engine(settings.database_url, hide_parameters=True,
                               pool_pre_ping=True, pool_recycle=1800)
        instance.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    instance = FastAPI(title=TITLE, version='0.1.0', lifespan=lifespan)
    instance.state.settings = settings
    instance.state.jwt_secret = settings.jwt_secret
    instance.state.policy_versions = settings.policy_versions
    instance.state.policy_links = settings.policy_links
    instance.state.photo_storage = settings.photo_storage
    instance.state.action_map = settings.action_map
    instance.state.triage_rules = settings.triage_rules
    instance.state.advice_templates = settings.advice_templates
    instance.state.feeding_config = settings.feeding_config
    instance.state.chat_config = settings.chat_config
    instance.state.motion_config = settings.motion_config
    instance.state.chat_llm_provider = settings.chat_llm_provider
    instance.state.vision_provider = settings.vision_provider
    instance.state.placeholder_library = settings.placeholder_library

    instance.get('/api/v1/health')(health)
    for router in ROUTERS:
        instance.include_router(router)
    instance.middleware('http')(auth_rate_limit)
    instance.middleware('http')(request_log)
    instance.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r'http://(?:localhost|127\.0\.0\.1):[0-9]+',
        allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
        allow_headers=['Authorization', 'Content-Type', 'Idempotency-Key'],
    )

    @instance.exception_handler(APIError)
    async def rejected(_request: Request, error: APIError):
        return api_error(error.status, error.code, error.message, error.detail)

    @instance.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        return api_error(400, 'VALIDATION_ERROR', '请检查请求字段')

    return instance


def create_dev_app() -> FastAPI:
    """Build the API from environment variables; used by local runs and tests."""
    return create_app(Settings.from_env())


app = FastAPI(title='绒星 API', version='0.1.0')
app.get('/api/v1/health')(health)
