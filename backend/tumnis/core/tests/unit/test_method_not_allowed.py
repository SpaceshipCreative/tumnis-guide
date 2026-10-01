"""A 405 names every method served at the path (RFC 9110, 15.5.6), Starlette's own 405
included, and leaves out routes that decline the path."""

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from tumnis.core.errors import install_problem_handlers


class _Route(APIRoute):
    def declines(self, path_params: dict[str, Any]) -> bool:
        return self.path.endswith("/{thing_id}") and path_params.get("thing_id") == "literal"


def _app() -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app)
    router = APIRouter(route_class=_Route)

    @router.get("/things/{thing_id}")
    async def read(thing_id: str) -> dict[str, str]:
        if thing_id == "literal":
            raise HTTPException(405)
        return {"id": thing_id}

    @router.put("/things/{thing_id}")
    async def write(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @router.post("/things/literal")
    async def make() -> dict[str, str]:
        return {"id": "new"}

    app.include_router(router)
    return app


def _allow(response: Any) -> set[str]:
    assert response.status_code == 405
    return set(response.headers["allow"].split(", "))


def test_framework_405_names_methods_of_every_route_at_the_path() -> None:
    assert _allow(TestClient(_app()).delete("/things/one")) == {"GET", "PUT"}


def test_a_declined_path_names_only_the_routes_that_serve_it() -> None:
    client = TestClient(_app())
    assert _allow(client.get("/things/literal")) == {"POST"}
    assert _allow(client.delete("/things/literal")) == {"POST"}
