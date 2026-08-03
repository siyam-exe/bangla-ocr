import os
import time

from bangla_ocr.storage import (
    GIB,
    cleanup_disposable_files,
    configure_runtime_environment,
    estimate_workspace_bytes,
    rotate_file,
    storage_preflight,
    update_resource_extrema,
)


def _config(runtime_root):
    return {
        "storage": {
            "runtime_root": str(runtime_root),
            "system_drive": "C:\\",
            "system_warning_gib": 15,
            "system_block_gib": 12,
            "output_reserve_gib": 2,
        }
    }


def test_runtime_environment_pins_all_temp_variables(tmp_path):
    paths = configure_runtime_environment(_config(tmp_path / "runtime"))

    assert paths["temp"].is_dir()
    assert os.environ["TEMP"] == str(paths["temp"])
    assert os.environ["TMP"] == str(paths["temp"])
    assert os.environ["TMPDIR"] == str(paths["temp"])
    assert os.environ["SURYA_RUNTIME_DIR"] == str(paths["surya"])


def test_preflight_blocks_low_system_drive_and_low_output_space(
    monkeypatch, tmp_path
):
    snapshot = {
        "timestamp_utc": "test",
        "disks": {
            "system": {"free_bytes": 11 * GIB, "free": "11.0 GiB"},
            "runtime": {"free_bytes": 3 * GIB, "free": "3.0 GiB"},
            "output": {"free_bytes": 3 * GIB, "free": "3.0 GiB"},
        },
        "memory": {},
    }
    monkeypatch.setattr(
        "bangla_ocr.storage.resource_snapshot",
        lambda config, output_root: snapshot,
    )

    result = storage_preflight(
        _config(tmp_path / "runtime"),
        tmp_path,
        estimated_workspace_bytes=2 * GIB,
    )

    assert result["ready"] is False
    assert any("C:" in error for error in result["errors"])
    assert any("output drive" in error for error in result["errors"])


def test_cleanup_removes_only_old_disposable_files(tmp_path):
    runtime_root = tmp_path / "runtime"
    temp_root = runtime_root / "temp"
    source_root = tmp_path / "sources"
    temp_root.mkdir(parents=True)
    source_root.mkdir()
    old_temp = temp_root / "old.tmp"
    fresh_temp = temp_root / "fresh.tmp"
    old_upload = source_root / ".upload-abcd.pdf"
    real_source = source_root / "book-abcd.pdf"
    for path in (old_temp, fresh_temp, old_upload, real_source):
        path.write_bytes(b"1234")
    old_timestamp = time.time() - 48 * 3600
    os.utime(old_temp, (old_timestamp, old_timestamp))
    os.utime(old_upload, (old_timestamp, old_timestamp))

    result = cleanup_disposable_files(
        _config(runtime_root), source_root, older_than_hours=24
    )

    assert result["deleted_files"] == 2
    assert not old_temp.exists()
    assert not old_upload.exists()
    assert fresh_temp.exists()
    assert real_source.exists()


def test_log_rotation_is_bounded(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"x" * 20)

    assert rotate_file(log, max_bytes=10, backups=2) is True
    assert not log.exists()
    assert (tmp_path / "server.log.1").exists()


def test_resource_extrema_tracks_minimum_space_and_maximum_memory():
    first = {
        "timestamp_utc": "one",
        "disks": {
            "system": {"free_bytes": 100},
            "runtime": {"free_bytes": 200},
            "output": {"free_bytes": 200},
        },
        "memory": {
            "physical_available_bytes": 80,
            "commit_used_bytes": 20,
            "process_working_set_bytes": 10,
            "process_private_bytes": 9,
        },
    }
    second = {
        "timestamp_utc": "two",
        "disks": {
            "system": {"free_bytes": 90},
            "runtime": {"free_bytes": 180},
            "output": {"free_bytes": 180},
        },
        "memory": {
            "physical_available_bytes": 70,
            "commit_used_bytes": 30,
            "process_working_set_bytes": 15,
            "process_private_bytes": 14,
        },
    }

    extrema = update_resource_extrema({}, first)
    extrema = update_resource_extrema(extrema, second)

    assert extrema["minimum_system_free_bytes"] == 90
    assert extrema["minimum_physical_available_bytes"] == 70
    assert extrema["maximum_commit_used_bytes"] == 30
    assert extrema["maximum_process_working_set_bytes"] == 15


def test_workspace_estimate_includes_source_and_per_page_allowance(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"x" * 100)

    assert estimate_workspace_bytes(source, 3) == 100 + 6 * 1024 * 1024
