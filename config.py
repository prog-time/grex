import os
import sys
import yaml  # type: ignore[import-untyped]


base_path = os.path.dirname(sys.argv[0])
data_path = os.path.join(base_path, "data")


def load_config(file_path) -> dict:
    with open(os.path.join(data_path, file_path)) as file:
        config = yaml.safe_load(file)
    return config


settings = load_config("settings.yml")

settings.update({
    "soft_version": "1.0.0",
})
