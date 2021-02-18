from dataclasses import dataclass
from typing import Dict, Optional
from pathlib import Path

from minimalci import util


@dataclass
class SomeDataclass:
    a: str
    b: int
    c: Optional[bool]
    d: Dict[str, str]


@dataclass
class SomeOtherDataclass:
    rr: str


def test_save_and_load_dataclass() -> None:
    data_path = Path("tmp.state.test")
    a = SomeDataclass("333", 3, True, {"fff": "3"})
    util.save_dataclass(SomeDataclass, data_path, a)
    b = util.load_dataclass(SomeDataclass, data_path)
    assert a == b
    b.d["test"] = "1"
    util.save_dataclass(SomeDataclass, data_path, b)
    c = util.load_dataclass(SomeDataclass, data_path)
    assert c == b


def test_dataclass_file_storage() -> None:
    data_path = Path("tmp.state.test")
    some_dataclass_storage = util.DataclassFileStorage(SomeDataclass, data_path)
    eee = some_dataclass_storage.load()
    eee.a = "some other string"
    some_dataclass_storage.save(eee)

    ggg = SomeOtherDataclass("ergfg")
    try:
        # This error is caught by mypy, so explicitly ignore
        some_dataclass_storage.save(ggg)  # type: ignore
    except KeyError:
        pass
    else:
        assert False


test_save_and_load_dataclass()
test_dataclass_file_storage()
