import concurrent.futures
import threading
from typing import Dict, List, Optional, Type, Any
import traceback
import time
import json
from pathlib import Path
import datetime
import enum
import os

from .executors import NonZeroExit


class Status(enum.Enum):
    not_started = 0
    running = 1
    waiting_for_task = 2
    waiting_for_semaphore = 3
    success = 4
    failed = 5
    skipped = 6


class Skipped(Exception):
    pass


class State():
    def __init__(self) -> None:
        self.source: Optional[Path] = None
        self.secrets: Optional[Path] = None
        self.tasks: List[Task] = []
        self.logdir: Path = Path()

        # Meta
        self.meta: Dict[str, Any] = {}
        self.commit: str = ""
        self.branch: str = ""
        self.repo_name: str = ""
        self.log_url: str = ""

    def save(self) -> None:
        # Hack to initialize at runtime instead of init
        try:
            getattr(self, "save_lock")
        except AttributeError:
            self.save_lock = threading.Lock()
        with self.save_lock:
            out = {
                "commit": self.commit,
                "branch": self.branch,
                "repo_name": self.repo_name,
                "log_url": self.log_url,
                "meta": self.meta,
                "tasks": [
                    {"name": task.name, "status": task.status.name}
                    for task in self.tasks
                ],
            }
            (self.logdir / "taskstate.json").write_text(json.dumps(out, indent=4))


class Task:
    run_after: List[Type["Task"]] = []
    run_always = False
    wait_for_semaphore = ""

    def __init__(self, state: State) -> None:
        self.state = state
        self.status = Status.not_started
        self.exception: Optional[Exception] = None
        self.completed = threading.Event()

    @property
    def status(self) -> Status:
        return self._status

    @status.setter
    def status(self, new_status: Status) -> None:
        self._status = new_status
        self.state.save()

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def run(self) -> None:
        raise NotImplementedError

    def _run(self) -> None:
        self.status = Status.running
        print("Task started")
        try:
            self.wait_for_tasks(self.run_after)
            # if self.wait_for_semaphore:
            #     with self._wait_for_semaphore(self.wait_for_semaphore):
            #         self.run()
            # else:
            self.run()
        except Exception as e:
            if isinstance(e, Skipped):
                self.status = Status.skipped
                self.completed.set()
                print("Task skipped")
            else:
                self.status = Status.failed
                self.completed.set()
                self.exception = e
                if isinstance(e, NonZeroExit):
                    print("Task failed: {}".format(e))
                    raise
                else:
                    print("Task failed")
                    for line in traceback.format_exc().splitlines():
                        print(line)
                    raise  # unexpected Exception
        else:
            self.status = Status.success
            self.completed.set()
            print("Task success")

    def get_task_by_class(self, task_class: Type["Task"]) -> "Task":
        for task in self.state.tasks:
            if task.__class__ == task_class:
                return task
        raise Exception("Task not found: {}".format(task_class))

    def wait_for_tasks(self, task_classes: List[Type["Task"]]) -> None:
        if not task_classes:
            return
        self.status = Status.waiting_for_task
        tasks = [self.get_task_by_class(task_class) for task_class in task_classes]
        for task in tasks:
            if not task.completed.is_set():
                print("Waiting for task: {}".format(task.__class__.__name__))
            task.completed.wait()
        for task in tasks:
            if not task.status == Status.success and not self.run_always:
                time.sleep(0.2)  # For improved logging
                print("Dependent task did not succeed: {}".format(task.__class__.__name__))
                raise Skipped
        print("Finished waiting for tasks: {}".format(", ".join([task_class.__name__ for task_class in task_classes])))
        self.status = Status.running

    # TODO: Reimplement file based semaphore for cross process/host support
    # @contextlib.contextmanager
    # def _wait_for_semaphore(self, sempahore_name: str) -> Iterator[str]:
    #     self.status = Status.waiting_for_semaphore
    #     semaphore = self.state.semaphores[sempahore_name]
    #     did_wait = False
    #     if semaphore.get_value() <= 0:  # Only print if we are likely to wait
    #         did_wait = True
    #         print("Waiting for semaphore {}".format(sempahore_name))
    #     with semaphore:
    #         if did_wait:
    #             print("Finished waiting for semaphore: {}".format(sempahore_name))
    #         self.status = Status.running
    #         yield sempahore_name
