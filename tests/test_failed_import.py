from pathlib import Path
import tempfile

import sys
sys.path.append(".")

from minimalci import taskrunner
from minimalci import executors
from minimalci import tasks


def test_failed_import() -> None:
    state = tasks.State()
    with tempfile.NamedTemporaryFile() as f:
        filepath = Path(f.name + ".py")
        filepath.write_text("1 = 2")
        taskrunner.run_all_tasks_in_file(filepath, state)
    assert state.status() == tasks.Status.failed

def test_empty_file_import() -> None:
    state = tasks.State()
    with tempfile.NamedTemporaryFile() as f:
        filepath = Path(f.name + ".py")
        filepath.write_text("")
        taskrunner.run_all_tasks_in_file(filepath, state)
    assert state.status() == tasks.Status.skipped

def test_no_file_import() -> None:
    state = tasks.State()
    with tempfile.NamedTemporaryFile() as f:
        taskrunner.run_all_tasks_in_file(Path("iuwehfiuasndfiuhwiefsdfergdber.py"), state)
    assert state.status() == tasks.Status.failed

test_failed_import()
test_empty_file_import()
test_no_file_import()
print("ok")
