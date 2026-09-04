# Video-Analyst per Reverse-Proxy anbinden — Übergabe an IT

Stand: 2026-09-04, verifiziert gegen den aktuellen Code-/Deploy-Stand (nicht gegen Doku).

## Ausgangslage

Der AI Video Analyst läuft produktiv auf einem eigenen VPS, unverändert seit 01.07.:

| | |
|---|---|
| Domain | `analyst.srv1691345.hstgr.cloud` |
| Server | Hostinger-VPS, 2 vCPU / 7,8 GB RAM, **geteilt** mit weiteren Diensten (n8n u.a.) |
| Reverse Proxy davor | Traefik, `network_mode: host` |
| TLS | Let's Encrypt via Traefik (HTTP-Challenge, Port 80 muss offen bleiben) |
| Zugriffsschutz | **Keiner mehr** (Entscheidung Chris, 2026-09-04, siehe Abschnitt unten) |
| Kapazität | `ANALYST_MAX_CONCURRENT=1` → **eine** Analyse läuft, der Rest wartet seriell in der Warteschlange |
| Frontend | Statisches React (kein Build-Step), alle Asset- und API-Pfade sind **wurzel-relativ** (`/static/…`, `/api/analyst/…`) |

Ziel: euer System soll den Analyst per Reverse-Proxy erreichbar machen, ohne dass Nutzer merken, dass es ein anderer Server ist.

---

## Entscheidung, die ihr treffen müsst: Subdomain oder Unterpfad

Das ist der einzige Punkt, der bestimmt, ob wir vorher noch etwas am Frontend ändern müssen.

| | Eigene Subdomain (`analyst.euredomain.de`) | Unterpfad (`euredomain.de/videoanalyse`) |
|---|---|---|
| Funktioniert mit dem Frontend wie es ist | Ja, sofort | **Nein** |
| Warum | Wurzel-relative Pfade lösen sich gegen die aktuelle Domain auf — bei eigener Subdomain ist das automatisch die richtige | `/static/styles.css` und `/api/analyst/…` lösen sich gegen `euredomain.de/` auf, nicht gegen `/videoanalyse/` → 404 bzw. fehlschlagende API-Calls |
| Aufwand bei uns | Keiner | Wir müssen vorher einen konfigurierbaren Pfad-Präfix einbauen (Backend-Routing + Frontend-Basispfad). **Bitte vorher mit uns abstimmen, bevor ihr den Proxy live schaltet** — sonst bricht die Seite beim ersten Test. |
| Empfehlung | **Bevorzugt**, wenn technisch möglich | Nur falls organisatorisch zwingend (z.B. Einbettung in ein bestehendes Login-System) |

---

## Pflicht-Konfiguration am Proxy (gilt für beide Varianten)

1. **Host-Header exakt setzen.** Traefik auf unserer Seite routet nach `Host: analyst.srv1691345.hstgr.cloud`. Reicht euer Proxy eure eigene Domain im Host-Header durch, findet unser Traefik keinen Router → 404.

   *nginx-Beispiel:*
   ```nginx
   location / {
       proxy_pass https://analyst.srv1691345.hstgr.cloud;
       proxy_set_header Host analyst.srv1691345.hstgr.cloud;
       proxy_ssl_server_name on;
   }
   ```

2. **Upload-Limit hochsetzen.** Videos sind groß, nginx blockt standardmäßig bei 1 MB.
   ```nginx
   client_max_body_size 2g;
   proxy_request_buffering off;
   ```
   Ohne `proxy_request_buffering off` schreibt euer Server jedes Video erst komplett auf die eigene Platte, bevor es weitergeht — unnötig langsam bei großen Dateien.

3. **Timeouts hoch für Upload + Analyse.** `proxy_send_timeout` / `proxy_read_timeout` großzügig setzen (mehrere Minuten), sonst bricht ein langer Upload oder eine lange Analyse ab.

4. **Streaming-Endpoint nicht puffern.** `/api/analyst/{id}/chat` streamt die Antwort. Unser Server setzt bereits `X-Accel-Buffering: no` — euer Proxy muss das respektieren (bei nginx automatisch über den Header, bei Caddy/Traefik ggf. eigene Einstellung prüfen). Sonst kommt die Chat-Antwort erst komplett am Ende statt live.

---

## Zugriffsschutz — liegt jetzt komplett bei euch

**Update 2026-09-04:** Die bisherige Basic-Auth auf unserer Seite wurde entfernt. `analyst.srv1691345.hstgr.cloud` ist damit ohne jede Zugriffskontrolle erreichbar — für jeden, der die Adresse kennt, nicht nur für euren Proxy. Es gibt keine zweite Schutzschicht mehr, die einen Konfigurationsfehler bei euch auffängt.

**Das heißt: eure Nutzer-Authentifizierung ist die einzige verbleibende Hürde.** Sie muss **serverseitig bei jedem Request** an die proxied Route geprüft werden, nicht nur als Weiche in eurem Frontend (eine Login-Seite verhindert nicht, dass jemand die proxied URL — oder die Analyst-Domain direkt — ohne Login aufruft). Bitte vor Live-Schaltung bestätigen, wie das bei euch technisch durchgesetzt ist — ohne das ist der Analyst faktisch öffentlich, inklusive Upload-Funktion und Verbrauch unserer bezahlten Gemini-/Claude-/Whisper-Kontingente.

---

## Kapazitätsgrenze — bitte einplanen

`ANALYST_MAX_CONCURRENT=1`: Es läuft immer nur **eine** Analyse gleichzeitig, unabhängig davon, wie viele Nutzer über den Proxy kommen. Mehr Zugriff heißt längere Warteschlange, nicht mehr Durchsatz. Bei spürbarem Bedarf sprechen wir über eine Anpassung — das ist eine Ressourcenfrage auf unserem VPS, keine Schnittstellenfrage.

---

## Testreihenfolge (wichtig — in dieser Reihenfolge, nicht direkt im Browser anfangen)

1. `curl -I https://<eure-domain>/health` → sollte `200` liefern (beweist: Routing + Host-Header korrekt)
2. Gleicher Call **ohne** Login bei euch (z.B. Inkognito-Fenster) → sollte scheitern/umleiten, wenn eure Zugriffskontrolle greift. Kommt trotzdem eine Antwort vom Analyst durch, ist eure Route ungeschützt — nicht weitermachen, bis das geklärt ist.
3. Kleiner Test-Upload (wenige MB) über den Proxy
4. Erst danach: volle Seite im Browser öffnen
5. Zuletzt: Chat-Funktion testen (einziger Streaming-Endpoint, reagiert empfindlich auf Puffer-Einstellungen)

## Fehlerbilder

| Symptom | Wahrscheinliche Ursache |
|---|---|
| 404 auf allem | Host-Header falsch/fehlt |
| Seite lädt, kein Styling | Unterpfad-Fall — Pfade lösen sich falsch auf, siehe oben |
| 413 beim Upload | `client_max_body_size` zu niedrig |
| Upload bricht bei großen Dateien ab | Timeout zu kurz oder `proxy_request_buffering` an |
| Chat-Antwort kommt erst am Ende statt live | Puffer-Einstellung am Proxy, `X-Accel-Buffering` wird nicht respektiert |
| Analyst antwortet auch ohne Login bei euch | Eure Zugriffskontrolle greift nicht serverseitig auf der proxied Route — dringend fixen, es gibt keine zweite Schutzschicht mehr |

---

## Was NICHT gebraucht wird

Explizit, damit es nicht doppelt gebaut wird: kein CORS, kein API-Key-System, keine eigene/versionierte API. Der Reverse-Proxy macht das überflüssig — alles läuft aus Sicht des Browsers auf einer Domain.

---

## Offene Punkte, bevor es losgeht

1. Subdomain oder Unterpfad? (siehe oben — bestimmt, ob wir vorher etwas bauen müssen)
2. Wie wird die Nutzer-Authentifizierung serverseitig durchgesetzt? **Das ist jetzt der einzige Schutz, den der Analyst noch hat — bitte vor Live-Schaltung schriftlich bestätigen.**
