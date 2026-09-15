# Release Reliability Lab

A passing build does not tell you whether a release will serve traffic correctly—or whether you can recover when it stops.

This lab follows a small FastAPI service from tested container to immutable release, validates it behind a stable Nginx endpoint, and exercises automatic rollback after a controlled failure. The task API gives the release process something concrete to run and verify.

**Release flow:** test and smoke-check → publish the exact tested image → stage by digest → validate candidate → promote → inject a controlled fault → verify rollback.

<!-- IMAGE SLOT: Final architecture diagram
Target path: docs/images/release-reliability-architecture.png
When the image is ready, replace this comment with:
<p align="center">
  <img src="docs/images/release-reliability-architecture.png" alt="Release Reliability Lab architecture" width="900">
</p>
-->

## What this proves

- The image published to GHCR is the image that passed the container smoke test; publication does not rebuild it.
- A candidate is checked before the stable route changes. A deliberately mismatched version expectation is rejected while the baseline identity remains unchanged.
- A distinct release was promoted from **0.1.0 to 0.1.1**, then a controlled failure exercise restored the previously validated release and checked health, version, digest, and source revision.
- An independent observer saw **healthy baseline → healthy promoted candidate → outage → healthy recovery**, with an observed outage of **approximately 18 seconds** in the final experiment.

The [proof runs below](#evidence) document these outcomes. Staging runs only on a temporary GitHub-hosted runner and is removed at the end of the workflow.

## Evidence

| Proof | Result | Run |
| --- | --- | --- |
| Verified promotion | Baseline 0.1.0 replaced by candidate 0.1.1 through the stable route | [Promotion run](https://github.com/NVX-11/release-reliability-lab/actions/runs/34762422874) |
| Controlled failure and rollback | Post-promotion failure detected; previous release restored and verified | [Rollback run](https://github.com/NVX-11/release-reliability-lab/actions/runs/34851202507) |
| Independent availability evidence | Healthy baseline and promoted service, observed outage, then healthy recovery; approximately 18 seconds of observed outage | [Availability run](https://github.com/NVX-11/release-reliability-lab/actions/runs/34856368502) |

Open each run's summary for release identities, validation outcomes, and cleanup results. Fault runs upload `controlled-fault-incident-<run_id>`; observer-enabled runs also upload `availability-evidence-<run_id>`. Both report artifacts have a 14-day retention period.

The outage figure belongs to this experiment. It is a sampled observation, not a recovery-time guarantee or a production availability measurement.

### Proof snapshots

<!-- IMAGE SLOT: Verified promotion — 0.1.0 → 0.1.1
Target path: docs/images/promotion-proof.png
When the image is ready, replace this comment with:
<p align="center">
  <img src="docs/images/promotion-proof.png" alt="Verified release promotion from 0.1.0 to 0.1.1" width="900">
</p>
-->

<!-- IMAGE SLOT: Controlled failure and verified rollback
Target path: docs/images/rollback-proof.png
When the image is ready, replace this comment with:
<p align="center">
  <img src="docs/images/rollback-proof.png" alt="Controlled failure and verified rollback to the previous release" width="900">
</p>
-->

<!-- IMAGE SLOT: Independent outage and recovery evidence
Target path: docs/images/availability-proof.png
When the image is ready, replace this comment with:
<p align="center">
  <img src="docs/images/availability-proof.png" alt="Independent availability observer showing healthy, outage, and recovery states" width="900">
</p>
-->

## Architecture

### CI and artifact flow

Source → Python tests and container smoke verification → exact tested image → GHCR immutable digest.

The Python test job and container job run independently on pushes and pull requests. Publication requires both to pass and runs only for pushes to `main`.

The container job builds once, starts that image, and verifies the live `/health` and `/version` responses. It exports the verified image with its Docker image ID and an archive checksum. The publication job checks the checksum, loads the archive, and checks the image ID before tagging and pushing it. No rebuild occurs between verification and publication.

GHCR returns the digest used by staging:

```text
ghcr.io/nvx-11/release-reliability-lab@sha256:<64-lowercase-hex-characters>
```

The full-commit tag `sha-<40-character-commit-SHA>` helps locate a publication. The digest identifies the immutable release. OCI labels record the source repository, source revision, and application version.

### Staging and reliability flow

Immutable baseline + candidate → active/candidate containers → stable Nginx route → promotion → controlled fault → verified rollback.

| Component | Responsibility |
| --- | --- |
| Release controller: `staging/deploy.sh` and `staging/release_controller.py` | Validate image identity, check health and version, manage promotion and rollback, and verify recovery |
| `active` container | Run the validated baseline and remain available as the rollback target |
| `candidate` container | Run the proposed release for validation and serve the stable route after promotion |
| Nginx `proxy` | Expose `127.0.0.1:8080`, route to one selected backend, and supply validated release identity headers |
| Independent availability observer | Sample only `http://127.0.0.1:8080/health` and report the observed availability sequence |

Both application containers use the internal `application` network and publish no host ports. Nginx joins `application` and `edge`; its only host binding is `127.0.0.1:8080`. Candidate checks run from the proxy container over the internal network.

Promotion changes Nginx's selected backend from `active:8000` to `candidate:8000`. The Compose service named `active` continues running the baseline; promotion does not rename containers. Rollback routes traffic back to that retained baseline.

The observer runs as a separate process on the same runner, outside the application containers. It watches the stable entrypoint only. It does not probe `active` or `candidate` directly, inspect image metadata, or decide which release should serve traffic.

### Release identity

A matching application version alone cannot distinguish two images built from different commits. Before starting either release, the controller checks that the requested immutable reference appears in local `RepoDigests` and validates the OCI source, full source revision, and version labels.

Nginx renders `X-Release-Digest` and `X-Release-Revision` from that validated metadata and suppresses backend-supplied headers with those names. Stable-route verification requires HTTP 200, the expected health/version JSON, and the expected digest and revision headers. Headers alone are not sufficient for success.

The workflow also records the **controller commit** separately from each **application image's source revision**. Updating the deployment logic does not imply rebuilding the application releases under test.

## How the reliability exercise works

1. **Validate and pull the releases.** Accept only complete SHA-256 digests or immutable references for this repository. Pull the baseline and candidate, then validate their local identities and OCI labels.
2. **Establish the baseline.** Start `active` and Nginx. Verify health, version, digest, and source revision through the stable endpoint with bounded retries.
3. **Validate the candidate.** Start `candidate` without changing the stable route. Check its health and version over the internal network. An intentionally impossible version expectation must fail, after which the baseline's stable-route identity is checked again.
4. **Promote.** Render the candidate route, validate the complete Nginx configuration, atomically replace the configuration file, and reload Nginx. Verify all four checks through the stable endpoint. The baseline stays running.
5. **Inject the optional fault.** After successful promotion, install a valid Nginx configuration pointing to the unreachable `candidate:65535` port. This creates a backend routing failure without modifying the image or stopping the application container.
6. **Detect and recover.** The controller's bounded stable-route verification must detect the failure. It then renders, validates, and reloads the previous route. Rollback succeeds only when the baseline's health, version, digest, and source revision all pass again.
7. **Record the outcome and clean up.** Write the workflow summary and applicable JSON reports, stop the observer, and remove the staging resources.

Candidate validation failure leaves the active route unchanged. If applying or verifying promotion fails, the controller attempts to restore and verify the previous route and reports the deployment as failed. In the controlled-fault exercise, success requires both the expected failure and verified recovery; an outage that never occurs or a recovery that cannot be verified fails the exercise.

Cleanup is wired into the deployment script's exit trap and a separate `if: always()` workflow step. It removes Compose containers, networks, volumes, generated configuration and inspection files, and the temporary GHCR login. Cleanup failures are surfaced. The staging job has a 15-minute timeout and leaves no hosted service behind.

## Availability evidence and incident reports

The controller and observer answer different questions:

| Evidence source | Question answered |
| --- | --- |
| Controller incident report | What failed, what rollback action was taken, and was the expected previous release restored? |
| Independent availability report | Did requests through the stable endpoint actually observe health, an outage, and later recovery? |

The dependency-free observer sends its own request every second with a 0.5-second timeout. HTTP 200 with the exact `{"status":"healthy"}` payload is classified as `healthy`; unsuccessful responses, invalid payloads, and request failures are classified as `unavailable`.

The controller supplies experiment phase labels, but each availability result comes from the observer's own HTTP request. The report includes phase timestamps, observation count, controller commit, final state, and an approximate outage duration from the first unavailable observation in the outage phase to the first healthy observation in the recovery phase. Sampling and timestamp resolution limit its precision.

The observer supplies evidence only. Missing observations do not veto promotion or prevent rollback. With the observer enabled for the fault exercise, incomplete evidence can fail the overall experiment after the controller has attempted recovery. The observer has a ten-minute maximum lifetime and is explicitly stopped and waited for before teardown.

The incident report records the previous and candidate release identities, injected failure, controller detection result, rollback action and result, recovery verification, final stable identity, and total elapsed time. Its elapsed time is separate from the observer's outage measurement.

## Run the staging exercise

Use **Actions → Ephemeral staging deployment → Run workflow** from reviewed `main`.

| Input | Value |
| --- | --- |
| `candidate_image` | A published image's complete digest or full immutable GHCR reference |
| `baseline_image` | The supplied known baseline, or another known-good immutable release; every run validates it again |
| `fault_mode` | `none` for promotion only; `post_promotion_backend_failure` for the rollback exercise |
| `availability_observer` | `disabled` by default; use `enabled` with the fault mode to collect the full availability sequence |

Find image digests in a successful main-branch CI run's **Published verified container** summary or the repository's GHCR package. Tags, other repositories, partial digests, and uppercase digests are rejected.

Use distinct published releases to reproduce a version transition. Selecting the same image for both inputs exercises the mechanism but does not prove a change between releases. Enabling the observer without the fault mode produces an incomplete outage/recovery sequence without changing the promotion outcome.

Staging pulls existing images; it does not build, retag, or publish them. It runs only through manual dispatch, with no deployment trigger on pushes or pull requests.

## CI, packaging, and permissions

The application image uses digest-pinned `python:3.12-slim-bookworm`, installs the hash-locked runtime dependencies from `requirements.lock`, copies the `app` package, and runs Uvicorn as an unprivileged user. Tests and development extras are excluded from the runtime image. The staging Nginx image is also pinned by digest.

The container smoke test polls readiness with per-request timeouts, checks exact health and version payloads, and verifies revision/version labels. Failed checks print container logs; cleanup runs on success and failure. The verified-image transfer artifact is retained for one day.

| Workflow job | Explicit token permissions | Publication access |
| --- | --- | --- |
| Python tests | `contents: read` | None |
| Container verification | `contents: read` | None |
| Main-branch publication | `contents: read`, `packages: write` | Push the verified image to GHCR |
| Manual staging | `contents: read`, `packages: read` | Pull published images |

Registry authentication uses the repository-provided `GITHUB_TOKEN`. Pull-request CI does not authenticate to GHCR or publish releases.

## Local development

Python 3.12 is the supported development version. From the repository root, using a POSIX shell:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest
uvicorn app.main:app --reload
```

Tests cover service metadata, the task lifecycle, input validation, release-controller validation, and availability-report behavior. The manual staging workflow supplies the live Compose/Nginx integration exercise.

To run the application container locally:

```bash
docker build --tag release-reliability-lab:local .
docker run --rm --name release-reliability-lab \
  --publish 127.0.0.1:8000:8000 release-reliability-lab:local
```

In another terminal:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/version
curl --fail http://127.0.0.1:8000/info
```

Interactive API documentation is at `http://127.0.0.1:8000/docs`. This local command runs the application only; the release exercise uses the separate staging workflow and port 8080.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service health |
| `GET` | `/version` | Application version |
| `GET` | `/info` | Service name and version |
| `POST` | `/tasks` | Create a task |
| `GET` | `/tasks` | List tasks |
| `GET` | `/tasks/{task_id}` | Fetch a task |
| `PATCH` | `/tasks/{task_id}` | Update a task |
| `DELETE` | `/tasks/{task_id}` | Delete a task |

The application version is defined in `app/__init__.py`; Pydantic models in `app/models.py` define the task contract.

### Dependency updates

Runtime dependencies are fully resolved with hashes. After changing runtime constraints in `pyproject.toml`, regenerate the lock on Python 3.12:

```bash
python3.12 -m venv .lock-venv
.lock-venv/bin/python -m pip install 'pip==24.3.1' 'pip-tools==7.4.1'
.lock-venv/bin/pip-compile --generate-hashes --strip-extras \
  --output-file=requirements.lock pyproject.toml
rm -rf .lock-venv
```

Review the lock diff, rebuild the image, and run the checks. Base-image updates require an intentional tag/digest change in `Dockerfile`. The hash lock covers runtime dependencies; the development test environment is installed separately from `.[dev]`.

## Scope and limitations

- **Temporary environment.** Staging exists only for one GitHub-hosted runner job. There is no permanent hosting or public endpoint.
- **Controlled failure coverage.** The proof exercises an unreachable backend route after promotion. It does not establish recovery from every application, network, or host failure.
- **Bounded availability evidence.** The observer measures one loopback health endpoint during the experiment. It is independent of the controller's health classification, but shares the runner and is not an off-host probe or a production observability system.
- **No continuous operations stack.** There is no Prometheus/Grafana monitoring stack, alerting, on-call integration, or ongoing recovery service after the workflow ends.
- **Process-local data.** Active and candidate have separate in-memory task stores. Routing changes do not transfer tasks; restarting or removing a container loses its data. Release rollback does not prove data recovery or migration safety.
- **No cloud infrastructure layer.** The repository contains no Kubernetes, Terraform, or provisioned cloud infrastructure. It uses GitHub Actions, GHCR, Docker Compose, and Nginx for the experiment.

## Code map

| Path | Contents |
| --- | --- |
| `app/` | FastAPI service, version, request models, and in-memory task store |
| `tests/` | Application, release-controller, and observer tests |
| `Dockerfile`, `requirements.lock` | Runtime packaging and locked dependencies |
| `.github/workflows/ci.yml` | Python tests, container verification, and exact-image publication |
| `.github/workflows/staging.yml` | Manual staging inputs, report uploads, and cleanup |
| `staging/compose.yml` | Active/candidate containers, Nginx, and network boundaries |
| `staging/deploy.sh` | Release orchestration, fault exercise, rollback, and incident reporting |
| `staging/release_controller.py` | Input normalization, metadata checks, Nginx rendering, and HTTP identity verification |
| `staging/availability_observer.py` | Independent stable-endpoint availability sampling and report generation |
| `staging/cleanup.sh` | Independent staging cleanup |