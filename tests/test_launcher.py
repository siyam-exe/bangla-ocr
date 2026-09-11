import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

from bangla_ocr.application import create_application
from bangla_ocr.config import PIPELINE_ROOT
from bangla_ocr.launcher import (
    application_url,
    probe_application,
    server_command,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def test_application_health_identifies_the_local_service(tmp_path):
    app = create_application(
        output_root=tmp_path / "output",
        source_root=tmp_path / "sources",
    )
    app.config["TESTING"] = True

    response = app.test_client().get("/api/health")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "active_job": False,
        "application": "bangla-ocr",
        "application_root": str(PIPELINE_ROOT),
        "status": "ready",
        "version": "1.1.0",
    }


def test_probe_recognizes_bangla_ocr(monkeypatch):
    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(
            {
                "application": "bangla-ocr",
                "application_root": str(PIPELINE_ROOT),
                "status": "ready",
                "version": "1.1.0",
            }
        ),
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "ready"
    assert result.detail == "1.1.0"


def test_probe_rejects_a_different_bangla_ocr_installation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(
            {
                "application": "bangla-ocr",
                "application_root": str(tmp_path / "other-installation"),
                "status": "ready",
                "version": "1.1.0",
            }
        ),
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "occupied"
    assert "different Bangla OCR installation" in result.detail


def test_probe_treats_an_invalid_installation_root_as_an_address_conflict(
    monkeypatch,
):
    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(
            {
                "application": "bangla-ocr",
                "application_root": "\0",
                "status": "ready",
                "version": "1.1.0",
            }
        ),
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "occupied"


def test_probe_distinguishes_an_offline_service(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        unavailable,
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "offline"


def test_probe_rejects_an_unrelated_local_service(monkeypatch):
    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        lambda *args, **kwargs: FakeResponse(
            {"application": "another-program", "status": "ready"}
        ),
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "occupied"


def test_probe_treats_a_non_json_response_as_an_address_conflict(monkeypatch):
    response = FakeResponse({})
    response.body = b"not a Bangla OCR health response"
    monkeypatch.setattr(
        "bangla_ocr.launcher.urllib.request.urlopen",
        lambda *args, **kwargs: response,
    )

    result = probe_application("127.0.0.1", 8765)

    assert result.state == "occupied"


def test_server_command_starts_without_opening_a_browser():
    command = server_command("127.0.0.1", 9000)

    assert command == [
        sys.executable,
        "-m",
        "bangla_ocr",
        "app",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--no-browser",
    ]
    assert application_url("127.0.0.1", 9000) == "http://127.0.0.1:9000"
    assert application_url("::1", 9000) == "http://[::1]:9000"


def test_packaged_home_controls_data_and_runtime_paths(tmp_path):
    home = tmp_path / "Bangla OCR"
    config_root = home / "config"
    config_root.mkdir(parents=True)
    source_config = Path(__file__).resolve().parents[1] / "config" / "default.json"
    (config_root / "default.json").write_bytes(source_config.read_bytes())
    environment = os.environ.copy()
    environment["BANGLA_OCR_HOME"] = str(home)
    script = (
        "import json; "
        "from bangla_ocr.config import PIPELINE_ROOT, load_config; "
        "c = load_config(); "
        "print(json.dumps({'root': str(PIPELINE_ROOT), "
        "'output': c['output']['default_root'], "
        "'runtime': c['storage']['runtime_root']}))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = json.loads(result.stdout)

    assert Path(payload["root"]) == home
    assert Path(payload["output"]) == home / "documents"
    assert Path(payload["runtime"]) == home / "workspace"
