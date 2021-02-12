import importlib.util
import contextlib
import inspect
from pathlib import Path
import time
from typing import List, Type, Any, Iterator
import threading
import datetime
import functools
import builtins
import sys
import traceback
import argparse
import signal

from .executors import global_kill_signal_handler
from .tasks import Task, State


def print_with_thread_prefix(global_log_write_lock: threading.Lock, logdir: Path, *args: Any, **kwargs: Any) -> None:
    # Swallows kwargs to keep signature compatible with builtins.print
    # TODO: use json-based logging instead
    prefix = threading.current_thread().name.split("-")[0]
    raw_print_string = " ".join([str(arg) for arg in args]).strip()
    if raw_print_string:
        for print_string in raw_print_string.splitlines():
            timestamp = datetime.datetime.utcnow().isoformat()
            line = f"{timestamp} {prefix:<20} {print_string}\n"
            sys.stdout.write(line)
            with global_log_write_lock:
                with open(logdir / "output.log", "a") as f:
                    f.write(line)


@contextlib.contextmanager
def monkey_patch_print(logdir: Path) -> Iterator[None]:
    original_print = builtins.print
    global_log_write_lock = threading.Lock()
    builtins.print = functools.partial(print_with_thread_prefix, global_log_write_lock, logdir)
    try:
        yield
    finally:
        builtins.print = original_print


def get_tasks_from_module(obj: object) -> List[Type[Task]]:
    tasks = [
        task for name, task in inspect.getmembers(obj)
        if inspect.isclass(task) and issubclass(task, Task) and task != Task
    ]
    # Sort tasks according to line number
    sorted_tasks = sorted(tasks, key=lambda task: task.run.__code__.co_firstlineno)  # type: ignore
    return sorted_tasks


def run_tasks(task_classes: List[Type[Task]], state: State) -> None:
    with monkey_patch_print(state.logdir):
        for task_class in task_classes:
            state.tasks.append(task_class(state))

        # Write initial state
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

        state.finished = time.time()
        state.save()


def run_all_tasks_in_file(filename: Path, state: State) -> None:
    # Import tasks file
    try:
        spec = importlib.util.spec_from_file_location("tasks", filename)
        tasks_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tasks_module)  # type: ignore
        tasks = get_tasks_from_module(tasks_module)
    except (Exception, SyntaxError):
        tb = traceback.format_exc()

        # Dummy task to indicate failure
        class FailedImport(Task):
            def run(self) -> None:
                print(tb)
                raise Exception("Import failed")
        tasks = [FailedImport]
    # Run all tasks
    run_tasks(tasks, state)


if __name__ == "__main__":
    # python3 -m minimalci.taskrunner <args>
    parser = argparse.ArgumentParser(description='Run tasks')
    parser.add_argument('--commit', help='Commit sha', default="local")
    parser.add_argument('--branch', help='Branch name', default="")
    parser.add_argument('--identifier', help="Unique identifier for run", default="")
    parser.add_argument('--repo-name', help='Name of repository', default="")
    parser.add_argument('--log-url', help='Commit sha', default="")
    parser.add_argument('--logdir', help='Directory for state and output logs', default=".")
    parser.add_argument('--file', help='Tasks file', default="tasks.py")
    args = parser.parse_args()

    state = State()
    state.commit = args.commit
    state.branch = args.branch
    state.repo_name = args.repo_name
    state.log_url = args.log_url
    state.logdir = Path(args.logdir)
    state.identifier = args.identifier

    # Kill executor processes and cleanly exit on SIGTERM
    signal.signal(signal.SIGTERM, global_kill_signal_handler)
    signal.signal(signal.SIGINT, global_kill_signal_handler)

    run_all_tasks_in_file(args.file, state)
