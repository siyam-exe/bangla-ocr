from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from bangla_ocr.application import _completed_page_count, _failure_details
from bangla_ocr.engines.surya_engine import SuryaEngine
from bangla_ocr.utils import write_json


def test_surya_server_log_stays_inside_the_runtime_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    runtime_dir = tmp_path / "workspace" / "surya"
    monkeypatch.setenv("SURYA_RUNTIME_DIR", str(runtime_dir))
    engine = SuryaEngine({"surya_runtime_backend": "cpu"}, tmp_path)
    engine._configure_upstream_runtime_cache()

    from surya.inference.backends import llamacpp as upstream

    opened: dict[str, Path] = {}

    class Process:
        pid = 4321

    def open_log(path, *args, **kwargs):
        opened["path"] = Path(path)
        return io.BytesIO()

    def attach_or_spawn(*, spawn_fn, expected_model_name, **kwargs):
        spawn_fn(8766)
        return SimpleNamespace(
            base_url="http://127.0.0.1:8766/v1",
            model_name=expected_model_name,
            spawned_by_us=True,
        )

    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(upstream, "open", open_log, raising=False)
    monkeypatch.setattr(
        upstream,
        "_resolve_llama_server_binary",
        lambda: "llama-server.exe",
    )
    monkeypatch.setattr(
        upstream,
        "_download_gguf_files",
        lambda: ("model.gguf", "mmproj.gguf"),
    )
    monkeypatch.setattr(upstream, "attach_or_spawn", attach_or_spawn)
    monkeypatch.setattr(upstream.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(upstream, "OpenAI", lambda **kwargs: object())

    upstream.LlamaCppBackend().start()

    assert opened["path"] == runtime_dir / "llamacpp_server.log"


def test_completed_page_count_is_limited_to_the_current_job(
    tmp_path: Path,
):
    book_root = tmp_path / "book"
    write_json(
        book_root / "audit" / "processing.json",
        {"all_processed_pages": [18, 19, 75]},
    )

    assert _completed_page_count(book_root, selected_pages={18, 19}) == 2


def test_runtime_cache_failure_names_the_configured_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    runtime_dir = tmp_path / "workspace" / "surya"
    monkeypatch.setenv("SURYA_RUNTIME_DIR", str(runtime_dir))

    failure = _failure_details(
        OSError("[WinError 448] The path contains an untrusted mount point"),
        "surya",
    )

    assert failure["category"] == "runtime_cache"
    assert failure["runtime_path"] == str(runtime_dir)
    assert "writable" in failure["explanation"].casefold()
