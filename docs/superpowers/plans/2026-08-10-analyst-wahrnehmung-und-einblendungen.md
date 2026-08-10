# Abtastrate 4 fps + Einblendungs-Wächter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Modell soll schnelle Bildereignisse sehen können, und es soll keine Einblendungen mehr empfehlen, die im Video bereits existieren.

**Architecture:** Zwei unabhängige Eingriffe. (1) Das Videohandle geht nicht mehr nackt in den Bewertungs-Call, sondern als `types.Part` mit `video_metadata.fps = 4` — Gemini tastet sonst mit 1 Bild/Sekunde ab. (2) `Einblendung` bekommt ein Feld `bereits_vorhanden`; `baue_einblendungs_schritt()` filtert diese Stellen heraus und baut gar keinen Schritt mehr, wenn nichts übrig bleibt.

**Tech Stack:** Bestand. `types.VideoMetadata(fps=…)` ist in der gepinnten `google-genai==2.8.0` vorhanden (geprüft: Felder `end_offset`, `fps`, `start_offset`). Keine neue Abhängigkeit, kein `requirements.txt`.

---

## Belege, auf denen dieser Plan steht

**Zoom (Lauf `b08f73bd`, Feedback: „im ersten moment des video gibt es einen schnellen zoom. du hast ihn nicht gesehen"):** Frameanalyse von `2.8. Reel 7 Tobi.mp4` bei 10 fps ergab Bildänderung (MAD) 25,3 / 14,7 / 9,4 bei t = 0,1 / 0,2 / 0,3 s, ab 0,5 s unter 5. Der Zoom ist nach 0,4 s vorbei und liegt damit vollständig zwischen den Abtastpunkten 0,0 s und 1,0 s. Beide Läufe empfahlen daraufhin, einen Zoom einzubauen.

**Einblendungen (Läufe `7230d0f8` und `b08f73bd`, Feedback: „im video sind grafiken und sogar kleine videoeinblendungen integriert"):** Das Modell hat sie gesehen — `schnitt_pacing.kommentar` lobt sie wörtlich („Die kleinen Bildeinblendungen lockern das ansonsten starre Bild gut auf"). Der JSON-Vertrag fragt aber nur, wo eine Einblendung den Inhalt verstärken *würde*, nie ob dort schon eine ist. Messung über alle Läufe mit aktuellem Schema: `einblendungen` ist in **9 von 9** befüllt, die Empfehlung feuert also ausnahmslos. Der Docstring von `baue_effekt_schritt()` benennt dasselbe Problem bereits („Genau so ist die Einblendungs-Empfehlung zum Dauerläufer geworden") und hat deshalb einen Auslöser-Wächter — bei den Einblendungen fehlt er.

---

## File Structure

| Datei | Änderung |
|---|---|
| `services/analyst_gemini_eval.py` | `VIDEO_FPS`, Helfer `_video_part()`, drei Aufrufstellen |
| `models/analyst.py` | `Einblendung.bereits_vorhanden` |
| `services/analyst_eval.py` | JSON-Vertrag + Filter in `baue_einblendungs_schritt()` |
| `services/analyst_eval_skill.md` | Regel, wann `bereits_vorhanden` zu setzen ist |
| `tests/test_analyst.py` | zwei Tests, nur anhängen |
| `CLAUDE.md` | beide Entscheidungen dokumentieren |

---

## Task 1: Abtastrate auf 4 fps

**Files:**
- Modify: `services/analyst_gemini_eval.py`
- Test: `tests/test_analyst.py` (nur anhängen)

- [ ] **Step 1: Test schreiben** — ans Dateiende von `tests/test_analyst.py`:

```python
def test_video_part_setzt_abtastrate():
    """Gemini tastet Videos sonst mit 1 Bild/Sekunde ab. Im Lauf b08f73bd war der Eröffnungs-Zoom
    nach 0,4 s vorbei und lag vollständig zwischen den Abtastpunkten 0,0 s und 1,0 s — das Modell
    konnte ihn nicht sehen und empfahl, einen Zoom einzubauen, den es schon gab.

    Der Test prüft die Verdrahtung, nicht die Zahl: Ohne `video_metadata` am Part greift der
    Default, und der Fehler kommt geräuschlos zurück.
    """
    from types import SimpleNamespace
    from services import analyst_gemini_eval as age

    handle = SimpleNamespace(uri="files/abc123", mime_type="video/mp4")
    part = age._video_part(handle)

    assert part.video_metadata is not None, "ohne video_metadata tastet Gemini mit 1 fps ab"
    assert part.video_metadata.fps == age.VIDEO_FPS
    assert age.VIDEO_FPS >= 2, "unter 2 fps ist der Zweck der Änderung verfehlt"
    assert part.file_data.file_uri == "files/abc123"
    assert part.file_data.mime_type == "video/mp4"


def test_video_part_faellt_auf_mp4_zurueck():
    """Ein File-Handle ohne `mime_type` darf den Lauf nicht kosten — an dieser Stelle sind Whisper
    und der Gemini-Upload bereits bezahlt."""
    from types import SimpleNamespace
    from services import analyst_gemini_eval as age

    part = age._video_part(SimpleNamespace(uri="files/x", mime_type=None))
    assert part.file_data.mime_type == "video/mp4"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

```bash
cd "/sessions/happy-adoring-lamport/mnt/KI Marketing Team/ki-video-editor" && \
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u HTTP_PROXY python3 -m pytest tests/test_analyst.py -q -k video_part
```
Erwartet: FAIL mit `AttributeError: module 'services.analyst_gemini_eval' has no attribute '_video_part'`

- [ ] **Step 3: Helfer einbauen.** In `services/analyst_gemini_eval.py`, nach den Importen:

```python
# Gemini tastet Videos standardmäßig mit 1 Bild/Sekunde ab. Schnelle Bewegungen fallen damit
# zwischen zwei Abtastpunkte: Im Lauf b08f73bd war der Eröffnungs-Zoom nach 0,4 s vorbei und lag
# vollständig zwischen Frame 0,0 s und Frame 1,0 s — das Modell konnte ihn nicht sehen und empfahl
# daraufhin, einen Zoom einzubauen, den es schon gab (Feedback Chris, 2026-08-10).
# Der Preis ist linear: 4 fps sind viermal so viele Videotokens pro Call. Bei Kostendruck ist der
# nächste Hebel `media_resolution` (LOW ≈ 66 statt 258 Tokens/Frame), nicht die Rate.
VIDEO_FPS = 4.0


def _video_part(video_file):
    """Videohandle mit erhöhter Abtastrate.

    `types.Part.from_uri()` nimmt kein `video_metadata` entgegen (Signatur: file_uri, mime_type,
    media_resolution) — der Part wird deshalb direkt gebaut.
    """
    return types.Part(
        file_data=types.FileData(
            file_uri=video_file.uri,
            mime_type=getattr(video_file, "mime_type", None) or "video/mp4",
        ),
        video_metadata=types.VideoMetadata(fps=VIDEO_FPS),
    )
```

- [ ] **Step 4: Die drei Aufrufstellen umstellen**

`services/analyst_gemini_eval.py:219` (in `_evaluate`, Modus pure/hybrid):
```python
    raw = (_generate([_video_part(video_file), user], cfg, f"analyst_eval_{mode}").text or "")
```

`services/analyst_gemini_eval.py:304` (in `_evaluate_teil`, Modus split):
```python
    raw = (_generate([_video_part(video_file), user], cfg, f"analyst_eval_split_{teil}").text or "")
```

**Nicht anfassen:** `services/analyst_vlm.py:195` und `:203` (Audio- und Blick-Pass). Beide gehören zu `describe_video()` und damit zum V1-Pfad, der aus dem Frontend nicht erreichbar ist. Beim Audio-Pass wäre eine höhere Bildrate ohnehin sinnlos.

- [ ] **Step 5: Tests laufen lassen**

```bash
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u HTTP_PROXY python3 -m pytest tests/test_analyst.py tests/test_analyst_chat.py tests/test_server_deploy.py -q
```
Erwartet: 257 passed (255 vorher + 2 neue).

- [ ] **Step 6: Verdrahtung gegenprüfen** — die Aufrufstellen dürfen kein nacktes Handle mehr durchreichen:

```bash
grep -n "_generate(\[" services/analyst_gemini_eval.py
```
Erwartet: beide Zeilen enthalten `_video_part(video_file)`, keine mehr `[video_file,`.

- [ ] **Step 7: NICHT committen** (der Mensch committet selbst).

---

## Task 2: Einblendungen nur dort empfehlen, wo keine sind

**Files:**
- Modify: `models/analyst.py` (`Einblendung`)
- Modify: `services/analyst_eval.py` (JSON-Vertrag ~Zeile 1143, `baue_einblendungs_schritt`)
- Modify: `services/analyst_eval_skill.md`
- Test: `tests/test_analyst.py` (nur anhängen)

- [ ] **Step 1: Test schreiben** — ans Dateiende von `tests/test_analyst.py`:

```python
def test_einblendungen_bereits_vorhandene_werden_nicht_empfohlen():
    """Läufe 7230d0f8 und b08f73bd: An den genannten Stellen HATTE das Video bereits Grafiken und
    kleine Videoeinblendungen — das Modell hatte sie sogar gesehen und in schnitt_pacing gelobt.
    Der Vertrag fragte nur, wo eine Einblendung verstärken WÜRDE, nie ob dort schon eine ist.
    Chris dazu: „im video sind grafiken und sogar kleine videoeinblendungen integriert. das muss
    der ki auffallen können."
    """
    from models.analyst import AnalystEvaluationV2, Einblendung
    from services.analyst_eval import baue_einblendungs_schritt

    ev = AnalystEvaluationV2(einblendungen=[
        Einblendung(zeitpunkt_sek=5.0, verstaerkt="Konflikte", bereits_vorhanden=True),
        Einblendung(zeitpunkt_sek=9.5, verstaerkt="Stress", bereits_vorhanden=False),
    ])
    ev = baue_einblendungs_schritt(ev)
    schritte = [e for e in ev.empfehlungen if e.gruppe == "einblendungen"]
    assert len(schritte) == 1
    assert "9" in schritte[0].anweisung and "Stress" in schritte[0].anweisung
    assert "Konflikte" not in schritte[0].anweisung, "bestehende Einblendung wurde erneut empfohlen"


def test_einblendungen_alle_vorhanden_erzeugt_keinen_schritt():
    """Sind an allen genannten Stellen schon Einblendungen, gibt es nichts zu empfehlen. Ein
    Schritt „blende etwas ein" wäre dann nicht nur nutzlos, sondern beweist dem Nutzer, dass nicht
    hingesehen wurde — und er verbraucht einen der nur drei Top-Plätze."""
    from models.analyst import AnalystEvaluationV2, Einblendung
    from services.analyst_eval import baue_einblendungs_schritt

    ev = AnalystEvaluationV2(einblendungen=[
        Einblendung(zeitpunkt_sek=5.0, verstaerkt="Konflikte", bereits_vorhanden=True),
        Einblendung(zeitpunkt_sek=9.5, verstaerkt="Stress", bereits_vorhanden=True),
    ])
    ev = baue_einblendungs_schritt(ev)
    assert not [e for e in ev.empfehlungen if e.gruppe == "einblendungen"]


def test_altlaeufe_ohne_das_feld_verhalten_sich_wie_bisher():
    """Gespeicherte Läufe kennen `bereits_vorhanden` nicht. Der Default muss deshalb False sein —
    sonst verschwindet die Empfehlung rückwirkend aus jedem Altlauf."""
    from models.analyst import Einblendung
    assert Einblendung(zeitpunkt_sek=1.0).bereits_vorhanden is False
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Erwartet: FAIL, weil `Einblendung` kein Feld `bereits_vorhanden` kennt.

- [ ] **Step 3: Modellfeld ergänzen.** In `models/analyst.py`, Klasse `Einblendung`, nach `verstaerkt`:

```python
    bereits_vorhanden: bool = False   # True = an dieser Stelle liegt schon eine Einblendung
```

Und den Klassen-Docstring um einen Absatz ergänzen:

```
    `bereits_vorhanden` trennt Beobachtung von Empfehlung: Ohne dieses Feld nennt das Modell die
    inhaltlich stärksten Momente — und das sind genau die, an denen ein guter Cutter längst eine
    Einblendung gesetzt hat. Default False, damit gespeicherte Altläufe sich nicht rückwirkend
    ändern.
```

- [ ] **Step 4: JSON-Vertrag anpassen.** `services/analyst_eval.py`, Zeile ~1143, ersetzen durch:

```python
  "einblendungen": [{"zeitpunkt_sek": <float: Stelle, an der eine visuelle Einblendung den Inhalt verstärkt oder verstärken würde>, "verstaerkt": "<das Wort oder die Aussage, die dort verstärkt wird — z.B. Hof, Selbstbewusstsein>", "bereits_vorhanden": <bool: true, wenn an dieser Stelle im Video SCHON eine Einblendung, Grafik, ein Foto oder eine B-Roll liegt; false, wenn dort nichts ist>}],
```

- [ ] **Step 5: Filter in `baue_einblendungs_schritt()`.** In `services/analyst_eval.py`, die Zeile

```python
    stellen = sorted(parsed.einblendungen, key=lambda e: e.zeitpunkt_sek)[:EINBLENDUNGEN_MAX]
```

ersetzen durch:

```python
    # Stellen, an denen schon eine Einblendung liegt, sind Beobachtung — keine Handlung. Ohne
    # diesen Filter empfiehlt der Schritt dem Nutzer, was er bereits getan hat (Läufe 7230d0f8 und
    # b08f73bd: an 5,0 s und 9,5 s lagen Grafiken, das Modell hatte sie in schnitt_pacing gelobt).
    offen = [e for e in parsed.einblendungen if not e.bereits_vorhanden]
    if not offen:
        return parsed
    stellen = sorted(offen, key=lambda e: e.zeitpunkt_sek)[:EINBLENDUNGEN_MAX]
```

Das `if not parsed.einblendungen: return parsed` darüber bleibt stehen.

- [ ] **Step 6: Skill-Regel ergänzen.** In `services/analyst_eval_skill.md`, im Absatz „Visuelle Einblendungen gehören ins Feld `einblendungen`", nach dem Satz über die höchstens 3 Stellen einfügen:

```
  **Setz `bereits_vorhanden` bei jeder Stelle.** `true`, wenn dort im Video schon eine Einblendung,
  Grafik, ein Foto oder eine B-Roll liegt — `false` nur, wenn dort wirklich nichts ist. Der Nutzer
  bekommt einen Handlungsschritt ausschließlich für die `false`-Stellen. Eine Einblendung zu
  empfehlen, die schon da ist, ist der schlimmste Einzelfehler dieses Schritts: Sie beweist dem
  Nutzer, dass nicht hingesehen wurde, und verbraucht einen der nur drei Top-Plätze.
```

- [ ] **Step 7: `PROMPT_VERSION` hochzählen.** In `services/analyst_eval.py` von `"2026-08-08"` auf `"2026-08-10"`. Regel aus CLAUDE.md: Ohne Erhöhung ist altes Feedback später nicht von neuem unterscheidbar — und hier ändert sich das Modellverhalten inhaltlich.

- [ ] **Step 8: Tests laufen lassen**

Erwartet: 260 passed (257 nach Task 1 + 3 neue).

- [ ] **Step 9: Gegen echte Altläufe prüfen** — die Nachbearbeitung darf an gespeicherten Läufen nichts kaputtmachen:

```bash
python3 tools/replay_nachbearbeitung.py 7230d0f8 b08f73bd
```
Erwartet: Läuft ohne Fehler durch. Die Einblendungs-Empfehlung bleibt in diesen Altläufen **erhalten** — sie haben das Feld nicht, Default `False`. Das ist richtig so: Rückwirkend etwas zu ändern würde die Feedback-Historie verfälschen.

- [ ] **Step 10: NICHT committen.**

---

## Task 3: CLAUDE.md nachziehen

**Files:** `CLAUDE.md`

- [ ] **Step 1:** Im Abschnitt „Was bewusst im Code steht statt im Prompt", beim Eintrag zu `baue_einblendungs_schritt()`, ergänzen:

```markdown
  **Nachtrag 2026-08-10:** Stellen mit `bereits_vorhanden=true` werden herausgefiltert; bleibt nichts
  übrig, entsteht kein Schritt. Vorher fragte der Vertrag nur, wo eine Einblendung verstärken *würde* —
  das Modell nannte daraufhin die inhaltlich stärksten Momente, also genau die, an denen der Nutzer
  längst eine gesetzt hatte. Gemessen: `einblendungen` war in 9 von 9 Läufen mit aktuellem Schema
  befüllt, die Empfehlung feuerte also ausnahmslos (Feedback 7230d0f8).
```

- [ ] **Step 2:** Neuer Abschnitt unter „Analyst-Pipeline":

```markdown
### Abtastrate des Videos (seit 2026-08-10)

Der Bewertungs-Call übergibt das Video als `types.Part` mit `video_metadata.fps = VIDEO_FPS`
(`services/analyst_gemini_eval.py`), aktuell **4**. Ohne das tastet Gemini mit **1 Bild/Sekunde** ab.

Warum: Im Lauf `b08f73bd` war der Eröffnungs-Zoom nach 0,4 s vorbei (gemessen bei 10 fps: MAD 25,3 /
14,7 / 9,4 bei 0,1 / 0,2 / 0,3 s) und lag vollständig zwischen den Abtastpunkten 0,0 s und 1,0 s. Das
Modell konnte ihn nicht sehen und empfahl, einen Zoom einzubauen, den es schon gab.

**Der Preis ist linear:** 4 fps sind viermal so viele Videotokens pro Call, und `v2_split` schickt das
Video zweimal. Bei Kostendruck ist der nächste Hebel `media_resolution` (LOW ≈ 66 statt 258 Tokens pro
Frame), nicht das Zurückdrehen der Rate — die Rate löst genau das Wahrnehmungsproblem.

**Nicht betroffen:** `analyst_vlm.py` (Audio- und Blick-Pass) gehört zum V1-Pfad und bleibt bei 1 fps.
```

- [ ] **Step 3: NICHT committen.**

---

## Self-Review

**Abdeckung:**

| Anforderung (Chris, 2026-08-10) | Task |
|---|---|
| 4 fps über das gesamte Video | 1 |
| Einblendungs-Empfehlung nur wo nötig | 2 |
| Entscheidungen dokumentiert | 3 |

**Typkonsistenz:** `_video_part(video_file)` und `VIDEO_FPS` sind je einmal in `analyst_gemini_eval.py` definiert und an zwei Aufrufstellen plus zwei Tests identisch verwendet. `Einblendung.bereits_vorhanden: bool = False` ist einmal im Modell definiert und wird in `baue_einblendungs_schritt()`, im JSON-Vertrag, im Skill und in drei Tests unter demselben Namen verwendet.

**Keine Platzhalter.**
