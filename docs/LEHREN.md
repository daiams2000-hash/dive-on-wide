# Lehren aus den Messläufen — für Agenten, die neu dazukommen

Dieses Dokument ist für jemanden geschrieben, der **nichts** von den Läufen im September 2026 weiß: ein frischer Agent, ein neuer Mitarbeiter, ein fremdes Gerüst, das Dive on Wide ansteuert. Jede Regel steht hier mit der Messung, aus der sie stammt — ohne die Zahl wäre sie nur eine Meinung.

Kurzfassung derselben Regeln als Notizen im Werkbank-Gedächtnis: Sie stehen in jedem Agentenlauf automatisch im Systemprompt. Hier steht, **warum**.

---

## 1. Der Verlust lügt. Nur die Aufgabe sagt die Wahrheit.

Ein Adapter erreichte Validierungsverlust **14,617 → 0,342**, Train- und Val-Kurve deckungsgleich — nach Lehrbuch ein Erfolg. Gemessen an der Aufgabe: **28 % → 29 %** Zuggenauigkeit, **0 von 80** Puzzles gelöst. Der Verlust maß die trivialen Vorlagen-Token (`<think></think>`, Zeilenumbrüche), nicht die eine Entscheidung, auf die es ankam.

Später zeigte sich dasselbe im Kleinen: Zwischen „lernt nichts" (Verlust 0,343) und „löst 21 %" (0,263) liegen **0,08** — auf der Kurve unsichtbar.

**Anwendung:** Kein Trainingsergebnis ohne Messung an der Aufgabe. Eine Verlustkurve ist ein Betriebszustand, kein Ergebnis.

## 2. Wer nur perfekte Lösungswege lernt, kann sich nicht fangen.

Trainingsdaten nur aus Optimalpfaden: **29 %** Zuggenauigkeit, **0** gelöste Puzzles. Dieselbe Menge, aber mit Zuständen **abseits** des Pfads (absichtlich falsch abbiegen, dann das Orakel erneut fragen): **64 %** und 13 % gelöst. Ein Modell, das nie einen Stand nach dem eigenen Fehler gesehen hat, ist nach dem ersten Fehltritt blind.

**Anwendung:** In jeden Datensatz gehören Erholungen. Beim Rejection Sampling (`ablehnung.py`): den Fehlgriff selbst **nicht** lernen, den Schritt danach schon — mit dem Fehler als Vorgeschichte.

## 3. Die Anweisung frisst die Aufgabe.

In einem Sokoban-Datensatz waren **137 von 174 Token (79 %)** die immer gleiche Systemzeile. Kürzung auf 43 Token, sonst nichts geändert: **+45 Prozentpunkte** gelöste Puzzles. Längeres Training (2000 statt 1200 Iterationen) brachte im selben Aufbau nur **+22**.

**Anwendung:** Vor jedem Training den Anteil der Anweisung an einem Median-Beispiel messen. Über ~30 % ist ein Warnzeichen. Dieselbe Zeile muss bei Training und Messung stehen, sonst misst man die Anweisung statt das Modell. (Werkbank-Daten: 808 von 1856 Token = 44 % — offen.)

## 4. Der Sprung kommt spät. Nicht früh abbrechen.

Vier Läufe saßen bis Iteration 600–900 bei Verlust 0,343 fest, drei brachen danach aus und lösten 9–21 %. Eine Zwischenmessung an der Aufgabe zeigte bei Iteration 300 und 750 jeweils **0/20** — und derselbe Lauf endete bei **4/20**. Ein früher Abbruch anhand der Aufgabenmessung hätte den besten Lauf getötet.

**Anwendung:** Zwischenstände beobachten ja, daraus abbrechen nein.

## 5. Ein Drittel der Läufe kann nichts lernen — das ist kein Rauschen.

Serie A: **2 von 6** Läufen blieben im Plateau (0 gelöst). Nach Kürzung der Anweisung: **0 von 6**. Instabilität hat eine Ursache, und sie ist auffindbar.

**Anwendung:** Mehrere Seeds sind Pflicht. Ein einzelner Lauf ist eine Anekdote.

## 6. Erst messen, dann erklären — und die eigene Erklärung prüfen.

Nach Serie B lautete die Erklärung für die toten Läufe „zu kurz trainiert", und die vorab gestellte Vorhersage traf ein. Serie C, die **genau einen** Faktor zurückdrehte, zeigte: Die toten Läufe verschwinden schon ohne eine einzige zusätzliche Iteration. Die Vorhersage stimmte, die Begründung war falsch.

**Anwendung:** Nie zwei Dinge gleichzeitig ändern, wenn die Zuschreibung zählt. Wurde es doch getan, die einfaktorielle Gegenprobe nachholen.

## 7. Entscheidungsregel vor dem Lauf aufschreiben.

Jede Serie hat eine `voranmeldung.md` mit Protokoll, Hypothese, **der Vorhersage, an der sie scheitern kann**, und der Entscheidungsregel (PASS nur, wenn die untere Grenze des 95-%-Intervalls über 0 liegt). Ergebnis eines Laufs: PASS +8,9 Punkte, Intervall [+0,5 ; +17,3] — knapp, und genau so berichtet.

**Anwendung:** Wer die Regel erst nach den Zahlen formuliert, hat keine Regel.

## 8. Stille ist kein Erfolg.

Ein Datensatz-Generator ohne Fortschrittsausgabe und ohne Versuchsgrenze verlangte 4000 verschiedene Puzzles aus einem Raum, der nach ~1500 erschöpft war: **82 Minuten bei 99 % CPU im Leerlauf**. Mit breiterem Raum, Fortschritt und Grenze: **2 Sekunden**.

**Anwendung:** Jeder Lauf über einer Minute bekommt Fortschritt und eine harte Grenze — auch Wegwerf-Skripte. Bei Verdacht auf Hänger die **CPU-Zeit** ansehen, nicht den Speicher: Eine Endlosschleife verbrennt einen Kern und null RAM.

## 9. Was gemessen wird, wird nicht trainiert — und das wird erzwungen.

Prüfsätze werden nach Fingerabdruck getrennt, Prüfzustände stehen auf einer Sperrliste, und `ablehnung.sammeln` **verweigert** gesperrte Aufgaben, statt sie stillschweigend zu filtern. Wer den Prüfsatz mittrainieren will, soll es merken.

## 10. Messfehler sehen aus wie Modellfehler.

Drei echte Fälle: Ein Token-Deckel von 4 schnitt die Antwort ab (80 „unlesbare" Antworten waren gültige Züge). Ein ungültiger Zug ließ das Brett stehen, das Modell lief bei Temperatur 0 bis zum Budget gegen dieselbe Wand (aus 80 Fehlern wurden 1404 gezählte). Eine Beobachter-Wache kopierte alle Zwischenstände in denselben Ordner, der Server hielt den ersten Adapter fest — sie meldete überall 0/20.

**Anwendung:** Bei einem überraschend schlechten Ergebnis zuerst **eine Rohantwort ansehen**, bevor das Modell beschuldigt wird.

---

## Belege

| Wo | Was |
|---|---|
| `docs/ERSTES_ERGEBNIS.md` | Die drei Sokoban-Serien mit allen Zahlen und der Zuschreibung |
| `storage/datenwert/sokoban*/bericht.md` | Maschinell erzeugte Berichte je Serie |
| `storage/datenwert/*/voranmeldung.md` | Die vorab festgelegten Regeln |
| `docs/TUEV.md` | Verseuchung, Abnahmefahrt, Längen-Doktor |
| `spiel.py`, `ablehnung.py`, `datenwert.py` | Die Werkzeuge, mit denen das gemessen wurde |
