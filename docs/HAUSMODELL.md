# Das Hausmodell — ein Modell, das Dive on Wide von innen kennt

Stand: 26.09.2026. Schritte 0–5 gebaut und gemessen; Hausmodell v1 besteht (Abschnitte „Gemessen“ am Ende).

## Die Frage

Lässt sich mit den vorhandenen Mitteln (ein Mac mit 24 GB, ein 4-B-Schüler, lokale Lehrermodelle) ein Modell trainieren, das im Harness von Dive on Wide zu Hause ist — das als Orchestrator weiß, welche Modelle es gibt und wofür, welche Werkzeuge, Skills und Regeln gelten, und daraus Abläufe baut, die wirklich laufen?

**Ja — und dieser Bereich ist besser trainierbar als Code.** Der Grund steht in den bisherigen Messungen: Wo es einen perfekten Schiedsrichter gab (Sokoban, Breitensuche), ging es von 0 % auf 80 %. Wo es nur einen halben gab (Codeaufgaben, versteckte Tests), blieben die Gewinne klein und unbelegt. Fast jede Entscheidung eines Orchestrators ist dagegen **prüfbar**: Gibt es das Modell? Passt es in den Speicher? Kann es Bilder sehen? Ist der Schritttyp für diese Anfrage der richtige? Dieselbe Lage wie bei Sokoban, nicht wie bei Code.

## Befund: Der Orchestrator sieht heute nur Namen

`run_orchestrator` übergibt die Modelle so:

```
- qwen2.5-coder:14b (Provider: Ollama)
- gemma4-finetuned:latest (Provider: Ollama)
```

Keine Größe, keine Fähigkeiten, keine Aussage darüber, ob ein Modell in den Speicher passt. Bei `gemma4-finetuned:latest` muss er raten. Dass er dabei Namen erfindet, belegt der Code selbst: Es gibt eine eigene Korrekturstufe, weil „der Planer oft nur `qwen2.5-coder` statt `ollama@@qwen2.5-coder:14b` schreibt".

Ollama liefert aber alles, was fehlt (nachgesehen mit `/api/show`):

| Modell | Fähigkeiten | Familie | Parameter | Kontext |
|---|---|---|---|---|
| qwen2.5-coder:14b | completion, tools, **insert** | qwen2 | 14,8 B | 32 k |
| gemma4:12b | completion, **vision**, audio, tools, thinking | gemma4 | 11,9 B | 262 k |
| qwen3.6-35b-a3b | tools, **thinking**, completion | qwen35moe | 34,7 B | 262 k |

**Schritt 0 ist deshalb keine Trainingsfrage, sondern eine Harnessfrage:** Der Orchestrator bekommt einen echten Katalog — Fähigkeiten, Größe, passt/passt nicht, Familie, Kontext, und wo vorhanden die gemessene Güte je Aufgabenart aus dem Prüfstand. Das verbessert **jedes** Modell sofort, auch das Standardmodell. Und es legt das Format fest, das das Hausmodell lesen lernt.

## Das eine Prinzip: lesen lernen, nicht auswendig lernen

Ein Modell, das lernt „`qwen2.5-coder:14b` ist der Coder", ist auf jedem anderen Rechner nutzlos, und auf diesem, sobald ein Modell dazukommt. Es muss lernen, **den Katalog zu lesen, der vor ihm liegt**, und daraus zu entscheiden.

Deshalb bekommt jedes Trainingsbeispiel einen **zufällig zusammengesetzten Katalog**: andere Namen, andere Größen, andere Speichergrenzen, manchmal kein Coder, manchmal kein Bildmodell, manchmal nur ein einziges Modell. Gemessen wird auf Katalogen mit **zurückgehaltenen Modellfamilien** und Namen, die im Training nie vorkamen. Besteht das Modell dort, hat es die Fertigkeit gelernt und nicht die Fakten.

## Was trainiert wird, und wer schiedsrichtert

| Fertigkeit | Schiedsrichter (Code, kein Modell) |
|---|---|
| Nur Modelle wählen, die es gibt | Name steht im gegebenen Katalog — sonst Fehler |
| Das passende Modell je Schritt | Code-Schritt → Modell mit `insert` oder Coder-Familie, falls vorhanden; Bild → `vision`; schweres Schlussfolgern → größtes passendes mit `thinking` |
| Nichts wählen, was nicht in den Speicher passt | `modell_passt` aus der Einrichtung |
| Den richtigen Ablauf je Anfrage | Die Anfrage wird aus einer Spezifikation erzeugt, die erlaubte und verbotene Schritttypen festlegt (Recherche → kein `code`; `web` und `research` nie zusammen; so wenige Schritte wie möglich) |
| Gültiges Format | JSON-Schema des Orchestrators |
| Werkzeuge, Skills, Befehle, Profile aus dem Katalog wählen | Der Name steht im gegebenen Katalog |
| Freigabe und Regeln verstehen | `regeln.entscheidung` und `braucht_freigabe` liefern die richtige Antwort |
| Sich nach einem Fehler fangen | Korrekturdialog: Der Harness meldet „Modell X gibt es nicht", das Hausmodell bessert nach |

Die letzte Zeile ist die Lehre aus Sokoban: Wer nur fehlerfreie Wege lernt, kann sich nach dem ersten Fehltritt nicht fangen. Die Korrekturdialoge gehören in jeden Datensatz.

Der stärkste Schiedsrichter kommt zuletzt: **den Plan wirklich ausführen**. Läuft der Ablauf durch und liefert er das Verlangte? Das ist teuer und deshalb nur für eine Stichprobe gedacht.

## Woher die Daten kommen — und woher nicht

**Keine Texte eines Claude-Modells im Training.** Das gilt seit Beginn und wird im Export mit `training_erlaubt=False` erzwungen. Konkret heißt das:

- **Generatoren und Schiedsrichter** sind Programmcode. Sie erzeugen Kataloge, Spezifikationen und prüfen Antworten. Programmcode ist kein Trainingsdatum.
- **Die Formulierungen der Aufträge** („Mach mir eine Übersicht über …") stammen aus der **echten Nutzung** von Dive on Wide (Chats, Orchestrator-Läufe) oder werden vom **lokalen Lehrermodell** (Qwen3.6-35B) aus der Spezifikation umformuliert.
- **Die Antworten** (die Pläne) entstehen durch **Ablehnungsstichprobe**: Lokale Modelle schlagen vor, der Schiedsrichter lässt nur Bestandenes durch — Label `GOLD_EXECUTION`, wie bei `ablehnung.py`. Wo ein Plan rein strukturell ist (Schritttypen, Modellwahl), kann ihn auch ein deterministischer Löser bauen, wie die Breitensuche bei Sokoban.

## Was nicht trainiert, sondern nachgeschlagen wird

Fakten über Dive on Wide, die sich ändern — welche Einstellung was bewirkt, welcher Knopf wo ist —, gehören **nicht** ins Gewicht, sondern in die Wissensbasis, aus der zur Laufzeit gelesen wird. Ein trainiertes Faktum veraltet mit der nächsten Version; ein nachgeschlagenes nicht. Trainiert werden **Fertigkeiten**: lesen, wählen, planen, sich korrigieren.

## Messung

Ein eigener Harness-Prüfstand, zurückgehalten wie beim Agentenbereich:

- **Erfundene Namen je 100 Pläne** — die wichtigste Zahl, weil ein erfundener Name einen Lauf sofort scheitern lässt
- **Modellwahl richtig** nach dem Schiedsrichter
- **Ablauf richtig** (Schritttypen gegen die Spezifikation)
- **Plan läuft wirklich durch** (Stichprobe)

Vergleich: das Grundmodell, das heutige Standardmodell als Orchestrator, und das Hausmodell, mindestens fünf Seeds, Voranmeldung vor dem Lauf, PASS nur bei unterer Intervallgrenze über null.

**Günstiger als der Agentenbereich:** Ein Orchestrator-Beispiel ist eine Anfrage und ein Plan, keine Kette aus fünfzehn Schritten. Die Beispiele sind kurz — also kürzeres `max_seq`, schnelleres Training, mehr Seeds in derselben Nacht. Und eine Messung ist ein einziger Aufruf je Aufgabe statt eines ganzen Agentenlaufs.

## Einsatz

Dive on Wide kann einen Schüler mit Adapter schon als Anbieter bedienen. Es fehlt eine eigene Rolle **Orchestrator-Modell** (wie es `WERKBANK_MODELL` schon gibt), damit das Hausmodell plant, während die Schritte die großen Modelle ausführen. Umstellen entscheidet der Besitzer.

## Reihenfolge

1. **Katalog im Harness** (Schritt 0) — ohne GPU, verbessert sofort jedes Modell. Mit Tests.
2. **Grundlinie messen** — wie oft erfindet das heutige Standardmodell Namen, wie oft wählt es falsch? Erst messen, dann trainieren.
3. **Generatoren und Schiedsrichter** — zufällige Kataloge, Spezifikationen, Korrekturdialoge.
4. **Daten** — Ablehnungsstichprobe mit lokalen Modellen, eine Nacht.
5. **Voranmeldung, dann fünf Seeds, dann Urteil** — eine Nacht.

## Was dieser Plan nicht verspricht

- Die Qualität der Freitextfelder eines Plans (`rolle`, `anweisung`) prüft kein Schiedsrichter direkt — nur das Ausführen verrät, ob sie taugen.
- Ob ein 4-B-Modell als Orchestrator das heutige Standardmodell schlägt, ist offen. Das Ziel ist ein Modell, das weniger erfindet und sicherer wählt — nicht eines, das klüger schreibt.
- Alles lief bisher auf einer Maschine. Wie gut das Hausmodell auf einem Rechner mit ganz anderen Modellen liest, zeigt erst ein zweites Gerät.

## Gemessen: Schritt 0 (Katalog) und Schritt 1 (Grundlinie), 25.09.2026

Der Katalog ist gebaut (`modell_katalog`, `katalog_text` in `server.py`). Gemessen mit `storage/datenwert/orchestrator/messen.py`: 24 Ziele, geplant vom Standardmodell gemma4:12b bei Temperatur 0 — einmal mit dem alten Planer (nur Namen), einmal mit Katalog. Bewertet von einem Schiedsrichter aus Code. Die Ziele sind reines Messmaterial.

**Erster Versuch: nicht aussagekräftig.** Mit Denkphase brauchte gemma4:12b für einen einzigen Plan oft über zehn Minuten; nach zwei Stunden waren 9 von 24 Zielen durch, und jeder „ungültige“ Plan war in Wahrheit eine leere Antwort. Das ist ein eigener Befund: **Der Orchestrator war mit dem Standardmodell praktisch unbenutzbar.** Die Planung läuft seitdem ohne Denkphase — 15 Sekunden je Plan, 24 von 24 gültig.

**Ohne Denkphase:**

| | alter Planer (nur Namen) | mit Katalog |
|---|---|---|
| gültige Pläne | 24 | 24 |
| **erfundene Modellnamen** | **8 von 43 Wahlen** | **0 von 47** |
| zu groß gewählt | 1 | 0 |
| Code-Schritte mit Code-Modell | 7/7 | 5/5 |

Der alte Planer kopierte etwa die Anbieterangabe in den Namen (`gemma4:12b (Provider: Ollama (lokal))`) oder vertippte sich (`hf.wo/…`) — jeder davon hätte einen Lauf scheitern lassen.

**Eine Nebenwirkung, die der Schiedsrichter nicht maß.** In der ersten Katalogfassung hieß ein großes, aber passendes Modell „höchstens für EINEN Schritt“. Der Planer mied daraufhin qwen3.6-35b völlig (0 Wahlen) — genau das Modell, das im Vergleichslauf mit 42/72 am besten und schnellsten war. Die Begründung war auch falsch: Schritte laufen nacheinander, und Ollama entlädt, wenn das nächste Modell Platz braucht. Geändert zu „groß, lädt langsamer, passt aber allein“; danach: weiterhin 0 erfunden, 0 zu groß, qwen3.6-35b **16-mal** gewählt.

**Gemessene Güte im Katalog (26.09.2026).** Jede Katalogzeile trägt jetzt, wo vorhanden, die Werkbank-Prüfung dieses Rechners („42/72 gelöst, 0,7 min je Aufgabe“), und ein hier festgehaltener Absturz macht aus jedem Modell „PASST NICHT“. Zwei Messungen, gleicher Planer, gleiche 24 Ziele:

| | alter Planer | Katalog mit Güte | dazu Regel vereinheitlicht |
|---|---|---|---|
| erfundene Modellnamen | 16 von 44 | 1 von 43 (Tippfehler `a3_b`) | **0 von 45** |
| zu groß gewählt | 1 | 0 | 0 |
| qwen3.6-35b gewählt (das gemessen beste) | — | 32-mal | 28-mal |
| Code-Schritte mit dem gemessen besten Modell | 0/6 | 0/5 | **0/5** |

Der alte Planer erfand diesmal doppelt so viele Namen wie am Vortag (16 statt 8), weil inzwischen mehr Modelle installiert sind — längere Listen, mehr Gelegenheit zum Vertippen.

**Was die Anweisung nicht schaffte.** Die Anweisung verlangte zunächst „Code → Merkmal ‚Code‘“ und zwei Sätze später „für Code-Schritte das Modell mit den meisten gelösten Aufgaben“. Der Planer folgte der ersten Regel. Auch nachdem die Regel eindeutig war („das mit den MEISTEN gelösten Aufgaben, auch wenn es nicht ‚Code‘ heißt“), nahm er für 5 von 5 Code-Schritten qwen2.5-coder (17/72) statt qwen3.6-35b (42/72). Er folgt dem Namen, nicht der Messung.

**Deshalb steht es jetzt im Harness, nicht im Prompt** (`modellwahl_pruefen` in `server.py`): Ein Modell mit „passt nicht“ wird nie ausgeführt, auch wenn der Planer es wählt; ein Code-Schritt bekommt das gemessen beste passende Modell, wenn das gewählte ebenfalls gemessen und schlechter ist. Der Verlauf zeigt jede Korrektur mit Grund. Das ist genau die Lehre dieses Plans: Was ein Schiedsrichter aus Code prüfen kann, muss man einem Modell nicht beibringen — man kann es durchsetzen. Trainiert werden muss nur, was sich nicht durchsetzen lässt: die Wahl der Schritte, die Anweisungen, die Rollen.

## Gemessen: Hausmodell v1 (26.09.2026) — PASS

Regeln in `storage/datenwert/hausmodell/voranmeldung.md`, vor jeder Messung geschrieben, mit einem Nachtrag vor der ersten Seed-Messung (jeder Seed wird an seinem Zwischenstand 846 gemessen, weil das Zeitlimit die Läufe bei ~960 von 1 130 Iterationen beendete).

- **Ziele:** 760, geschrieben vom lokalen qwen3.6-35b aus Art und Lebensbereich, gefiltert durch Code; 114 nur zur Prüfung.
- **Daten:** 1 189 Beispiele per Ablehnungsstichprobe (Lehrer qwen3.6-35b, Schiedsrichter `pruefstand/orchestrator.py`), davon 219 Korrekturdialoge; 137 Versuche verworfen. Keine Claude-Texte.
- **Prüfung:** 138 Pläne — P1: Prüfziele auf Katalogen aus **zurückgehaltenen Modellfamilien**; P2: die 24 Messziele auf dem echten Katalog dieses Rechners.

| Planer | angenommen | P1 fremde Kataloge | P2 echter Katalog | erfunden | „PASST NICHT“ gewählt | s je Plan |
|---|---|---|---|---|---|---|
| 4B ohne Training | 35 % | 41/114 | 7/24 | 9 | 16 | 6,0 |
| gemma4:12b (heutiger Planer) | 56 % | 61/114 | 16/24 | 12 | 1 | 13,2 |
| qwen3.6-35b (Lehrer) | 72 % | 84/114 | 15/24 | 0 | 1 | 6,2 |
| **4B + Adapter, 4 Seeds** | **75–83 %, Mittel 79 %** | 91–95/114 | 13–19/24 | 0–2 | 4–10 | 6,3 |

**Urteil: PASS, +44 Punkte, 95-%-Intervall [+39 ; +49]** gegen den 4B ohne Adapter. Seed 4 fiel wegen eines Serverausfalls in der Messung aus und zählt nicht; das Urteil steht auf vier Seeds.

**Produktfrage: ja.** Der trainierte 4B (2,3 GB) plant auf fremden Katalogen deutlich besser als gemma4:12b (7 GB) und sogar besser als sein Lehrer — er hat nur die Pläne gesehen, die der Schiedsrichter annahm. Und er ist doppelt so schnell wie gemma4:12b.

**Was das Ergebnis nicht sagt.**
- **Auf dem echten Katalog ist der Vorsprung klein:** im Mittel 16,25 von 24 gegen 16 bei gemma4:12b. Der große Gewinn liegt beim Lesen **fremder** Kataloge — also auf anderen Rechnern, genau wofür er gebaut ist, aber hier noch nicht auf einem zweiten Gerät belegt.
- **Er wählt öfter ein Modell mit „PASST NICHT“** (4–10-mal gegen 1-mal bei gemma4:12b). Im Einsatz fängt das der Harness ab (`modellwahl_pruefen`), ein Fehler ist es trotzdem — der nächste Datensatz braucht mehr Beispiele genau dafür.
- „Angenommen“ heißt: Die Regeln des Harness sind eingehalten. Ob die Anweisungen in den Schritten gut sind, prüft erst das Ausführen; das ist nicht gemessen.

**Einsatz:** bester Seed ist Seed 3 (82,6 %). Sein Adapter im Lauf `training_20260926_092429` ist bitgleich mit dem gemessenen Zwischenstand 846. Aktiviert ist nichts; umstellen entscheidet der Besitzer, in zwei Schritten:

1. **Training → Läufe →** „Hausmodell v1 Seed 3“ → **Als Modell bereitstellen**. Das startet einen mlx-Server mit dem Adapter; er erscheint als Modell „Schüler: …“.
2. **Training → Rollen → Orchestrator · plant Abläufe:** dieses Modell wählen, speichern.

Ab dann plant das Hausmodell; die Schritte laufen weiter mit den Modellen, die der Plan wählt, und `modellwahl_pruefen` setzt „PASST NICHT“ und das beste Code-Modell durch. Rückgängig: die Rolle leeren — dann plant wieder das Chat-Standardmodell.
