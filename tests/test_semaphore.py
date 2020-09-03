from typing import List
import threading
import concurrent.futures
import time

from minimalci.semaphore import semaphore_queue, read_queue, aquire_either_lock


def test_semaphore_queue(semaphore: str) -> None:
    result = []

    def sleep(i: int) -> None:
        with semaphore_queue(semaphore, verbose=True):
            time.sleep(i)
            result.append(i)
    with concurrent.futures.ThreadPoolExecutor(3) as e:
        fs = []
        fs.append(e.submit(sleep, 3))
        time.sleep(0.5)
        fs.append(e.submit(sleep, 2))
        time.sleep(0.5)
        fs.append(e.submit(sleep, 1))
        [f.result() for f in fs]
        assert result == [3, 2, 1]


def test_aquire_either_lock() -> None:
    result: List[threading.Lock] = []

    def do_stuff(i: int, a: threading.Lock, b: threading.Lock, result: List[threading.Lock]) -> None:
        with aquire_either_lock([a, b]) as the_one:
            # print("thread", i, a == the_one, threading.active_count())
            result.append(the_one)
            time.sleep(0.2)

    a = threading.Lock()
    b = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(10) as e:
        fs = [e.submit(do_stuff, i, a, b, result) for i in range(10)]
        [f.result() for f in fs]

    assert result.count(a) == 5
    assert result.count(b) == 5


if __name__ == "__main__":
    print("aquire_either_lock")
    test_aquire_either_lock()
    print("ok")

    print("local")
    test_semaphore_queue("semaphore.queue")
    print("ok")

    print(read_queue("semaphore.queue"))

    # print("remote linux")
    # test_semaphore_queue("linuxhost:semaphore.queue")
    # print("ok")

    # print("remote macos")
    # test_semaphore_queue("machost:semaphore.queue")
    # print("ok")
