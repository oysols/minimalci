import importlib.util
import inspect
from pathlib import Path
import time
from typing import List, Type, Any
import threading
import datetime
import functools
import builtins
import sys

from .tasks import Task, State, Status


def print_with_thread_prefix(global_log_write_lock: threading.Lock, logdir: Path, *args: Any, **kwargs: Any) -> None:
    # Swallows kwargs to keep signature compatible with builtins.print
    # TODO: use json-based logging instead
    prefix = threading.current_thread().name.split("-")[0]
    timestamp = datetime.datetime.utcnow().isoformat()
    print_string = " ".join([str(arg) for arg in args])
    line = f"{timestamp} {prefix:<20} {print_string}\n"
    sys.stdout.write(line)
    with global_log_write_lock:
        with open(logdir / "output.log", "a") as f:
            f.write(line)


def monkey_patch_print(logdir: Path) -> None:
    global_log_write_lock = threading.Lock()
    builtins.print = functools.partial(print_with_thread_prefix, global_log_write_lock, logdir)


def get_tasks_from_module(obj: object) -> List[Type[Task]]:
    tasks = [
        task for name, task in inspect.getmembers(obj)
        if inspect.isclass(task) and issubclass(task, Task) and task != Task
    ]
    # Sort tasks according to line number
    sorted_tasks = sorted(tasks, key=lambda task: task.run.__code__.co_firstlineno)
    return sorted_tasks


def run_tasks(task_classes: List[Type[Task]], state: State) -> None:
    monkey_patch_print(state.logdir)

    for task_class in task_classes:
        state.tasks.append(task_class(state))

    # Write state
    state.save()

    # Start tasks
    threads = []
    for task in state.tasks:
        task_name = task.__class__.__name__
        # Set thread name to task name for output aggregation
        thread = threading.Thread(target=task._run, name=task_name)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def run_all_tasks_in_file(filename: Path, state: State) -> None:
    monkey_patch_print(state.logdir)

    state.meta["started"] = time.time()
    try:
        # Import tasks file
        spec = importlib.util.spec_from_file_location("tasks", filename)
        tasks_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tasks_module)  # type: ignore
        tasks = get_tasks_from_module(tasks_module)
    except Exception:
        # Write a file to indicate the run failed
        state.meta["finished"] = time.time()

        # Dummy task to indicate failure
        # TODO: Handle this more gracefully
        class FailedImport(Task):
            def __init__(self, state: State) -> None:
                super().__init__(state)
                self.status = Status.failed

        state.tasks.append(FailedImport(state))
        state.save()
        raise
    try:
        # Run all tasks
        run_tasks(tasks, state)
    except Exception:
        state.meta["finished"] = time.time()
        state.save()
        raise
    state.meta["finished"] = time.time()
    state.save()
