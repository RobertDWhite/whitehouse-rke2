import os

import yaml


def load_config():
    path = os.environ.get("CONFIG_PATH", "/etc/congress/config.yaml")
    with open(path) as f:
        return yaml.safe_load(f)
