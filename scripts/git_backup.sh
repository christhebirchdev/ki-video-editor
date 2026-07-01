#!/bin/bash
# Auto-Backup: sichert den aktuellen Stand nach GitHub. Stündlich von launchd aufgerufen.
# ponytail: simpelst möglich — commit nur wenn es was zu sichern gibt, push best-effort.
REPO="/Users/chris/Documents/Claude/Projects/KI Marketing Team/ki-video-editor"
cd "$REPO" || exit 1

git add -A
if git diff --cached --quiet; then
  echo "$(date '+%F %T') nichts zu sichern"
  exit 0
fi

git commit -m "auto-backup $(date '+%F %T')" >/dev/null 2>&1
if git push origin main >/dev/null 2>&1; then
  echo "$(date '+%F %T') OK — committed + gepusht"
else
  echo "$(date '+%F %T') committed LOKAL, PUSH FEHLGESCHLAGEN (GitHub-Credentials prüfen)"
fi
