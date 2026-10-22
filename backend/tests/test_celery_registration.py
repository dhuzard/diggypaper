"""Every module declaring Celery tasks is loaded by the worker.

The worker only imports ``celery_app.conf.include``; a task module missing there is accepted by
the API (``.delay`` succeeds) and then dropped by the worker as "unregistered task". Eager mode
(tests, local runs) never notices.
"""

from pathlib import Path

from app.celery_app import celery_app

TASKS_DIR = Path(__file__).resolve().parents[1] / "app" / "tasks"


def test_every_task_module_is_included_in_the_worker():
    declaring = {
        "app.tasks" if path.stem == "__init__" else f"app.tasks.{path.stem}"
        for path in TASKS_DIR.glob("*.py")
        if "@celery_app.task" in path.read_text(encoding="utf-8")
    }
    assert "app.tasks.nts" in declaring
    assert declaring <= set(celery_app.conf.include)
