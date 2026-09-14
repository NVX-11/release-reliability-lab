#!/usr/bin/env python3
"""Bounded, evidence-only observer for the staging stable endpoint."""

from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

TARGET = "http://127.0.0.1:8080/health"
PHASES = ("baseline", "candidate", "outage", "recovery")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def observe(timeout: float) -> str:
    """Classify one independent request; no controller identity is consulted."""
    try:
        with urlopen(TARGET, timeout=timeout) as response:
            healthy = response.status == 200 and json.load(response) == {"status": "healthy"}
        return "healthy" if healthy else "unavailable"
    except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError):
        return "unavailable"


def current_phase(path: Path) -> str:
    try:
        phase = path.read_text().strip()
    except OSError:
        return "baseline"
    return phase if phase in PHASES else "baseline"


def build_report(observations: list[dict[str, str]], interval: float, commit: str) -> dict[str, object]:
    def first(phase: str, state: str) -> dict[str, str] | None:
        return next((item for item in observations if item["phase"] == phase and item["state"] == state), None)

    baseline = first("baseline", "healthy")
    candidate = first("candidate", "healthy")
    outage = first("outage", "unavailable")
    recovery = first("recovery", "healthy")
    ordered = bool(
        baseline and candidate and outage and recovery
        and observations.index(baseline) < observations.index(candidate) < observations.index(outage) < observations.index(recovery)
    )
    duration = None
    if outage and recovery:
        start = datetime.fromisoformat(outage["timestamp"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(recovery["timestamp"].replace("Z", "+00:00"))
        duration = round((end - start).total_seconds())
    return {
        "experiment_type": "independent_external_outage_recovery_evidence",
        "controller_commit": commit,
        "monitoring_target": TARGET,
        "probe_interval_seconds": interval,
        "total_observations": len(observations),
        "baseline_healthy_observed": baseline is not None,
        "candidate_healthy_observed": candidate is not None,
        "outage_observed": outage is not None,
        "recovery_observed": recovery is not None,
        "first_baseline_healthy_timestamp": baseline["timestamp"] if baseline else None,
        "first_post_promotion_healthy_timestamp": candidate["timestamp"] if candidate else None,
        "first_outage_timestamp": outage["timestamp"] if outage else None,
        "first_recovery_timestamp": recovery["timestamp"] if recovery else None,
        "approximate_outage_duration_seconds": duration,
        "outage_duration_precision": f"approximately +/- {interval:g} second probe interval",
        "final_observer_state": observations[-1]["state"] if observations else "unavailable",
        "result": "success" if ordered and observations[-1]["state"] == "healthy" else "failure",
    }


def run(output: Path, report: Path, phase_file: Path, interval: float, timeout: float, max_duration: float, commit: str) -> int:
    if interval <= 0 or timeout <= 0 or max_duration <= 0 or timeout > interval:
        raise ValueError("interval, timeout, and lifetime must be positive, with timeout no greater than interval")
    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    observations: list[dict[str, str]] = []
    started = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as stream:
        while not stop and time.monotonic() - started < max_duration:
            cycle = time.monotonic()
            item = {"timestamp": timestamp(), "phase": current_phase(phase_file), "state": observe(timeout)}
            observations.append(item)
            stream.write(json.dumps(item, separators=(",", ":")) + "\n")
            stream.flush()
            remaining = interval - (time.monotonic() - cycle)
            if remaining > 0:
                time.sleep(remaining)
    report.write_text(json.dumps(build_report(observations, interval, commit), indent=2) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--phase-file", type=Path, required=True)
    parser.add_argument("--controller-commit", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--max-duration", type=float, default=600.0)
    args = parser.parse_args()
    try:
        return run(args.observations, args.report, args.phase_file, args.interval, args.timeout, args.max_duration, args.controller_commit)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
