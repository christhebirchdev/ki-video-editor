# tests/test_projects_api.py
import pytest
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_create_project():
    response = client.post("/api/projects/", json={"name": "Test Projekt", "platform": "tiktok"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Projekt"
    assert data["platform"] == "tiktok"
    assert "id" in data
    shutil.rmtree(Path("projects") / data["id"], ignore_errors=True)


def test_get_nonexistent_project():
    response = client.get("/api/projects/doesnotexist")
    assert response.status_code == 404


def test_create_and_get_project():
    create_response = client.post("/api/projects/", json={"name": "Mein Video", "platform": "reels"})
    project_id = create_response.json()["id"]
    get_response = client.get(f"/api/projects/{project_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Mein Video"
    shutil.rmtree(Path("projects") / project_id, ignore_errors=True)
