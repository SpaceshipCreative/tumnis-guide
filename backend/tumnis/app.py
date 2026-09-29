"""FastAPI application factory: routers, MCP mount, middleware, lifespan (P0-04+)."""

from fastapi import FastAPI

from tumnis.core.clock import Clock
from tumnis.settings import Settings


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    raise NotImplementedError
