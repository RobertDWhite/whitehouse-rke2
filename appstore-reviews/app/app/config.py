import os

import yaml

DEFAULTS = {
    "ai": {
        "base_url": "http://ollama-router.ai-stack.svc.cluster.local:11434/v1",
        "model": "qwen2.5:14b",
        "fallback_models": ["llama3.1:8b", "llama3.2:3b"],
        "temperature": 0.4,
        "request_timeout": 120.0,
    }
}


def load_config():
    path = os.environ.get("CONFIG_PATH")
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if path and os.path.exists(path):
        with open(path) as f:
            loaded = yaml.safe_load(f) or {}
        for section, values in loaded.items():
            cfg.setdefault(section, {}).update(values or {})
    return cfg
