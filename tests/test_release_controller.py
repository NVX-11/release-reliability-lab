import io
from pathlib import Path

import pytest

import staging.release_controller as controller
from staging.release_controller import (
    REPOSITORY,
    SOURCE,
    normalize_fault_mode,
    normalize_image,
    render_nginx,
    verify_metadata,
)

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


def test_topology_checks_do_not_make_unretried_http_requests():
    deploy_text = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    topology_body = deploy_text.split("verify_topology() {", 1)[1].split("\n}\n", 1)[0]
    assert "docker inspect" in topology_body
    assert "PortBindings" in topology_body
    assert "wget" not in topology_body
    assert "http://" not in topology_body

    # Active reachability is retried by verify_stable; candidate reachability is
    # retried by its bounded 30-attempt loop after structural verification.
    active_startup = deploy_text.split('verify_topology false', 1)[1]
    assert 'verify_stable "$active_version"' in active_startup
    candidate_startup = deploy_text.split('verify_topology true', 1)[1]
    assert "for _ in {1..30}; do" in candidate_startup
    assert "http://candidate:8000/health" in candidate_startup


def test_render_rejects_unknown_backend():
    with pytest.raises(ValueError):
        render_nginx("untrusted", REFERENCE, "b" * 40)


def test_fault_mode_none_is_explicit_and_unsupported_modes_are_rejected():
    assert normalize_fault_mode("none") == "none"
    assert normalize_fault_mode("post_promotion_backend_failure") == "post_promotion_backend_failure"
    with pytest.raises(ValueError, match="unsupported fault mode"):
        normalize_fault_mode("post-promotion-typo")


def test_fault_route_is_valid_shape_but_uses_an_unreachable_candidate_port():
    rendered = render_nginx("post_promotion_backend_failure", REFERENCE, REVISION)
    assert "server candidate:65535;" in rendered
    assert "server candidate:8000;" not in rendered
    assert f'add_header X-Release-Digest "{DIGEST}" always' in rendered
    assert f'add_header X-Release-Revision "{REVISION}" always' in rendered


def test_fault_is_opt_in_and_occurs_only_after_verified_promotion():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/staging.yml").read_text()
    deploy = (root / "staging/deploy.sh").read_text()
    assert "default: none" in workflow
    assert "FAULT_MODE: ${{ inputs.fault_mode }}" in workflow
    assert 'fault_mode=$("${controller[@]}" fault-mode "${FAULT_MODE:-none}")' in deploy
    promotion_verified = deploy.index('promotion="successful: candidate identity verified through stable route"')
    fault_branch = deploy.index('if [[ "$fault_mode" == "post_promotion_backend_failure" ]]', promotion_verified)
    fault_render = deploy.index("render post_promotion_backend_failure", fault_branch)
    assert promotion_verified < fault_branch < fault_render


def test_fault_success_requires_detection_and_verified_previous_identity():
    deploy = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    fault_body = deploy.split('if [[ "$fault_mode" == "post_promotion_backend_failure" ]]', 1)[1]
    assert 'if verify_stable "$candidate_version" "$candidate_ref" "$candidate_revision"' in fault_body
    assert "restore_active || exit 1" in fault_body
    restore_body = deploy.split("restore_active() {", 1)[1].split("\n}", 1)[0]
    assert 'verify_stable "$active_version" "$active_ref" "$active_revision"' in restore_body
    assert 'rollback_result="successful"' in restore_body
    assert 'recovery_verification="passed health, version, immutable digest, and revision"' in restore_body
    assert "return 1" in restore_body


def test_summary_and_incident_report_contain_required_recovery_evidence():
    deploy = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    assert 'echo "| Restored stable release identity | \\`$restored_identity\\` |"' in deploy
    assert 'echo "| Restored stable release identity | `$restored_identity` |"' not in deploy
    for summary_field in (
        "Fault mode",
        "Fault injection result",
        "Failure detection result",
        "Rollback attempted",
        "Rollback result",
        "Restored stable release identity",
        "Recovery verification result",
        "Cleanup",
    ):
        assert f'echo "| {summary_field} |' in deploy
    for report_field in (
        '"incident_type"',
        '"previous_release_identity"',
        '"candidate_release_identity"',
        '"injected_failure"',
        '"detection_mechanism"',
        '"observed_impact"',
        '"rollback_action"',
        '"recovery_verification"',
        '"final_stable_identity"',
        '"outcome"',
        '"timing"',
    ):
        assert report_field in deploy


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
