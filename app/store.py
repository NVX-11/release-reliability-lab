"""A small, thread-safe in-memory repository for tasks."""

from threading import Lock

from app.models import Task, TaskCreate, TaskUpdate


class TaskStore:
    """Store tasks for the lifetime of the application process."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, task: TaskCreate) -> Task:
        with self._lock:
            created = Task(id=self._next_id, completed=False, **task.model_dump())
            self._tasks[created.id] = created
            self._next_id += 1
            return created

    def list(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, task_id: int) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: int, changes: TaskUpdate) -> Task | None:
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return None
            updated = current.model_copy(
                update=changes.model_dump(exclude_unset=True)
            )
            self._tasks[task_id] = updated
            return updated

    def delete(self, task_id: int) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def clear(self) -> None:
        """Reset all state. Primarily useful for test isolation."""
        with self._lock:
            self._tasks.clear()
            self._next_id = 1


task_store = TaskStore()
