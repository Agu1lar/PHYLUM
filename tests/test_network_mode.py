import importlib
import sys


def test_desktop_backend_parses_lan_flags(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "desktop_backend.py",
            "--host",
            "0.0.0.0",
            "--port",
            "8100",
            "--allow-lan",
            "--public-base-url",
            "http://192.168.0.20:8100",
        ],
    )

    import desktop_backend

    args = desktop_backend.parse_args()

    assert args.host == "0.0.0.0"
    assert args.port == 8100
    assert args.allow_lan is True
    assert args.public_base_url == "http://192.168.0.20:8100"


def test_app_main_network_payload_uses_runtime_env(monkeypatch):
    monkeypatch.setenv("AGENTE_RUNTIME_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENTE_RUNTIME_PORT", "8100")
    monkeypatch.setenv("AGENTE_ALLOW_LAN", "true")
    monkeypatch.setenv("AGENTE_PUBLIC_BASE_URL", "http://192.168.0.20:8100")

    import app_main

    app_main = importlib.reload(app_main)
    payload = app_main._network_payload()

    assert payload["bind_host"] == "0.0.0.0"
    assert payload["port"] == 8100
    assert payload["lan_enabled"] is True
    assert payload["public_base_url"] == "http://192.168.0.20:8100"
    assert payload["ws_url"] == "ws://192.168.0.20:8100/ws"
    assert payload["cors"]["allow_origin_regex"]
