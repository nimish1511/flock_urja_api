from app import __main__ as app_entrypoint
from app.config import settings


def test_app_module_entrypoint_uses_configured_host_and_port(monkeypatch):
    calls = {}

    def fake_run(app, host, port, reload):
        calls.update(app=app, host=host, port=port, reload=reload)

    monkeypatch.setattr(app_entrypoint.uvicorn, "run", fake_run)
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "port", 8765)
    monkeypatch.setattr(settings, "debug", False)

    app_entrypoint.main()

    assert calls == {
        "app": "app.main:app",
        "host": "127.0.0.1",
        "port": 8765,
        "reload": False,
    }
