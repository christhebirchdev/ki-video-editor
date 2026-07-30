# Hook-Regeln: Struktur A→B→C→D + zwei Pflichtfelder

Anlass: „die texthook war bei einem anderen run nicht gut und hat nicht getriggert oder
neugierde erzeugt" — das Modell vergab gute Scores für Text, der sauber, lesbar und thematisch
passend war, aber keinen Haken setzte.

## Diagnose

Die Hook-Regeln waren nach Fehlerfällen gewachsen, nicht nach Entscheidungsweg. Der Score stand
faktisch vor der Prüfung: Gestaltung, Länge, Untertitel-Abgrenzung und Redundanz waren über acht
Absätze verteilt, die Frage „wirkt der Hook überhaupt?" kam nirgends zuerst. Alle Kriterien waren
zudem *abwesenheitsbasiert* — sie sagten, was einen Score senkt, nicht, was einen Hook ausmacht.
Ein Text ohne Mängel bekam damit eine 4.

## Struktur

Der Abschnitt folgt jetzt dem Entscheidungsweg, der Score fällt erst in C:

| Schritt | Frage | Inhalt |
|---|---|---|
| **A** | Was ist da? | `text_hook_wortlaut` immer wörtlich erfassen |
| **B** | Zählt es als Hook? | grafischer Inhalt, Untertitel, die zwei Score-0-Fassungen |
| **C** | Wie stark ist es? | Kerntest, Pflichtfelder, Kriterien, Anker, Text-/Sprech-Spezifika, Redundanz |
| **D** | Was empfiehlst du? | Gestaltungs-Checkliste → `texthook_maengel`, `texthook_varianten` |

## Der Kerntest

„Öffnet er, oder beschreibt er?" steht vor allen anderen Kriterien. Dazu drei Kriterien, die
Stärke positiv beschreiben statt Mängel aufzuzählen: **Einsatz** (was gewinnt/verliert der
Zuschauer), **Trennschärfe** (trifft die Zielgruppe UND sortiert andere aus), **Konkretheit**.

## Zwei Pflichtfelder mit Deckel

`*_offene_frage` und `*_mechanik` (Enum `HOOK_MECHANIKEN`) für BEIDE Hooks. Sie sind
Forcing Functions: wer keine offene Frage formulieren kann, hat keinen Haken gefunden.

`deckle_hooks_ohne_haken()` in `nachbearbeiten()` setzt den Score auf höchstens 2, wenn die
offene Frage fehlt (< 3 Wörter) oder die Mechanik `keine` ist. Score 0 bleibt unberührt —
„gar keine Text-Hook" ist keine schwache Hook. Der Deckel greift vor
`erzwinge_hook_empfehlungen`, damit die dadurch entstandene Schwäche auch eine Empfehlung auslöst.

Warum Code und nicht nur Prompt: Der Widerspruch „keine offene Frage, trotzdem Score 4" ist
mechanisch prüfbar. Prompt-Regeln, die das Modell gegen sich selbst durchsetzen soll, halten
erfahrungsgemäß nicht.

## Nicht verifiziert

Ob das Modell `*_offene_frage` und `*_mechanik` verlässlich füllt, zeigt erst der Lauf. Bleiben
die Felder oft leer, greift der Deckel zu breit und die Schwelle muss weicher werden.
