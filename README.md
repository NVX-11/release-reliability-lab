# Release Reliability Lab

Release Reliability Lab is a small API built to demonstrate the foundations of a
reliable software delivery process. Milestone 1 deliberately keeps the runtime
architecture minimal: a FastAPI service, an in-memory task repository, automated
tests, and a least-privilege CI pipeline.

## Architecture

```text
Client
  |
  v
FastAPI routes (`app/main.py`)
  |
  v
Thread-safe in-memory task store (`app/store.py`)
```

Pydantic request and response models in `app/models.py` define the API contract.
The version reported by the API is centralized in `app/__init__.py`.
Because task state exists only in the process, restarting the service clears all
tasks. This is intentional for this milestone and makes the project's current
operational limitations explicit.

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

A task requires a non-blank `title`; `description` is optional and `completed`
defaults to `false`. FastAPI also exposes interactive API documentation at
`http://127.0.0.1:8000/docs` while the service is running.

## Local development

Python 3.12 is used by CI and is the recommended development version.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Try the service from another terminal:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"Verify the release"}'
```

## Tests

```bash
python -m pytest
```

The test suite checks health and version metadata, the task lifecycle, missing
resources, and request validation. GitHub Actions runs the same command for every
push and pull request.

## Scope and roadmap

Milestone 1 includes only the application and CI foundation. It does **not**
provision cloud infrastructure or include a database, deployment, Kubernetes,
Terraform, or monitoring.

A logical next milestone is to package the service as a reproducible container,
publish a versioned artifact from CI, and add a staged deployment workflow with
smoke checks and an explicit rollback path. Persistent storage and observability
can follow once deployment behavior is proven.
