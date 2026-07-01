"""
Tests für die Server-Deployment-Änderungen:
- Job-Warteschlange (ANALYST_MAX_CONCURRENT) serialisiert wirklich
- ANALYST_ONLY blendet die Editor-Router aus, Analyst bleibt erreichbar

Laufen ohne API-Keys (keine Live-Calls): die schwere Pipeline _run wird gestubbt.
"""
import importlib
import threading
import time


def test_queue_serializes(monkeypatch):
    """Bei Default ANALYST_MAX_CONCURRENT=1 darf nie mehr als 1 Analyse gleichzeitig
    durch die schwere Pipeline laufen — der Rest wartet in der Schlange."""
    from services import analyst_engine as ae

    concurrent = 0
    max_seen = 0
    lock = threading.Lock()

    def fake_run(run_id, run_dir):
        nonlocal concurrent, max_seen
        with lock:
            concurrent += 1
            max_seen = max(max_seen, concurrent)
        time.sleep(0.2)
        with lock:
            concurrent -= 1

    monkeypatch.setattr(ae, "_run", fake_run)
    monkeypatch.setattr(ae, "write_status", lambda *a, **k: None)

    threads = [threading.Thread(target=ae.run_analysis, args=(f"r{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_seen == 1, f"Warteschlange verletzt: {max_seen} liefen parallel"


def _route_paths():
    import main
    return {getattr(r, "path", "") for r in main.app.routes}


def test_analyst_only_hides_editor_routers(monkeypatch):
    """ANALYST_ONLY → /api/analyst erreichbar, /api/projects & Co. nicht eingebunden.

    WICHTIG: config wird NICHT neu geladen (das würde config.settings durch ein neues
    Objekt ersetzen und andere Module, die `from config import settings` halten, brechen).
    Wir schalten nur das geteilte settings-Objekt um und laden allein main neu.
    """
    import config
    import main
    monkeypatch.setattr(config.settings, "analyst_only", True)
    importlib.reload(main)  # bindet erneut an DASSELBE config.settings (jetzt analyst_only=True)
    try:
        paths = _route_paths()
        assert any(p.startswith("/api/analyst") for p in paths), "Analyst muss erreichbar sein"
        for editor in ("/api/projects", "/api/pipeline", "/api/subtitles", "/api/shorts", "/api/feedback"):
            assert not any(p.startswith(editor) for p in paths), f"{editor} darf bei ANALYST_ONLY nicht da sein"
    finally:
        # Volle App wiederherstellen, damit Folge-Tests die Editor-Router sehen.
        config.settings.analyst_only = False
        importlib.reload(main)


def test_full_app_has_editor_routers():
    """Ohne Flag (lokal) ist der Editor wieder da."""
    import config
    import main
    config.settings.analyst_only = False
    importlib.reload(main)
    paths = _route_paths()
    assert any(p.startswith("/api/projects") for p in paths)
    assert any(p.startswith("/api/analyst") for p in paths)
