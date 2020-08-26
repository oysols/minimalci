from pathlib import Path
from minimalci import tasks
import tempfile

def test_state_snapshot() -> None:
    a = tasks.dict_to_dataclass(
        tasks.StateSnapshot,
        {
            "commit": "1234",
            "branch": "",
            "identifier": "",
            "repo_name": "",
            "log_url": "",
            "started": 3.4,
            "status": "running",
            "finished": 1.0,
            "tasks": [
                {
                    "name": "itititit",
                    "status": "running",
                    "run_after": ["1", "2", "3"],
                    "run_always": True,
                    "started": 4.0,
                    "finished": 5.0,
                },
            ],
        }
    )
    with tempfile.NamedTemporaryFile() as f:
        filepath = Path(f.name)
        a.save(filepath)
        b = tasks.StateSnapshot.load(filepath)
    assert a == b

test_state_snapshot()
