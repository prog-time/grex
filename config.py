import os
import sys

import yaml  # type: ignore[import-untyped]

base_path = os.path.dirname(sys.argv[0])
data_path = os.path.join(base_path, "data")


def load_config(file_path) -> dict:
    with open(os.path.join(data_path, file_path)) as file:
        config = yaml.safe_load(file)
    return config


def load_version() -> str:
    """App version from the VERSION file next to this module — its only source."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"), encoding="utf-8") as file:
        return file.read().strip()


settings = load_config("settings.yml")

settings.update({
    "soft_version": load_version(),
})
