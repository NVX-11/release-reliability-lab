# Release Reliability Lab

Release Reliability Lab is a small FastAPI service for demonstrating reliable
software delivery practices. Milestone 3 extends the tested container pipeline
with publication of the exact verified image to GitHub Container Registry
(GHCR). It does not deploy the image.

## Architecture

```text
Client -> port 8000 -> Uvicorn/FastAPI routes (`app/main.py`)
                              |
                              v
                 Process-local task store (`app/store.py`)
```

The production image uses the slim Debian Python 3.12 image, installs only the
locked runtime dependency set, copies only the `app` package, and runs Uvicorn
as an unprivileged `app` user. Build tools, tests, development dependencies,
virtual environments, caches, Git metadata, and local configuration are
excluded from the image. No credentials or secrets are required or copied.

Pydantic models in `app/models.py` define the API contract. The application
version reported by `/version` remains centralized in `app/__init__.py`.

> **Current data limitation:** tasks are stored only in the application process.
> Stopping or restarting the process or container permanently loses all tasks.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Report service health |
| `GET` | `/version` | Report the application version |
| `POST` | `/tasks` | Create a task |
| `GET` | `/tasks` | List tasks |
| `GET` | `/tasks/{task_id}` | Fetch a task |
| `PATCH` | `/tasks/{task_id}` | Update a task |
| `DELETE` | `/tasks/{task_id}` | Delete a task |

Interactive API documentation is available at `http://127.0.0.1:8000/docs`
while the service is running.

## Build and run the container

From the repository root:

```bash
docker build --tag release-reliability-lab:local .
docker run --rm --name release-reliability-lab \
  --publish 127.0.0.1:8000:8000 release-reliability-lab:local
```

Binding to `127.0.0.1` keeps this development instance accessible only from the
host. In another terminal, verify it:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/version
```

Local builds are not published.

## Reproducibility and dependency updates

The `Dockerfile` pins the multi-platform `python:3.12-slim-bookworm` base image
by immutable manifest-list SHA-256 digest. This prevents an upstream tag move
from silently changing a build. To update Python or Debian security fixes,
select the intended supported Python 3.12 slim tag, obtain its current manifest
digest from the official Python image, update both the readable tag and digest
in `Dockerfile`, then rebuild and run all checks.

Runtime dependencies are fully resolved with hashes in `requirements.lock`.
The final image installs that file with pip's `--require-hashes`; development
extras from `pyproject.toml` are not installed. After intentionally changing a
runtime constraint in `pyproject.toml`, regenerate the lock on Python 3.12 with:

```bash
python3.12 -m venv .lock-venv
.lock-venv/bin/python -m pip install 'pip==24.3.1' 'pip-tools==7.4.1'
.lock-venv/bin/pip-compile --generate-hashes --strip-extras \
  --output-file=requirements.lock pyproject.toml
rm -rf .lock-venv
```

Review the lock diff and then build and test the image. Pinning the lock tooling
in the update command makes lock regeneration repeatable; the generated hashes
make package downloads verifiable.

## Local development and tests

Python 3.12 is the supported development version.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest
uvicorn app.main:app --reload
```

The pytest suite covers service metadata, the task lifecycle, missing resources,
and input validation.

## CI container verification

GitHub Actions retains the original Python test job and adds an independent
container job. The container job builds the Dockerfile, starts that exact image,
and polls `/health` up to 30 times with per-request timeouts rather than relying
on a fixed startup delay. Readiness requires both HTTP 200 and the exact JSON
health payload. It then requires HTTP 200 and the exact `/version` JSON payload,
including the version imported from the application's version source. Any build,
startup, HTTP, status, content, or version mismatch fails the job. A shell trap
prints container logs on failure and always forcibly removes the test container.

## Verified GHCR publication

Pull requests and pushes run both the Python tests and the live-container smoke
checks. After a successful smoke test, CI saves that image, its Docker image ID,
and an archive checksum as a GitHub Actions workflow artifact retained for one
day. The publication job downloads the artifact, checks the archive checksum,
loads it, and requires its image ID to match before applying a registry tag.
Publication therefore does not rebuild or substitute the tested image. Cleanup
steps run even when verification or publication fails.

Publication is restricted to successful pushes to `main` and depends on both
verification jobs. Pull requests never authenticate to GHCR or publish. The
publishing job alone receives `packages: write`; all jobs have only
`contents: read` otherwise. It authenticates with the repository-provided
`GITHUB_TOKEN`, not a personal token.

The package name is:

```text
ghcr.io/nvx-11/release-reliability-lab
```

Every successful main-branch publication receives a unique full-commit tag:

```text
ghcr.io/nvx-11/release-reliability-lab:sha-<full-40-character-commit-SHA>
```

CI records the digest returned by GHCR, validates that it is a SHA-256 digest,
and writes both the tag and immutable digest reference to the workflow job
summary. For a published digest, pull and run the immutable artifact with:

```bash
docker pull ghcr.io/nvx-11/release-reliability-lab@sha256:<64-hex-character-digest>
docker run --rm --publish 127.0.0.1:8000:8000 \
  ghcr.io/nvx-11/release-reliability-lab@sha256:<64-hex-character-digest>
```

Find publications on the repository's **Packages** page. Open the
`release-reliability-lab` package and locate the `sha-<commit>` version, or open
the successful main-branch Actions run and read **Published verified
container** in the publish job summary. The full commit in the tag and the OCI
`org.opencontainers.image.revision` label identify the source revision. The
image also carries `org.opencontainers.image.source` and
`org.opencontainers.image.version` labels.

These identifiers serve different purposes:

* The **application version** (currently `0.1.0`) describes the software API and
  is reported by `/version`; it is stored independently in `app/__init__.py`.
* The **image tag** `sha-<full commit SHA>` is a convenient, traceable registry
  name. Tags can technically be moved, so it is not an immutable deployment
  identity.
* The **registry digest** `sha256:<digest>` is content-addressed and immutable.
  Future deployment automation should consume the `ghcr.io/...@sha256:...`
  reference recorded by the workflow, rather than relying on a tag.

The first main-branch publication may require a repository or organization
administrator to allow GitHub Actions to create or write packages. If GHCR
returns `permission_denied`, enable **Settings → Actions → General → Workflow
permissions → Read and write permissions**, and ensure the package's **Manage
Actions access** grants this repository write access. The workflow stops on
that error and does not change repository, organization, or package visibility.

## Implemented scope and roadmap

**Implemented functionality:** the FastAPI API, process-local task storage,
automated Python tests, a least-privilege CI workflow, production-oriented
container packaging, and a GitHub Actions job that performs live-container HTTP
smoke checks. Successful pushes to `main` publish that exact verified image to
GHCR and record its immutable digest.

**Not implemented:** deployment, rollback, persistent storage, observability,
cloud infrastructure, Kubernetes, Terraform, or public endpoints. GHCR stores
an artifact; it does not run or expose the application.

The next milestone is deployment of an explicitly selected immutable digest.
Rollback remains a separate later milestone; persistence and observability can
follow afterward.
