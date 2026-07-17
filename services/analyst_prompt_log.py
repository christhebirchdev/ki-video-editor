# services/analyst_prompt_log.py
"""
Nachvollziehbarkeits-Log für den AI Video Analyst.

Schreibt pro Analyse-Lauf EINE Markdown-Datei `prompt_log.md` in den Run-Ordner
(`analyst_runs/<run_id>/prompt_log.md`). Für jeden LLM-Call wird festgehalten:
- INPUT: was in den Prompt floss (Dateiname, Engine, Messwerte, Anhänge …),
- PROMPT: der exakte System-Prompt + die exakte User-Message,
- OUTPUT: die rohe LLM-Antwort (und, falls vorhanden, das geparste JSON).

So ist für jedes analysierte Video später exakt reproduzierbar, WAS der Input war,
WIE der Prompt aussah und WAS herauskam.

Design-Regel: Logging darf eine Analyse NIEMALS zum Absturz bringen. Jeder Aufruf
ist vollständig in try/except gekapselt; ein Fehler beim Loggen wird nur auf stdout
gemeldet und ansonsten verschluckt.

ponytail: bewusst nur Datei-Append, keine DB/kein Framework — ein Markdown pro Video,
das neben analysis.json/meta.json liegt.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

LOG_NAME = "prompt_log.md"


def _fence(text: str, lang: str = "text") -> str:
    """Bettet Text als Codeblock ein. Nutzt eine Fence-Länge, die im Text nicht vorkommt,
    damit dreifache Backticks/Tilden im Inhalt (z.B. Markdown im Skill) nicht ausbrechen."""
    s = "" if text is None else str(text)
    # Wähle ein Tilde-Fence, das länger ist als jede Tilde-Sequenz im Inhalt.
    longest = 0
    run = 0
    for ch in s:
        if ch == "~":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    fence = "~" * max(3, longest + 1)
    return f"{fence}{lang}\n{s.rstrip()}\n{fence}"


def _kv_block(inputs: Optional[dict[str, Any]]) -> str:
    if not inputs:
        return "_(keine)_"
    lines = []
    for k, v in inputs.items():
        if v is None or v == "":
            continue
        val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        lines.append(f"- **{k}:** {val}")
    return "\n".join(lines) if lines else "_(keine)_"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_header(log_path: Path, run_dir: Path) -> None:
    """Schreibt den Datei-Kopf genau einmal (beim ersten Call eines Laufs)."""
    if log_path.exists():
        return
    filename = engine = created = ""
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        filename = meta.get("filename", "")
        engine = meta.get("engine", "")
        created = meta.get("created_at", "")
    except Exception:
        pass
    head = (
        f"# Prompt-Log — Run `{run_dir.name}`\n\n"
        f"- **Video:** {filename or '(unbekannt)'}\n"
        f"- **Engine:** {engine or '(unbekannt)'}\n"
        f"- **Upload:** {created or '(unbekannt)'}\n"
        f"- **Log erstellt:** {_now()}\n\n"
        "> Jeder Abschnitt = ein LLM-Call dieses Laufs, in Aufrufreihenfolge.\n"
        "> Input → Prompt (System + User) → Output.\n\n"
        "---\n\n"
    )
    log_path.write_text(head, encoding="utf-8")


def _next_index(log_path: Path) -> int:
    """Fortlaufende Call-Nummer je Lauf (zählt bestehende '## Call'-Überschriften)."""
    try:
        return log_path.read_text(encoding="utf-8").count("\n## Call ") + 1
    except Exception:
        return 1


def log_call(
    run_dir: Optional[Path | str],
    *,
    call: str,
    recipient: str,
    model: str = "",
    system_prompt: str = "",
    user_message: str = "",
    output_raw: str = "",
    output_parsed: Any = None,
    attachments: Optional[list[str]] = None,
    inputs: Optional[dict[str, Any]] = None,
) -> None:
    """Hängt einen Call-Abschnitt an prompt_log.md an. Crash-sicher (verschluckt Fehler).

    Args:
        run_dir: Ordner des Laufs (analyst_runs/<id>). None/leer → No-Op (Logging deaktiviert).
        call: Kurzname des Calls (z.B. "eval_hybrid", "describe_frames").
        recipient: "Gemini" oder "Claude".
        model: konkretes Modell (z.B. settings.claude_model / GEMINI_MODEL).
        system_prompt: exakter System-Prompt / system_instruction.
        user_message: exakte User-Message (Text-Teil des Calls).
        output_raw: rohe Textantwort des Modells.
        output_parsed: optional das geparste Ergebnis (dict/pydantic) → als JSON geloggt.
        attachments: nicht-Text-Anhänge, die mitgeschickt wurden (z.B. ["Video: x.mp4", "17 Frames"]).
        inputs: knappe Zusammenfassung der Eingangsdaten (Dateiname, Engine, Messwerte …).
    """
    try:
        if not run_dir:
            return
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / LOG_NAME
        _ensure_header(log_path, run_dir)
        idx = _next_index(log_path)

        parsed_txt = ""
        if output_parsed is not None:
            try:
                if hasattr(output_parsed, "model_dump"):       # pydantic v2
                    obj = output_parsed.model_dump()
                elif hasattr(output_parsed, "dict"):           # pydantic v1
                    obj = output_parsed.dict()
                else:
                    obj = output_parsed
                parsed_txt = json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception as e:
                parsed_txt = f"(konnte geparstes Ergebnis nicht serialisieren: {e})"

        att = ", ".join(attachments) if attachments else "_(keine)_"

        section = [
            f"\n## Call {idx} — {call} → {recipient}"
            + (f" ({model})" if model else "")
            + f"  ·  {_now()}\n",
            "### Input (was in den Prompt floss)\n",
            _kv_block(inputs) + "\n",
            f"**Anhänge (nicht-Text):** {att}\n",
            "### System-Prompt\n",
            _fence(system_prompt) + "\n",
            "### User-Message\n",
            _fence(user_message) + "\n",
            "### Output — roh\n",
            _fence(output_raw) + "\n",
        ]
        if parsed_txt:
            section += ["### Output — geparst (JSON)\n", _fence(parsed_txt, "json") + "\n"]
        section.append("\n---\n")

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(section))
    except Exception as e:  # Logging darf die Analyse nie brechen
        print(f"  [PROMPT-LOG] WARN: Konnte Call '{call}' nicht loggen: {e}")
