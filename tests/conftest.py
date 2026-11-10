import pytest
from fastapi.testclient import TestClient

from tagtrail.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "workspace.sqlite"), base_url="http://localhost") as session:
        yield session


@pytest.fixture
def demo(client):
    response = client.post("/api/demo")
    assert response.status_code == 201, response.text
    return response.json()
