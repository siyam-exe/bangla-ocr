from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .utils import merge_dicts, read_json
from .storage import configure_runtime_environment


APPLICATION_HOME = (
    Path(os.environ["BANGLA_OCR_HOME"]).expanduser().resolve()
    if os.environ.get("BANGLA_OCR_HOME")
    else None
)
PIPELINE_ROOT = APPLICATION_HOME or Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PIPELINE_ROOT / "config" / "default.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    config = read_json(DEFAULT_CONFIG)
    if APPLICATION_HOME is not None:
        config["output"]["default_root"] = str(APPLICATION_HOME / "documents")
        config["storage"]["runtime_root"] = str(APPLICATION_HOME / "workspace")
    if path:
        config = merge_dicts(config, read_json(path))
    for section, key in (
        ("output", "default_root"),
        ("storage", "runtime_root"),
    ):
        value = Path(str(config[section][key])).expanduser()
        if not value.is_absolute():
            value = (PIPELINE_ROOT / value).resolve()
        config[section][key] = str(value)
    configure_runtime_environment(config)
    return config
