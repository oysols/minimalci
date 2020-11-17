import signal
import time
from typing import Any
import sys


def handle_signal(*args: Any, **kwargs: Any) -> None:
    print("GOT SIGNAL", args)
    raise Exception


if __name__ == "__main__":
    catchable_sigs = set(signal.Signals) - {signal.SIGKILL, signal.SIGSTOP}
    for sig in catchable_sigs:
        signal.signal(sig, handle_signal)

    try:
        while True:
            print("sleeping")
            time.sleep(1)
    except Exception:
        print("Exception received")
        print("sleeping 1 second and exit 101")
        time.sleep(1)
        sys.exit(101)
