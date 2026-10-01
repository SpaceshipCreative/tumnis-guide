"""A 405 names every method served at the path, unless a route fixes its own Allow."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tumnis.core.errors import FixedAllow, install_problem_handlers


def _app() -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/things/{thing_id}")
    async def read(thing_id: str) -> dict[str, str]:
        if thing_id == "literal":
            raise FixedAllow("POST")
        return {"id": thing_id}

    @app.put("/things/{thing_id}")
    async def write(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    return app


def test_framework_405_names_methods_of_every_route_at_the_path() -> None:
    response = TestClient(_app()).delete("/things/one")
    assert response.status_code == 405
    assert set(response.headers["allow"].split(", ")) >= {"GET", "PUT"}


def test_fixed_allow_is_kept_as_the_route_set_it() -> None:
    response = TestClient(_app()).get("/things/literal")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
