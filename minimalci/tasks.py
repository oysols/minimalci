import threading
from typing import List, Optional, Type, Any, Union, TypeVar, Callable, ContextManager
import dataclasses
import traceback
import time
import json
from pathlib import Path
import enum
from dataclasses import dataclass
import secrets

from minimalci.executors import ProcessError, global_kill_signal
from minimalci import semaphore, util


class Status(enum.Enum):
    not_started = 0
    running = 1
    waiting_for_task = 2
    waiting_for_semaphore = 3
    success = 4
    failed = 5
    skipped = 6


def get_overall_status(status_list: List[Status]) -> Status:
    if not status_list:
        return Status.not_started
    if all(status == Status.skipped for status in status_list):
        return Status.skipped
    if all(status in [Status.success, Status.skipped] for status in status_list):
        return Status.success
    if Status.running in status_list:
        return Status.running
    if Status.waiting_for_semaphore in status_list:
        return Status.waiting_for_semaphore
    if Status.waiting_for_task in status_list:
        return Status.waiting_for_task  # Should only happen momentarily in race conditions
    return Status.failed


class Skipped(Exception):
    pass


@dataclass
class TaskSnapshot:
    name: str
    status: str
    run_after: List[str]
    run_always: bool
    aquire_semaphore: List[str]
    aquired_semaphore: str
    started: Optional[float]
    finished: Optional[float]


@dataclass
class StateSnapshot:
    commit: str
    branch: str
    repo_name: str
    log_url: str
    identifier: str
    status: str
    started: float
    finished: Optional[float]
    tasks: List[TaskSnapshot]

    def save(self, filepath: Path) -> None:
        util.save_dataclass(StateSnapshot, filepath, self)

    @classmethod
    def load(cls, filepath: Path) -> "StateSnapshot":
        return util.load_dataclass(StateSnapshot, filepath)


class State():
    def __init__(
        self,
        commit: str = "",
        branch: str = "",
        repo_name: str = "",
        log_url: str = "",
        identifier: str = "",
        logdir: Optional[Path] = None,
    ) -> None:
        self.commit = ""
        self.branch = ""
        self.repo_name = ""
        self.log_url = ""
        self.identifier = identifier if identifier else secrets.token_hex(16)
        self.logdir = logdir if logdir else Path()
        self.tasks: List[Task] = []
        self.started = time.time()
        self.finished: Optional[float] = None

    def get_task_by_class(self, task_class: Type["Task"]) -> "Task":
        for task in self.tasks:
            if task.__class__ == task_class:
                return task
        raise Exception("Task not found: {}".format(task_class))

    def status(self) -> Status:
        return get_overall_status([task.status for task in self.tasks])

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            commit=self.commit,
            branch=self.branch,
            repo_name=self.repo_name,
            log_url=self.log_url,
            identifier=self.identifier,
            status=self.status().name,
            started=self.started,
            finished=self.finished,
            tasks=[
                TaskSnapshot(
                    name=task.name,
                    status=task.status.name,
                    run_after=[self.get_task_by_class(task_class).name for task_class in task.run_after],
                    run_always=task.run_always,
                    aquire_semaphore=task.aquire_semaphore,
                    aquired_semaphore=task.aquired_semaphore,
                    started=task.started,
                    finished=task.finished,
                )
                for task in self.tasks
            ],
        )

    def save(self) -> None:
        # Hack to initialize at runtime instead of init
        try:
            getattr(self, "save_lock")
        except AttributeError:
            self.save_lock = threading.Lock()
        with self.save_lock:
            self.snapshot().save(self.logdir / "state.json")


class Task:
    run_after: List[Type["Task"]] = []
    run_always = False
    aquire_semaphore: List[str] = []

    def __init__(self, state: State) -> None:
        self.state = state
        self.status = Status.not_started
        self.aquired_semaphore: str = ""
        self.exception: Optional[Exception] = None
        self.completed = threading.Event()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None

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
        try:
            self.wait_for_tasks(self.run_after)
            if self.aquire_semaphore:
                self.status = Status.waiting_for_semaphore
                self_description = ":".join([self.name, self.state.repo_name, self.state.identifier])
                semaphore_queues: List[ContextManager[Any]] = [
                    semaphore.semaphore_queue(
                        semaphore_string,
                        self_description=self_description,
                        verbose=True,
                        kill_signal=global_kill_signal,
                    )
                    for semaphore_string in self.aquire_semaphore
                ]
                with semaphore.aquire_either_lock(semaphore_queues) as aquired_semaphore:
                    self.aquired_semaphore = self.aquire_semaphore[semaphore_queues.index(aquired_semaphore)]
                    print("Task started")
                    self.started = time.time()
                    self.status = Status.running
                    self.run()
                    print("Task success")
                    self.status = Status.success
            else:
                print("Task started")
                self.started = time.time()
                self.status = Status.running
                self.run()
                print("Task success")
                self.status = Status.success
        except Exception as e:
            if isinstance(e, Skipped):
                self.status = Status.skipped
                print("Task skipped")
            else:
                self.status = Status.failed
                self.exception = e
                if isinstance(e, ProcessError):
                    print("Task failed: {}".format(e))
                else:
                    print("Task failed")
                    for line in traceback.format_exc().splitlines():
                        print(line)
        finally:
            self.finished = time.time()
            self.state.save()
            self.completed.set()

    def wait_for_tasks(self, task_classes: List[Type["Task"]]) -> None:
        if not task_classes:
            return
        self.status = Status.waiting_for_task
        tasks = [self.state.get_task_by_class(task_class) for task_class in task_classes]
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
