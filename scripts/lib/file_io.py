"""RUNNER_TEMP path helpers and JSON read/write utilities."""
import json
import os
from pathlib import Path

RUNNER_TEMP = Path(os.environ.get("RUNNER_TEMP", "."))


def runner_temp_path(filename: str) -> Path:
    return RUNNER_TEMP / filename


def write_json(filename: str, data: dict) -> None:
    path = runner_temp_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_json(filename: str) -> dict:
    path = runner_temp_path(filename)
    with open(path, encoding="utf-8") as f:
        return json.load(f)
