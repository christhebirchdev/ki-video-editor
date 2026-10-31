# Integration des Video-Analysten ins Agentursystem

Diese Anleitung ist für die Person oder KI geschrieben, die den Analysten ins KI-System der Agentur
übernimmt. Lies sie ganz, bevor du etwas änderst.

## Kurzfassung

Der Analyst ist **fertig**. Er bleibt ein **eigener Dienst** (Python/FastAPI in einem Docker-Container)
und wird vom Agentursystem nur angebunden. Du musst **zwei Dinge** tun — sonst nichts:

1. Der Reverse Proxy des Agentursystems setzt bei **jeder** Anfrage an den Analysten den Header
   `X-Analyst-Nutzer: <stabile Konto-ID des eingeloggten Nutzers>` — **serverseitig**, und
   überschreibt dabei einen Wert, den der Browser eventuell mitschickt.
   Zusätzlich im Container: `ANALYST_NUTZER_PFLICHT=1`.
2. Wird ein Konto gelöscht: `DELETE /api/analyst/meine` mit diesem Header aufrufen.

**Nicht** den Analysten in eine andere Sprache oder ein anderes Framework umschreiben. Die
Bewertungslogik ist über Monate an echten Videos kalibriert (über 600 automatische Tests). Eine
Übersetzung verliert diese Kalibrierung, ohne dass es jemand merkt.

## 1. Betrieb

- Bauen und starten: `docker compose up -d --build` im Repo-Root (`Dockerfile`, `docker-compose.yml`).
- Port im Container: `8001`. Persistente Volumes: `analyst_runs` (Analysen, Videos, Chats) und
  `analyst_models` (Whisper-Modell-Cache).
- Pflicht-Variablen in `.env`: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `ASSEMBLYAI_API_KEY`,
  `ANALYST_ONLY=1`, `ADMIN_PASSWORD` (Admin-Ansicht).
- Ressourcen: eine Analyse belegt etwa 1,5 CPU während der Transkription und 2–4 GB RAM.
  `ANALYST_MAX_CONCURRENT` (Default 1) begrenzt parallele Analysen, weitere warten in einer Queue.

| Variable | Default | Bedeutung |
|---|---|---|
| `ANALYST_NUTZER_PFLICHT` | `0` | `1` = jede Anfrage braucht `X-Analyst-Nutzer`, sonst 401. Nach der Integration **immer 1**. |
| `ANALYST_VIDEO_TAGE` | `0` (Compose: `30`) | Videodateien nach N Tagen löschen. Analysen bleiben. `0` = nie. |
| `ANALYST_MAX_CONCURRENT` | `1` | Gleichzeitige Analysen. |
| `WHISPER_MODEL` | `small` | Transkriptionsmodell. Auf schwacher CPU `base`. |

## 2. Einbindung

Variante A (empfohlen): Das Agentursystem leitet einen Pfad (z. B. `/video-analyst/`) per Reverse
Proxy auf den Container und zeigt ihn in einem eigenen Menüpunkt oder iframe. Die Oberfläche bringt
der Analyst selbst mit (`templates/index.html`, `static/app.jsx`).

Variante B: Das Agentursystem baut eine eigene Oberfläche und ruft nur die API auf (Abschnitt 4).

In beiden Fällen gilt: Der Header `X-Analyst-Nutzer` kommt vom **Proxy**, nie aus Browser-Code
des Agentursystems. Sonst könnte ein Nutzer sich als ein anderer ausgeben.

Format der Nutzer-ID: `[A-Za-z0-9_-]`, 8–64 Zeichen. Andere Werte werden ignoriert, als ob kein
Header da wäre (im Pflichtmodus also 401). Kürzere Konto-IDs bitte z. B. mit einem Präfix
verlängern (`konto-42`) — und dieses Präfix dann immer gleich verwenden.

**Sicherheit:** Der Container selbst hat keine Anmeldung. Er darf nach der Integration nur noch
über den Proxy erreichbar sein (kein öffentlicher Port, keine öffentliche Subdomain ohne Proxy).

## 3. Was beim Umzug automatisch passiert

- Kunden haben bis zur Integration eine **Browser-ID** benutzt. Das Frontend schickt sie zusätzlich
  als `X-Analyst-Geraet` mit. Ist `ANALYST_NUTZER_PFLICHT=1` und weicht die Browser-ID von der
  Konto-ID ab, schreibt der Analyst beim ersten Öffnen von „Meine Analysen" die Läufe dieser
  Browser-ID auf das Konto um. Kein Zusatzcode nötig. Wichtig: Der Proxy darf `X-Analyst-Geraet`
  **nicht** entfernen.
- Läufe ohne Besitzer (aus der Zeit vor V4) sieht im Pflichtmodus niemand mehr außer über die
  Admin-Ansicht. Das ist gewollt.
- Datenumzug vom alten Server: das Volume `analyst_runs` 1:1 kopieren (z. B.
  `docker run --rm -v analyst_runs:/d -v $PWD:/b alpine tar czf /b/runs.tgz -C /d .`).

## 4. API (Auszug, alle unter `/api/analyst`)

| Methode + Pfad | Zweck |
|---|---|
| `POST /upload` (multipart `file`) | Video hochladen → `{id}` |
| `POST /{id}/marke` (multipart `files`) | optional: Zielgruppen-/Strategiedateien |
| `POST /{id}/start?engine=v4&format=…&ziel=TOFU\|MOFU\|BOFU[&vorgaenger=<id>]` | Analyse starten |
| `GET /{id}` | Status, Queue-Platz, Ergebnis (`result`) |
| `GET/POST /{id}/chat` | Rückfragen-Chat (Antwort gestreamt) |
| `GET /meine` | eigene Analysen, neueste zuerst |
| `GET /fortschritt` | Gesamtlevel + Schnitt-Score je Kategorie (letzte 5 Videos) |
| `GET /{id}/video` | gespeichertes Video abspielen (mit Range, 410 wenn gelöscht) |
| `GET /meine/export` | alle eigenen Analysen mit Chat (Datenauskunft) |
| `POST /{id}/neu-bewerten` | neuer Lauf mit demselben Video (anderes Ziel) |
| `DELETE /{id}` | eigenen Lauf löschen |
| `DELETE /meine` | alle eigenen Läufe löschen (Konto-Löschung) |

Fremde Läufe antworten mit 404, nicht 403.

## 5. Nicht anfassen

- `services/analyst_eval.py`, `services/analyst_eval_skill_v3.md`, `services/analyst_gemini_eval.py`:
  Bewertungslogik und Prompt. Jede Prompt-Änderung braucht ein Hochzählen von `PROMPT_VERSION`
  (Cache), sonst liefert der Cache alte Ergebnisse.
- `models/analyst.py`: Das Datenformat der gespeicherten Analysen. Ältere Läufe müssen lesbar bleiben.
- `services/analyst_nutzer.py`: die einzige Stelle, an der der Nutzer bestimmt wird. Soll der
  Nutzer anders ermittelt werden (z. B. aus einem signierten Token statt aus einem Header), ist
  **nur** `aktueller_nutzer()` zu ändern.

## 6. Abnahme

```bash
pip install -r requirements.txt pytest httpx
python -m pytest -q --ignore=tests/test_models.py --ignore=tests/test_services.py \
  tests/test_analyst*.py tests/test_server_deploy.py
```

Erwartung: alle Tests grün (Stand 2026-10-30: 598). `tests/test_models.py` und
`tests/test_services.py` gehören zum Editor und haben vorbestehende Importfehler.

Danach im Browser hinter dem Proxy prüfen:
1. Video analysieren (Version V4, Ziel wählen) → erscheint unter „Meine Analysen".
2. Mit zweitem Konto einloggen → die Analyse des ersten ist **nicht** sichtbar.
3. `GET /api/analyst/meine` ohne Header → 401.
