"""FastAPI application entry point"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.config.logging import setup_logging
from app.db import db
from app.api.v1.router import api_router, health_router
from app.utils.exceptions import (
    GatewayIAMError,
    OperationNotFoundError,
    InvalidOperationStateError,
)

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown"""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    await db.connect()
    logger.info("Database connected")

    yield

    # Shutdown
    logger.info("Shutting down...")
    await db.disconnect()
    logger.info("Database disconnected")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="API de provisionnement IAM pour MidPoint",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(OperationNotFoundError)
async def operation_not_found_handler(request: Request, exc: OperationNotFoundError):
    """Handle operation not found errors"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "message": exc.message,
            "details": exc.details,
        },
    )


@app.exception_handler(InvalidOperationStateError)
async def invalid_state_handler(request: Request, exc: InvalidOperationStateError):
    """Handle invalid operation state errors"""
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_state",
            "message": exc.message,
            "details": exc.details,
        },
    )


@app.exception_handler(GatewayIAMError)
async def gateway_iam_error_handler(request: Request, exc: GatewayIAMError):
    """Handle generic Gateway IAM errors"""
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": exc.message,
            "details": exc.details,
        },
    )


# Include routers
app.include_router(health_router)  # /health, /metrics
app.include_router(api_router)  # /api/v1/*

# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint redirecting to docs"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
        "connectors_dashboard": "/dashboard/connectors",
        "approvers_dashboard": "/dashboard/approvers",
    }


@app.get("/dashboard/connectors", include_in_schema=False)
async def connectors_dashboard():
    """Serve the connectors dashboard"""
    html_path = Path(__file__).parent / "static" / "connectors.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/dashboard/approvers", include_in_schema=False)
async def approvers_dashboard():
    """Serve the approvers dashboard"""
    html_path = Path(__file__).parent / "static" / "approvers.html"
    return FileResponse(html_path, media_type="text/html")
