import json
import dataclasses
from typing import TypeVar, Any, Type, Union, Generic
from pathlib import Path


T = TypeVar("T")


def validate_and_cast_to_type(data_type: Type[T], data: Any) -> T:
    # Basic types
    simple_types = [int, float, str, bool]
    if data_type in simple_types:
        if not isinstance(data, data_type):
            raise TypeError(f"Expected {data_type}, got '{type(data)}'")
        return data

    # Dataclasses
    if dataclasses.is_dataclass(data_type):
        if dataclasses.is_dataclass(data):
            data = dataclasses.asdict(data)
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict when casting to {data_type}, got '{type(data)}'")
        fieldtypes = {field.name: field.type for field in dataclasses.fields(data_type)}
        return data_type(
            **{
                key: validate_and_cast_to_type(fieldtypes[key], value)
                for key, value in data.items()
            }
        )  # type: ignore

    # Generic types
    elif hasattr(data_type, "__origin__"):
        # Optional[type]
        if data_type.__origin__ == Union:  # type: ignore
            try:
                optional_type, nonetype = data_type.__args__  # type: ignore
                assert type(None) == nonetype
            except Exception:
                raise Exception("Unsupported Union type. Only Optional supported.")
            return None if data is None else validate_and_cast_to_type(optional_type, data)  # type: ignore
        # List[type]
        elif data_type.__origin__ == list:  # type: ignore
            (item_type,) = data_type.__args__  # type: ignore
            return [validate_and_cast_to_type(item_type, item) for item in data]  # type: ignore
        # Dict[type, type]
        elif data_type.__origin__ == dict:  # type: ignore
            key_type, value_type = data_type.__args__  # type: ignore
            return {
                validate_and_cast_to_type(key_type, key): validate_and_cast_to_type(
                    value_type, value
                )
                for key, value in data.items()
            }  # type: ignore
        else:
            raise Exception("Unsupported generic type")

    raise TypeError(f"Unsupported type '{data_type}'")


def load_dataclass(dataclass_type: Type[T], filepath: Path) -> T:
    data = json.loads(filepath.read_text())
    return validate_and_cast_to_type(dataclass_type, data)


def save_dataclass(dataclass_type: Type[T], filepath: Path, dataclass_instance: T) -> None:
    validated_dataclass = validate_and_cast_to_type(dataclass_type, dataclass_instance)
    data = json.dumps(dataclasses.asdict(validated_dataclass), indent=4)
    filepath.write_text(data)


class DataclassFileStorage(Generic[T]):
    def __init__(self, dataclass_type: Type[T], path: Path):
        self.dataclass_type: Type[T] = dataclass_type
        self.path = path

    def load(self) -> T:
        return load_dataclass(self.dataclass_type, self.path)

    def save(self, dataclass_instance: T) -> None:
        save_dataclass(self.dataclass_type, self.path, dataclass_instance)
