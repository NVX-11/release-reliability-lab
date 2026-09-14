import json
from pathlib import Path

import pytest

import staging.availability_observer as observer
from staging.release_controller import normalize_observer_mode


def sample(phase, state, timestamp):
    return {"phase": phase, "state": state, "timestamp": timestamp}


def test_target_is_only_stable_loopback_health_endpoint():
    assert observer.TARGET == "http://127.0.0.1:8080/health"
    source = Path(observer.__file__).read_text()
    assert "active:8000" not in source
    assert "candidate:8000" not in source


def test_observer_mode_rejects_unsupported_values():
    assert normalize_observer_mode("disabled") == "disabled"
    assert normalize_observer_mode("enabled") == "enabled"
    with pytest.raises(ValueError, match="unsupported availability observer mode"):
        normalize_observer_mode("sometimes")


def test_outage_and_later_recovery_are_required_for_success():
    base = [
        sample("baseline", "healthy", "2026-01-01T00:00:00Z"),
        sample("candidate", "healthy", "2026-01-01T00:00:01Z"),
    ]
    assert observer.build_report(base, 1, "abc")["result"] == "failure"
    outage_only = base + [sample("outage", "unavailable", "2026-01-01T00:00:02Z")]
    assert observer.build_report(outage_only, 1, "abc")["result"] == "failure"
    recovered = outage_only + [sample("recovery", "healthy", "2026-01-01T00:00:05Z")]
    report = observer.build_report(recovered, 1, "abc")
    assert report["result"] == "success"
    assert report["approximate_outage_duration_seconds"] == 3
    assert report["final_observer_state"] == "healthy"


def test_report_generation_and_bounded_lifetime(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "observe", lambda _timeout: "healthy")
    monkeypatch.setattr(observer, "timestamp", lambda: "2026-01-01T00:00:00Z")
    phase = tmp_path / "phase"
    phase.write_text("baseline\n")
    observations = tmp_path / "observations.jsonl"
    report_path = tmp_path / "report.json"
    assert observer.run(observations, report_path, phase, 0.01, 0.005, 0.025, "commit") == 0
    report = json.loads(report_path.read_text())
    assert 1 <= report["total_observations"] <= 4
    assert report["monitoring_target"] == observer.TARGET
    assert report["probe_interval_seconds"] == 0.01
    assert report["controller_commit"] == "commit"


def test_interval_timeout_and_lifetime_are_bounded(tmp_path):
    with pytest.raises(ValueError, match="timeout no greater"):
        observer.run(tmp_path / "o", tmp_path / "r", tmp_path / "p", 1, 2, 10, "commit")


def test_workflow_default_summary_and_cleanup_process_management():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/staging.yml").read_text()
    deploy = (root / "staging/deploy.sh").read_text()
    cleanup = (root / "staging/cleanup.sh").read_text()
    observer_input = workflow.split("availability_observer:", 1)[1].split("permissions:", 1)[0]
    assert "default: disabled" in observer_input
    assert "AVAILABILITY_OBSERVER: ${{ inputs.availability_observer }}" in workflow
    for field in (
        "Availability observer", "Baseline availability", "Candidate availability",
        "Controlled outage observed", "Recovery availability", "Observed outage duration",
        "Final observer state", "Availability evidence result",
    ):
        assert f'echo "| {field} |' in deploy
    assert 'kill "$observer_pid"' in deploy
    assert 'wait "$observer_pid"' in deploy
    assert "availability.pid" in cleanup
    assert cleanup.index('kill "$observer_pid"') < cleanup.index("docker compose")


def test_deployment_and_artifact_upload_share_runner_temp_report_path():
    workflow = (Path(__file__).parents[1] / ".github/workflows/staging.yml").read_text()
    deployment_step = workflow.split(
        "- name: Pull, verify, stage, and promote published images", 1
    )[1].split("- name:", 1)[0]
    upload_step = workflow.split("- name: Upload independent availability report", 1)[1].split(
        "- name:", 1
    )[0]
    expected = "${{ runner.temp }}/availability-report.json"
    assert f"AVAILABILITY_REPORT_PATH: {expected}" in deployment_step
    assert f"path: {expected}" in upload_step
    job_env = workflow.split("    env:", 1)[1].split("    steps:", 1)[0]
    assert "AVAILABILITY_REPORT_PATH" not in job_env


def test_missing_baseline_or_candidate_evidence_does_not_gate_controller_progression():
    deploy = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    collector = deploy.split("collect_observation_evidence() {", 1)[1].split("\n}", 1)[0]
    assert 'if ! wait_for_observation "$phase" "$state"; then' in collector
    assert "observer_evidence_failure=true" in collector
    assert "return 0" in collector

    baseline = deploy.index("collect_observation_evidence baseline healthy")
    candidate_start = deploy.index('"${compose[@]}" up --detach --no-build candidate')
    candidate_evidence = deploy.index("collect_observation_evidence candidate healthy")
    fault_start = deploy.index(
        'if [[ "$fault_mode" == "post_promotion_backend_failure" ]]', candidate_evidence
    )
    assert baseline < candidate_start
    assert candidate_evidence < fault_start


def test_missing_outage_evidence_cannot_prevent_rollback():
    deploy = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    fault = deploy.split('if [[ "$fault_mode" == "post_promotion_backend_failure" ]]', 1)[1]
    outage_evidence = fault.index("collect_observation_evidence outage unavailable")
    rollback = fault.index("restore_active || exit 1")
    assert outage_evidence < rollback
    assert "wait_for_observation outage unavailable\n  restore_active" not in fault


def test_recovery_precedes_final_availability_evidence_failure():
    deploy = (Path(__file__).parents[1] / "staging/deploy.sh").read_text()
    fault = deploy.split('if [[ "$fault_mode" == "post_promotion_backend_failure" ]]', 1)[1]
    rollback = fault.index("restore_active || exit 1")
    recovery_evidence = fault.index("collect_observation_evidence recovery healthy", rollback)
    finalize = fault.index("stop_observer", recovery_evidence)
    evidence_failure = fault.index('[[ "$observer_evidence_failure" == false', finalize)
    assert rollback < recovery_evidence < finalize < evidence_failure
