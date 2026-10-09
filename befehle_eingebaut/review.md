---
description: Prüft die aktuellen Git-Änderungen auf echte Fehler, ohne etwas zu ändern
argument-hint: [basis-branch]
---
Führe ein Code-Review durch. Ändere dabei keine Datei.

1. Ermittle die Änderungen. Basis: „$1“ — ist sie leer, prüfe die uncommitteten Änderungen (`git diff HEAD`), sonst den Branch gegen diese Basis (`git diff <Basis>...HEAD`).
2. Lies zu jeder geänderten Stelle genug vom umgebenden Code, um sie wirklich zu verstehen.
3. Suche nach echten Fehlern: falsches Verhalten, Abstürze, Randfälle, Sicherheitslücken, fehlende Fehlerbehandlung, kaputte Aufrufer. Keine Stilfragen.
4. Führe vorhandene Tests aus, wenn es sie gibt.

Melde mit „fertig“: jeden Fund mit Datei und Zeile, einem konkreten Szenario, in dem er schiefgeht, und wie sicher du dir bist. Ohne echte Funde sag genau das.
