"""Tests for service metadata endpoints."""

from fastapi.testclient import TestClient

from app import __version__


def test_health_reports_healthy(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_version_reports_package_version(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert __version__ == "0.1.1"
    assert response.json() == {"version": "0.1.1"}


def test_info_reports_stable_service_metadata(client: TestClient) -> None:
    response = client.get("/info")

    assert response.status_code == 200
    assert response.json() == {
        "service": "release-reliability-lab",
        "version": "0.1.1",
    }
