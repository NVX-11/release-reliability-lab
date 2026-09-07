"""HTTP API for Release Reliability Lab."""

from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Response, status

from app import __version__
from app.models import Task, TaskCreate, TaskUpdate
from app.store import task_store

app = FastAPI(title="Release Reliability Lab", version=__version__)
TaskId = Annotated[int, Path(gt=0)]


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/version", tags=["service"])
def version() -> dict[str, str]:
    return {"version": __version__}


@app.post("/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate) -> Task:
    return task_store.create(task)


@app.get("/tasks", response_model=list[Task])
def list_tasks() -> list[Task]:
    return task_store.list()


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: TaskId) -> Task:
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: TaskId, changes: TaskUpdate) -> Task:
    task = task_store.update(task_id, changes)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: TaskId) -> Response:
    if not task_store.delete(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
