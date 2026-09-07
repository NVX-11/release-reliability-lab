"""Tests for task behavior and input validation."""

from fastapi.testclient import TestClient


def test_task_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/tasks", json={"title": "  Ship safely  ", "description": "Run checks"}
    )
    assert created.status_code == 201
    assert created.json() == {
        "id": 1,
        "title": "Ship safely",
        "description": "Run checks",
        "completed": False,
    }

    assert client.get("/tasks").json() == [created.json()]
    assert client.get("/tasks/1").json() == created.json()

    updated = client.patch("/tasks/1", json={"completed": True})
    assert updated.status_code == 200
    assert updated.json()["completed"] is True

    deleted = client.delete("/tasks/1")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/tasks").json() == []


def test_missing_task_returns_not_found(client: TestClient) -> None:
    for response in (
        client.get("/tasks/999"),
        client.patch("/tasks/999", json={"completed": True}),
        client.delete("/tasks/999"),
    ):
        assert response.status_code == 404
        assert response.json() == {"detail": "Task not found"}


def test_create_rejects_blank_title(client: TestClient) -> None:
    response = client.post("/tasks", json={"title": "   "})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "title"]


def test_create_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post("/tasks", json={"title": "Valid", "owner": "unknown"})

    assert response.status_code == 422


def test_update_rejects_null_title(client: TestClient) -> None:
    created = client.post("/tasks", json={"title": "Keep a valid title"})

    response = client.patch(f"/tasks/{created.json()['id']}", json={"title": None})

    assert response.status_code == 422


def test_task_ids_are_positive_integers(client: TestClient) -> None:
    assert client.get("/tasks/not-an-integer").status_code == 422
    assert client.get("/tasks/0").status_code == 422
