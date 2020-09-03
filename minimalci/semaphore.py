from typing import Iterator, List, Any, Dict, Tuple, ContextManager, Sequence, Optional, TypeVar
import json
import subprocess
import contextlib
import time
import inspect
import threading
from queue import Queue

from minimalci import semaphore_subprocess


# Multiple simultaneous locks


LockLikeType = TypeVar("LockLikeType", ContextManager[Any], threading.Lock)


def _aquire_lock_and_block_until_event(
    lock: LockLikeType,
    lock_aquired_queue: "Queue[Optional[LockLikeType]]",
    release_event: threading.Event,
) -> None:
    """Get lock (blocking), add to queue and hold lock until receiving release event"""
    try:
        with lock:
            lock_aquired_queue.put(lock)
            release_event.wait()
    except Exception as e:
        print(f"Error getting lock: {e}")
        lock_aquired_queue.put(None)


@contextlib.contextmanager
def aquire_either_lock(locks: Sequence[LockLikeType]) -> Iterator[LockLikeType]:
    """Aquire and yield the first of two locks

    Get all locks in threads, yield first lock aquired and release all other locks immediately

    Usage:
        with aquire_either_lock([lock_a, lock_b]) as lock:
            do_stuff()
    """
    if not locks:
        raise Exception("No locks provided")
    release_events = [threading.Event() for lock in locks]
    lock_aquired_queue: Queue[LockLikeType] = Queue()
    # Try to get all locks in threads
    for lock, release_event in zip(locks, release_events):
        threading.Thread(
            target=_aquire_lock_and_block_until_event,
            args=(lock, lock_aquired_queue, release_event),
            name=threading.current_thread().name,  # For parsing print output in taskrunner
            daemon=True,
        ).start()
    # Wait for first lock aquired
    aquired_lock = lock_aquired_queue.get()
    if aquired_lock is None:
        [release_event.set() for release_event in release_events]
        raise Exception("Error getting lock")
    # Make sure all other locks are released as soon as they are aquired
    for lock, release_event in zip(locks, release_events):
        if lock != aquired_lock:
            release_event.set()
    # Yield the aquired lock to caller
    try:
        yield aquired_lock
    finally:
        # Release the lock
        release_events[locks.index(aquired_lock)].set()


# File based semaphore queue


def kill_thread(process: "subprocess.Popen[Any]", kill_signal: threading.Event) -> None:
    while not (kill_signal.is_set() or process.poll() is not None):
        kill_signal.wait(timeout=5)
    if process.poll() is None:
        process.terminate()


@contextlib.contextmanager
def semaphore_queue(path: str, self_description: str = "", verbose: bool = False, kill_signal: threading.Event = threading.Event()) -> Iterator[None]:
    """File based remote or local semaphore with self healing queue

    Usage:
        import semaphore
        with semaphore.semaphore_queue("user@remote_host:./semaphore.queue"):
            do_stuff()

    Semaphore file format
        {"concurrency": int, "queue": {"pid": int, "description": str}}

    path can be remote or local
        user@remote_host:./my/semaphore/path
        ./my/semaphore/path

    Requires passwordless SSH access for remote connections

    Sends python code to python subprocess on remote/local host
    Communicates with magic string to indicate aquiry of semaphore.

    Design goal:
        Semaphore without relying on central authority.
        Ease of use.
        Do not require setup on remote hosts.
        Gracefully handle and recover from failure conditions.
        Support Linux and MacOS
        Only rely on single simple file.
    """
    if ":" in path:
        host, filename = path.split(":")
        command = ["ssh", host]
    else:
        filename = path
        command = ["bash", "-ce"]  # Run in shell for consistency with ssh version
    command += [f"python3 -u - {filename} --self-description={self_description}"]
    semaphore_process_source = inspect.getsource(semaphore_subprocess)
    while True:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(
            target=kill_thread,
            args=(process, kill_signal),
            daemon=True,
            name=threading.current_thread().name,
        ).start()
        try:
            process.stdin.write(semaphore_process_source.encode())  # type: ignore
            process.stdin.close()  # type: ignore
            for raw_line in iter(process.stdout.readline, b""):  # type: ignore
                line = raw_line.decode().strip()
                if verbose:
                    if line.startswith(semaphore_subprocess.MESSAGE_PREFIX):
                        message = line[len(semaphore_subprocess.MESSAGE_PREFIX):]
                        print(message)
                if line == semaphore_subprocess.SEMAPHORE_AQUIRED:
                    if verbose:
                        print(f"Semaphore aquired {path}")
                    try:
                        yield
                    finally:
                        if verbose:
                            print(f"Semaphore released {path}")
                        process.terminate()
                        process.wait()
                        return
            if kill_signal.is_set() or process.wait() == 0:  # Required to handle KeyboardInterrupt
                raise Exception("Killed while waiting for semaphore")
            print("Semaphore process crashed")
            time.sleep(10)
            print("Retrying semaphore")
        finally:
            process.terminate()
            process.wait()


def read_queue(path: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Read remote or local semaphore queue"""
    if ":" in path:
        host, filename = path.split(":")
        command = ["ssh", host]
    else:
        filename = path
        command = ["bash", "-ce"]  # Run in shell for consistency with ssh version
    command += [f"python3 -u - {filename} --read"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    semaphore_process_source = inspect.getsource(semaphore_subprocess)
    process.stdin.write(semaphore_process_source.encode())  # type: ignore
    process.stdin.close()  # type: ignore
    raw_output = process.stdout.read()  # type: ignore
    concurrency, queue = json.loads(raw_output)
    return concurrency, queue
