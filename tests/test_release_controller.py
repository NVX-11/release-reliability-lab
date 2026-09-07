import io
from pathlib import Path

import pytest

import staging.release_controller as controller
from staging.release_controller import REPOSITORY, SOURCE, normalize_image, render_nginx, verify_metadata

DIGEST = "sha256:" + "a" * 64
REFERENCE = f"{REPOSITORY}@{DIGEST}"
REVISION = "b" * 40


@pytest.mark.parametrize(
    "value",
    [
        f"{REPOSITORY}:latest",
        "ghcr.io/attacker/image@" + DIGEST,
        "sha256:" + "a" * 63,
        "sha256:" + "A" * 64,
        REFERENCE + "; echo unsafe",
        "",
    ],
)
def test_normalize_rejects_mutable_malformed_or_unauthorized_images(value):
    with pytest.raises(ValueError):
        normalize_image(value)


def test_normalize_accepts_only_digest_or_approved_full_reference():
    assert normalize_image(DIGEST) == REFERENCE
    assert normalize_image(REFERENCE) == REFERENCE


def test_metadata_keeps_revision_and_application_version_distinct():
    document = {
        "RepoDigests": [REFERENCE],
        "Config": {
            "Labels": {
                "org.opencontainers.image.source": SOURCE,
                "org.opencontainers.image.revision": "b" * 40,
                "org.opencontainers.image.version": "0.1.0",
            }
        },
    }
    assert verify_metadata(document, REFERENCE) == {
        "source": SOURCE,
        "revision": "b" * 40,
        "version": "0.1.0",
    }


@pytest.mark.parametrize("field", ["source", "revision", "version", "digest"])
def test_metadata_rejects_each_identity_failure(field):
    document = {
        "RepoDigests": [REFERENCE],
        "Config": {
            "Labels": {
                "org.opencontainers.image.source": SOURCE,
                "org.opencontainers.image.revision": "b" * 40,
                "org.opencontainers.image.version": "0.1.0",
            }
        },
    }
    if field == "digest":
        document["RepoDigests"] = []
    else:
        key = f"org.opencontainers.image.{field}"
        document["Config"]["Labels"][key] = "unknown" if field == "version" else "wrong"
    with pytest.raises(ValueError):
        verify_metadata(document, REFERENCE)


def test_proxy_route_has_one_selected_backend_and_loopback_is_compose_owned():
    active = render_nginx("active", REFERENCE, REVISION)
    candidate = render_nginx("candidate", REFERENCE, "c" * 40)
    assert "server active:8000" in active
    assert "candidate:8000" not in active
    assert "server candidate:8000" in candidate
    assert "active:8000" not in candidate
    assert f'add_header X-Release-Digest "{DIGEST}" always' in active
    assert f'add_header X-Release-Revision "{REVISION}" always' in active
    # Resolve from the test module rather than relying on the pytest working directory.
    compose_text = (Path(__file__).parents[1] / "staging/compose.yml").read_text()
    assert '"127.0.0.1:8080:8080"' in compose_text
    assert "application:\n    internal: true" in compose_text
    assert "edge: {}" in compose_text
    assert "networks: [application]\n" in compose_text
    assert "networks: [application, edge]" in compose_text
    assert compose_text.count("ports:") == 1


def test_cleanup_supplies_images_and_does_not_suppress_compose_failure():
    root = Path(__file__).parents[1]
    cleanup_text = (root / "staging/cleanup.sh").read_text()
    workflow_text = (root / ".github/workflows/staging.yml").read_text()
    assert 'export ACTIVE_IMAGE="${ACTIVE_IMAGE:-$cleanup_image}"' in cleanup_text
    assert 'export CANDIDATE_IMAGE="${CANDIDATE_IMAGE:-$cleanup_image}"' in cleanup_text
    assert "--project-name release-reliability-staging" in cleanup_text
    assert "down --volumes --remove-orphans" in cleanup_text
    assert "down --volumes --remove-orphans || true" not in cleanup_text
    assert "bash staging/cleanup.sh" in workflow_text


def test_render_rejects_unknown_backend():
    with pytest.raises(ValueError):
        render_nginx("untrusted", REFERENCE, "b" * 40)


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, body, digest=DIGEST, revision="b" * 40):
        super().__init__(body)
        self.headers = {"X-Release-Digest": digest, "X-Release-Revision": revision}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_http_verification_requires_proxy_release_identity(monkeypatch):
    monkeypatch.setattr(controller, "urlopen", lambda *_args, **_kwargs: FakeResponse(b'{"status":"healthy"}'))
    controller.verify_http("http://stable", "/health", expected_digest=REFERENCE, expected_revision="b" * 40)


def test_http_verification_rejects_wrong_proxy_release_identity(monkeypatch):
    monkeypatch.setattr(
        controller,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b'{"status":"healthy"}', digest="sha256:" + "c" * 64),
    )
    with pytest.raises(ValueError, match="release digest"):
        controller.verify_http("http://stable", "/health", expected_digest=REFERENCE, expected_revision="b" * 40)
