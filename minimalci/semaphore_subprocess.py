from typing import List, Any, Dict, Tuple
import json
import subprocess
import fcntl
import time
import os
from pathlib import Path
import signal
import argparse


# This file intentionally Python 3.5 compatible since it might be run on outdated hosts


SEMAPHORE_AQUIRED = "SEMAPHORE_AQUIRED"
MESSAGE_PREFIX = "MESSAGE:"


def read_and_update_queue(
    filename: str,
    add_self: bool = False,
    remove_self: bool = False,
    self_description: str = "",
) -> Tuple[int, List[Dict[str, Any]]]:
    """Gets lock on queue, verifies state of pids, add or removes its own pid"""
    with open(filename, "r+") as f:
        # Take file lock
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        # Load queue
        raw_data = f.read()
        try:
            data = json.loads(raw_data)
            concurrency = data["concurrency"]
            queue = data["queue"]
        except Exception:
            raise Exception("Queue parse error", raw_data)
        # Check that pids are running
        try:
            output = subprocess.check_output(
                ["ps", "-o", "pid,state", *[str(entry["pid"]) for entry in queue]]
            ).decode().strip()
            running_pids = [
                int(line.strip().split()[0])
                for line in output.splitlines()[1:]
                if line.strip().split()[1] != "Z"  # Zombie process
            ]
        except subprocess.CalledProcessError:
            running_pids = []
        # Prune queue based on only running pids
        verified_queue = [entry for entry in queue if entry["pid"] in running_pids]
        self_pid = os.getpid()
        if add_self:
            # Verify that self_pid is in the queue or insert
            if self_pid not in [entry["pid"] for entry in verified_queue]:
                verified_queue.append(
                    {"pid": self_pid, "description": self_description}
                )
        if remove_self:
            # Remove self_pid entry from queue
            verified_queue = [entry for entry in verified_queue if entry["pid"] != self_pid]
        if verified_queue != queue:
            # Write queue
            f.seek(0)
            new_data = {"concurrency": concurrency, "queue": verified_queue}
            f.write(json.dumps(new_data, indent=4))
            f.truncate()
        return concurrency, verified_queue


def signal_handler(*args: Any) -> None:
    raise Exception


def wait_in_queue(filename: str, self_description: str = "") -> None:
    """Reads and updates queue with own PID
    Prunes dead PIDs
    Communicates aquisition by magic string on stdout
    Aquisition is determined by its pids order in the queue less than concurrency setting
    """
    signal.signal(signal.SIGTERM, signal_handler)  # Handle SIGTERM gracefully

    if not Path(filename).is_file():  # Create queue first time for ease of use
        Path(filename).write_text('{"concurrency": 1, "queue": []}')

    try:
        last_message = ""
        while True:
            concurrency, queue = read_and_update_queue(filename, add_self=True, self_description=self_description)
            # Get number in queue
            self_pid = os.getpid()
            my_index = [entry["pid"] for entry in queue].index(self_pid)
            # Check if should run
            if my_index < concurrency:
                break  # Semaphore aquired
            new_message = MESSAGE_PREFIX + "Position in queue: {} (concurrency {})".format(my_index, concurrency)
            if new_message != last_message:
                print(new_message)
                last_message = new_message
            else:
                print()  # Force crash if parent process is dead
            time.sleep(1)

        print(SEMAPHORE_AQUIRED)  # Signal that we have aquired semaphore
        while True:
            time.sleep(1)
            print()  # Force crash if parent process is dead
    except KeyboardInterrupt:
        pass
    finally:
        read_and_update_queue(filename, remove_self=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Aquire semaphore from file queue')
    parser.add_argument('filename', help='Path to queue')
    parser.add_argument('--self-description', help='Description to add to self in queue', default='')
    parser.add_argument('--read', help='Read queue and return as json', action="store_true")
    args = parser.parse_args()

    if args.read:
        concurrency, queue = read_and_update_queue(args.filename)
        print(json.dumps([concurrency, queue]))
    else:
        wait_in_queue(args.filename, args.self_description)
