# Das erste gemessene Ergebnis: 0 % → 13 %

Am 18.09.2026 hat Dive on Wide zum ersten Mal ein kleines Modell nachweisbar besser gemacht — und zwar so, dass man es widerlegen kann. Dieses Dokument hält den ganzen Weg fest, **einschließlich der beiden Fehlschläge davor**, weil die mehr über das Verfahren sagen als das Ergebnis selbst.

## Der Aufbau

| | |
|---|---|
| Schüler | `qwen3-4b-2507-mlx-4bit` (4 B, 4 Bit), LoRA Rang 16 auf 16 Schichten |
| Lehrer | **Breitensuche** — ein Algorithmus, kein Modell. Jedes Label bewiesen optimal. |
| Schiedsrichter | Simulator (`spiel.spielen`): „gelöst" ist ein Fakt, keine Bewertung |
| Aufgabe | Sokoban, 5×5 bis 7×7, 1–2 Kisten, Lösungen 3–10 Züge, ein Zug je Anfrage |
| Trainingsdaten | 14 375 Beispiele aus 2000 Puzzles, **inklusive Zuständen abseits des Optimalpfads** |
| Prüfsatz | 120 zurückgehaltene Puzzles, 491 gesperrte Zwischenstände, **0 davon im Training** |
| Training | 1200 Iterationen, Stapel 8, Lernrate 5e-5, Spitzenspeicher 4,2 GB, ~45 min |

## Das Ergebnis

```
                     Orakelzug getroffen      Puzzles gelöst      gültige Züge
Zufallserwartung             44 %
A  (Grundmodell)     47/183 = 26 %            0/120  =  0 %           67 %
AB (mit Adapter)    117/183 = 64 %           16/120  = 13 %           86 %
```

Alle 16 gelösten Puzzles wurden **optimal** gelöst, also in der kürzest möglichen Zugzahl. Das Grundmodell scheitert in jeder einzelnen Episode an einem Regelverstoß — es läuft gegen eine Wand oder schiebt zwei Kisten.

## Die zwei Fehlschläge davor — und was sie gelehrt haben

**Fehlschlag 1: Der Verlust lügt.** Derselbe Aufbau, aber nur 1969 Beispiele, alle auf dem Optimalpfad. Der Validierungsverlust fiel von 14,617 auf 0,342, Train- und Val-Kurve deckungsgleich — nach Lehrbuch ein Erfolg. Die Messung sagte etwas anderes:

```
A  (Grundmodell): 28 % Orakelzüge, 0/80 gelöst
AB (mit Adapter): 29 % Orakelzüge, 0/80 gelöst
```

Der Verlust maß die Vorlagen-Token (`<think></think>`, Zeilenumbrüche), die trivial vorhersagbar sind, und nicht den einen Buchstaben, auf den es ankommt. **Hätten wir dem Verlust geglaubt, stünde im Bericht „Adapter deutlich verbessert".** Genau dagegen ist der Datenwert-Test gebaut, und hier hat er sich zum ersten Mal selbst bewiesen.

**Fehlschlag 2: Nur perfekte Spielverläufe lehren nichts über Fehler.** Trainiert man ausschließlich Optimalpfade, sieht das Modell nie einen Stand, der nach einem eigenen Fehltritt entsteht. Ein einziger Fehler führt dann in unbekanntes Gebiet, und der Rest der Episode ist verloren. Die Abhilfe kostet nichts, weil der Lehrer ein Algorithmus ist: absichtlich falsch abbiegen, dann das Orakel erneut fragen, was von **hier** aus richtig ist (`spiel.zustaende_sammeln`). Das war der Unterschied zwischen 29 % und 64 %.

## Drei Messfehler, die unterwegs auffielen

1. **Ein Token-Deckel von 4** schnitt die Antwort des Adapters ab (`'\n\nD'`): 80 angeblich „unlesbare" Antworten waren in Wahrheit gültige Züge.
2. **Ein ungültiger Zug ließ das Brett stehen.** Bei Temperatur 0 antwortet das Modell auf dasselbe Brett identisch — es lief bis zum Budget gegen dieselbe Wand. Aus 80 Fehlern wurden 1404 gezählte. Heute beendet ein Regelverstoß die Episode.
3. **Ein Datensatz-Generator ohne Grenze** verlangte 4000 verschiedene Puzzles aus einem Raum, der nach ~1500 erschöpft war, und drehte 82 Minuten bei 99 % CPU im Leerlauf. Mit breiterem Raum, Fortschrittsausgabe und Versuchsgrenze: 2 Sekunden.

## Nachrechnen

```bash
python3 spiel.py orakel --anzahl 50      # beweist Lehrer und Schiedsrichter
python3 spiel.py zeigen                  # ein Puzzle mit optimaler Lösung
python3 tests/run_tests.py --nur spiel   # die Regeln des Schiedsrichters
```

Die Herkunft des Datensatzes steht in `storage/training/daten/sokoban_v2/herkunft.json`, die Sperrliste der Prüfzustände ist dort dokumentiert.

## Die ganze Serie: sechs Seeds, ein PASS — knapp

Der Einzellauf oben war Seed 1. Vollständig gemessen (19.09.2026, je 108 min Training, gleiches Protokoll, dieselben 120 zurückgehaltenen Puzzles):

| Lauf | Zuggenauigkeit | gelöst (Standard) | Transfer (7×7, 2 Kisten) |
|---|---|---|---|
| A (Grundmodell) | 26 % | 0/120 | 0/60 |
| AB-1 | 64 % | 16/120 (13 %) | 0/60 |
| **AB-2** | **27 %** | **0/120** | 0/60 |
| AB-3 | 57 % | 11/120 (9 %) | 0/60 |
| AB-4 | 67 % | 25/120 (21 %) | 1/60 (2 %) |
| **AB-5** | **24 %** | **0/120** | 0/60 |
| AB-6 | 62 % | 12/120 (10 %) | 0/60 |
| AB-klein (⅕ der Daten) | 48 % | 1/120 (1 %) | 0/60 |

**Urteil: PASS.** Gepaarte Differenz +8,9 Prozentpunkte, 95-%-Intervall **[+0,5 ; +17,3]**, sechs Seeds. Die untere Grenze liegt über null — nach der Regel, die vor dem Lauf in `storage/datenwert/sokoban/voranmeldung.md` stand. Sie liegt aber nur *knapp* darüber, und das gehört in denselben Satz.

**Zwei von sechs Läufen lernen nichts.** AB-2 und AB-5 bleiben die vollen 1200 Iterationen im Plateau bei Verlust 0,343 — dem Zustand „gib einen sauberen Buchstaben aus und rate". Die anderen vier brechen zwischen Iteration 750 und 1200 aus. Das Verfahren wirkt, ist aber in einem Drittel der Fälle instabil; das ist kein Rauschen, das ist ein offener Mangel.

**Die Datenmenge ist messbar wertvoll.** Ein Fünftel der Beispiele (2788 statt 13 943), sonst alles gleich: Zuggenauigkeit 48 % statt 57–67 %, gelöst 1/120 statt 11–25/120. Genau das ist die Frage, für die der Datenwert-Test gebaut wurde — und sie hat hier zum ersten Mal eine Zahl.

**Transfer: UNVERIFIED.** Auf 7×7-Brettern mit zwei Kisten löst der beste Adapter 1 von 60, gepaart +0,3 Prozentpunkte, Intervall [−0,4 ; +1,0]. Gelernt wurden kleine Bretter mit einer Kiste, nicht „Sokoban".

**Ein Beobachterfehler, der fast zu einer falschen Regel geführt hätte.** Eine Wache sollte während des Trainings die Aufgabe messen. Sie zeigte durchgehend 0/20 — auch für Läufe, die am Ende 10 % lösten. Ursache: Alle Zwischenstände landeten im selben Ordner, und der Server hielt den zuerst geladenen Adapter fest. Mit eigenem Pfad je Stand (AB-4): **Iter 300 → 0/20, Iter 750 → 0/20, Iter 1200 → 4/20.** Daraus folgt die eigentliche Lehre: **Ein früher Abbruch anhand der Aufgabenmessung hätte den besten Lauf getötet.** Der Sprung passiert spät; vorher ist ein guter Lauf von einem toten nicht zu unterscheiden.

## Serie B: dieselbe Methode, 0 % → 80 %

Serie A hatte zwei tote Läufe (1200 Iterationen, Plateau bei Verlust 0,343). Die Vermutung — zu kurz trainiert, nicht kaputt — wurde als Serie B **vorab angemeldet** (`storage/datenwert/sokoban_b/voranmeldung.md`), samt der Vorhersage, an der sie hätte scheitern können: *höchstens 1 von 6 Läufen bleibt im Plateau*.

Zwei Änderungen, beide vorher benannt: kurze Systemzeile (43 statt 137 Token — in Serie A waren **79 %** jedes Beispiels die immer gleiche Anweisung) und 2000 statt 1200 Iterationen. Die Zustände sind bitgleich dieselben wie in Serie A.

| Lauf | Zuggenauigkeit | gelöst (Standard) | Transfer (7×7, 2 Kisten) |
|---|---|---|---|
| A (Grundmodell) | 16 % | 0/120 | 0/60 |
| AB-1 | 94 % | 98/120 (82 %) | 7/60 (12 %) |
| AB-2 | 92 % | 93/120 (78 %) | 5/60 (8 %) |
| AB-3 | 92 % | 87/120 (72 %) | 7/60 (12 %) |
| AB-4 | 90 % | 83/120 (69 %) | 7/60 (12 %) |
| AB-5 | 92 % | 96/120 (80 %) | 11/60 (18 %) |

**Standard: PASS**, +76,2 Prozentpunkte, Intervall **[+69,7 ; +82,7]**, fünf Seeds.
**Transfer: PASS**, +12,3 Punkte, Intervall **[+7,8 ; +16,9]** — auf Brettgrößen und Kistenzahlen, die im Training nie vorkamen. In Serie A war derselbe Transfer noch UNVERIFIED.

**Plateau-Läufe: 0 von 5.** Die Vorhersage ist eingetroffen, die Vermutung bestätigt.

**Was Serie B nicht beantwortet:** Zwei Änderungen auf einmal heißt, die Kombination wirkt — nicht, welcher Teil. Das prüft Serie C mit genau einer Änderung (kurze Zeile, wieder 1200 Iterationen). Die drei möglichen Ausgänge stehen vor dem Lauf in `storage/datenwert/sokoban_c/voranmeldung.md`.

**Nebenbefund zur Grundlinie:** Mit der kurzen Anweisung fällt das untrainierte Modell von 26 % auf 16 % Zuggenauigkeit — es zieht aus ausführlichen Regeln also durchaus etwas. Gelöst hat es in beiden Fällen 0 von 120. Deshalb dürfen Serie A und B nicht gegeneinander gerechnet werden; jede steht für sich.

## Serie C: welcher Teil war der Hebel?

Serie B hatte zwei Dinge gleichzeitig geändert. Serie C ändert gegenüber B **genau eines** zurück (wieder 1200 Iterationen, kurze Anweisung bleibt) — damit ist jeder Vergleich einfaktoriell.

| Serie | Anweisung | Iter. | Δ gelöste Puzzles | tote Läufe | Transfer |
|---|---|---|---|---|---|
| A | lang (137 Token) | 1200 | +8,9 pp [+0,5 ; +17,3] | **2 von 6** | UNVERIFIED |
| C | kurz (43) | 1200 | **+54,2 pp** [+44,9 ; +63,4] | 0 von 6 | PASS +7,2 [+1,2 ; +13,2] |
| B | kurz (43) | 2000 | **+76,2 pp** [+69,7 ; +82,7] | 0 von 5 | PASS +12,3 [+7,8 ; +16,9] |

**A → C (nur die Anweisung gekürzt): +45 Punkte. C → B (nur länger trainiert): +22 Punkte.** Die kurze Anweisung ist der doppelt so starke Hebel.

**Eine Korrektur meiner eigenen Deutung.** Nach Serie B hieß es hier, die toten Läufe der Serie A seien „zu kurz trainiert" gewesen — die vorab gestellte Vorhersage (höchstens 1 Plateau-Lauf bei 2000 Iterationen) war eingetroffen, also schien die Erklärung bestätigt. Serie C zeigt: Die Plateau-Läufe verschwinden schon **ohne eine einzige zusätzliche Iteration**, sobald die Anweisung kürzer ist. Die Vorhersage stimmte, die Begründung war falsch. Ohne die einfaktorielle Gegenprobe hätten wir eine plausible und falsche Geschichte erzählt — und genau diese Gegenprobe ist das, was unser TÜV anderen abverlangt.

## Der Härtetest: dasselbe Verfahren ohne perfektes Orakel

Sokoban hatte eine Breitensuche als Lehrer. Codeaufgaben haben keinen — aber sie haben **ausführbare Tests**. Der Schüler löst Fabrik-Aufgaben selbst, nur bestandene Verläufe werden Trainingsdaten (`ablehnung.py`, Label GOLD_EXECUTION).

**Zuerst musste das Messinstrument repariert werden.** Mit 12 Kontrollaufgaben bei Temperatur 0,2 schwankte allein die Grundlinie zwischen 4/12 und 5/12 — 8,3 Prozentpunkte Rauschen, genau in der Größe der erwarteten Effekte. Neues Instrument: **72 unverseuchte Aufgaben** (12 Kontrollaufgaben plus 60, die in keiner Sammlung je bestanden und aus denen folglich kein einziges Trainingsbeispiel entstand), **Temperatur 0**, deterministisch. Auflösung 1,4 statt 8,3 Punkte.

| Bedingung | Grundlinie | Adapter | Δ | Urteil |
|---|---|---|---|---|
| Datensatz v1 (789 Beispiele), lange Anweisung | 12/72 | 12/72 · 18/72 | +0,0 · +8,3 | UNVERIFIED |
| Datensatz v2 (1518), lange Anweisung | 12/72 | 15/72 · 13/72 | +4,2 · +1,4 | UNVERIFIED |
| Datensatz v2, **kurze Anweisung** (252 statt 808 Token) | 9/72 | 16/72 · 12/72 | +9,7 · +4,2 | UNVERIFIED |

**Zwei vorab benannte Vorhersagen, beide nicht eingetroffen:**

1. *Mehr Daten helfen.* Verdopplung von 789 auf 1518 Beispiele: +4,2 statt +2,8 Punkte. Kein Unterschied. Das spart weitere Sammelnächte.
2. *Die Kürzung der Anweisung ist auch hier der Hebel.* Angekündigt war: „beide Seeds deutlich über +8,3 Punkten". Ergebnis: +9,7 und +4,2. **Der Sokoban-Befund überträgt sich nicht** in der dramatischen Form (+45 Punkte dort).

**Was bleibt:** Alle sechs unabhängig trainierten Adapter liegen auf oder über ihrer Grundlinie (+0,0 · +8,3 · +4,2 · +1,4 · +9,7 · +4,2). Zusammengefasst ergibt das +4,6 Punkte, Intervall [+0,7 ; +8,6], nominell PASS — **aber diese Auswertung wurde nach Sicht der Daten gewählt und mischt zwei Anweisungen mit zwei Grundlinien.** Sie ist eine Hypothese für den nächsten Lauf, kein Ergebnis. Der ehrliche Stand im Agentenbereich lautet: **ein kleines, konsistent positives Signal, das nach unseren eigenen Regeln noch nicht verifiziert ist.**

**Nebenbefund mit Produktbezug:** Die Messdauer schwankte je Adapter zwischen 67 und 238 Minuten bei identischer Aufgabenmenge. Ein trainierter Agent läuft nicht nur anders gut, sondern auch anders lang — das gehört in jede Kostenrechnung.

## Was das Ergebnis **nicht** sagt

- **Sokoban bleibt ein Übungsplatz.** 80 % gelöste Puzzles sind ein Beweis für das Verfahren, kein Produkt.
- **Zwei Änderungen auf einmal** in Serie B: Die Zuschreibung fehlt noch (Serie C läuft).
- **Das PASS ist knapp.** Untere Intervallgrenze +0,5 Prozentpunkte bei sechs Seeds. Ein siebter Fehlschlag-Seed könnte es wieder kippen — das ist kein Grund, jetzt aufzuhören zu messen.
- **Ein Drittel der Läufe lernt nichts.** Solange das so ist, ist das Verfahren nicht verlässlich, nur wirksam.
- **Sokoban ist kein Produkt.** Es ist ein Übungsplatz, auf dem sich Lernen messen lässt. Ob dasselbe Verfahren bei Agentenaufgaben trägt, ist offen; dort steht die Messung noch bei 7/12 gegen 0/12 mit diagnostiziertem Gerüstfehler.


## Agentenbereich v3: der erste PASS im Produktbereich (24.09.2026)

Nicht mehr Sokoban, sondern die eigentliche Aufgabe: der Werkbank-Agent, der Programmieraufgaben löst. Regeln in `storage/datenwert/agent_v3/voranmeldung.md`, **vor** dem Lauf geschrieben.

**Aufbau.** Der beste bisherige Schüler (kurze Anweisung, 16/72) löste die 128 Trainingsaufgaben selbst; nur Verläufe mit bestandenen versteckten Tests wurden Daten (693 neue, zusammen mit den alten 2192 Beispiele, **0 aus den Messaufgaben**). Dann Seeds trainiert und auf **72 zurückgehaltenen Aufgaben** gemessen — 12 Kontrollaufgaben und 60, die das Grundmodell beim Sammeln **nie** gelöst hat. Temperatur 0.

| Lauf | gelöst | Differenz zu A |
|---|---|---|
| A (Grundmodell) | 9/72 (12,5 %) | — |
| AB-1 | 16/72 | +9,7 |
| AB-2 | 17/72 | +11,1 |
| AB-3 | 12/72 | +4,2 |
| AB-4 | 16/72 | +9,7 |

**Urteil: PASS.** Mittel **+8,7 Punkte**, 95-%-Intervall **[+3,8 ; +13,6]**, vier Seeds. Die Vorhersage (mindestens +5) ist eingetroffen. Jeder Seed löst **8 bis 12 Aufgaben, die das Grundmodell nie gelöst hat** — das ist Übertragung auf Unbekanntes, nicht Wiederholung.

**Was dazu gehört, im selben Satz:**
- **Vier statt fünf Seeds.** Seed 2 wurde nachts verworfen, weil der Modellserver hing (15 Zeitüberschreitungen, als „nicht gelöst" gezählt — Infrastruktur, nicht Modell). Fassung 2 startet den Server bei einem Hänger neu; die Nachmessung brauchte keinen einzigen Neustart. Für Seed 5 reichte die Zeit nicht.
- **A wurde in diesem Lauf nicht neu gemessen.** Der Wert 9/72 stammt aus `lauf3.py` zwei Tage zuvor (Temperatur 0, gleiche Anweisung, gleicher Server). Alle vier Differenzen hängen an dieser einen Zahl. Die Gegenprobe steht noch aus.
- **Was die Iteration beiträgt, ist offen.** Die Vorgänger ohne Iteration lagen bei 16 und 12 — dieselbe Größenordnung. Belegt ist das Rezept „kurze Anweisung + geprüfte eigene Lösungen", nicht der Zusatznutzen der Iteration.
- **Streuung je Aufgabe ist groß:** 28 Aufgaben löst mindestens ein Seed, nur 5 lösen alle vier.
- **Ob der Schüler die großen Modelle schlägt, ist nicht gemessen.** Verglichen wurde mit seinem eigenen Grundmodell, nicht mit qwen2.5-coder:14b oder gemma4:12b im selben Harness.

Bester Adapter: AB-2, `storage/training/adapter/training_20260924_003025`. Die laufende Installation wurde nicht umgestellt.


## Vergleichslauf: Bringt das Training etwas? (25.09.2026)

Das PASS von v3 hatte zwei Lücken: Die Grundlinie stammte aus einem zwei Tage alten Lauf, und verglichen war nur mit dem eigenen Grundmodell. Regeln in `storage/datenwert/vergleich/voranmeldung.md`, vor dem Lauf. Dieselben 72 Aufgaben, dieselbe kurze Anweisung, Temperatur 0.

| Modell | gelöst von 72 | Minuten je Aufgabe |
|---|---|---|
| **qwen3.6-35b-a3b** (installiert, MoE, 3 B aktiv) | **42 (58,3 %)** | **0,7** |
| gemma4:12b | 30 von 66, dann Zeitgrenze (4 h) — nicht vergleichbar | ~3,6 |
| qwen2.5-coder:14b | 17 (23,6 %) | 1,1 |
| 4B + Adapter, Mittel der vier v3-Seeds | 15,25 | 1,1–2,9 |
| 4B ohne Adapter, **heute frisch** | 10 (13,9 %) | 1,6 |

**Frage 1 — die Grundlinie hält.** Frisch gemessen 10/72 statt 9/72 (Temperatur 0 ist also nicht ganz deterministisch, aber nah). Mit dem frischen Wert bleibt v3 **PASS: +7,3 Punkte, [+2,4 ; +12,2]**. Das Verfahren verbessert einen kleinen Schüler nachweislich — auch gegen eine Grundlinie aus demselben Tag.

**Frage 2 — das Produkt: nein, nicht auf diesem Rechner.** Das vorhandene qwen3.6-35b-a3b löst fast dreimal so viel wie der trainierte Adapter und ist dabei am schnellsten. Von den 28 Aufgaben, die irgendein Adapter-Seed löst, löst es 25 ebenfalls; nur drei löst allein ein Adapter. Die Vorhersage (qwen2.5-coder:14b löst mehr als der Adapter) ist eingetroffen, knapp. Der einzige Vorteil des Adapters ist der Speicher: rund 2,5 GB statt 15,7 GB.

**Was daraus folgt.**
- **Sofort und ohne Training** ist die größte gemessene Verbesserung für die Werkbank die Modellwahl: qwen3.6-35b-a3b statt des 4B-Schülers oder gemma4:12b. Umstellen entscheidet der Besitzer.
- **Einen 4-B-Schüler zu trainieren, um die großen Modelle zu ersetzen, lohnt sich hier nicht.** Training lohnt sich dort, wo ein kleines Modell zwingend ist: als Orchestrator, der neben dem arbeitenden großen Modell laufen muss und ihm den Speicher lassen soll (`docs/HAUSMODELL.md`), und auf Geräten mit wenig Speicher.
- Den 35B selbst nachzutrainieren, würde den Vergleich ändern — mit 15,7 GB Gewichten auf 24 GB Arbeitsspeicher ist das hier aber nicht machbar.

## Nachtrag: qwen3.8-27B in 3 Bit (25./26.09.2026)

Dieselben 72 Aufgaben, dieselben Regeln; Nachträge in `storage/datenwert/vergleich/voranmeldung.md` vor jedem Anlauf. Die vorhandene nvfp4-Fassung (17,4 GiB) brach schon ab etwa 5 000 Token Kontext mit Speichernot ab. Geprüft wurde deshalb die 3-Bit-GGUF von unsloth (12,2 GiB).

| Modell | gelöst von 72 | Minuten je Aufgabe |
|---|---|---|
| qwen3.6-35b-a3b | 42 | 0,7 |
| **qwen3.8-27b, 3 Bit** (über llama-server, siehe unten) | **40 (55,6 %)** | **2,3** |
| qwen2.5-coder:14b | 17 | 1,1 |

**Die Vorhersage ist eingetroffen:** mehr als der Coder, weniger als der 35B, mindestens dreimal so langsam (2,3 statt 0,7 Minuten). Die beiden großen Modelle lösen weitgehend dieselben Aufgaben (34 gemeinsam); der 27B löst 6, die der 35B nicht löst, der 35B 8, die der 27B nicht löst.

**Der wichtigere Befund ist der erste Anlauf.** Über Ollama lief der 27B nach 30 Aufgaben aus dem Speicher: macOS beendete den Modellprozess (`free_swap="0 B"`), acht Minuten später die Ollama-App. Die Ursache steht im Ollama-Protokoll. Für dieses Hybridmodell legt llama.cpp je Agentenschritt einen Zwischenstand von 150 MiB an, bis zu 32 Stück (4,7 GiB), dazu einen Zwischenspeicher für Eingaben von bis zu 8 GiB. Mit 13,5 GiB Modell reicht das auf 24 GB nicht. Eine Probe mit Einzelanfragen bis 12 000 Token hatte bestanden; sie konnte das nicht zeigen, weil die Zwischenstände erst über viele Schritte wachsen.

In Ollama lässt sich beides nicht einstellen. Gemessen wurde deshalb dieselbe Datei direkt in `llama-server` mit `--ctx-checkpoints 4 --cache-ram 0`: 13,9 GiB über den ganzen Lauf, 29 % Speicher frei, nichts ausgelagert. Ein Wächter hätte die Messung sauber abgebrochen, bevor der Rechner erstickt.

**Was daraus folgt.**
- **Für Dive on Wide über Ollama ist der 27B auf diesem Rechner nicht brauchbar.** Der Modellkatalog weiß das jetzt: Ein hier festgehaltener Absturz (`werkzeuge/guete_eintragen.py --absturz`) führt ein Modell als „PASST NICHT“, egal wie groß die Datei ist. Der Orchestrator wählt es nicht mehr.
- **Die Empfehlung bleibt qwen3.6-35b-a3b:** Es löst etwas mehr, ist dreimal so schnell und lief unter Ollama alle 72 Aufgaben ohne Speicherprobleme.
- Die Dateigröße sagt bei Hybridmodellen nicht voraus, ob ein Modell unter Agentenlast passt. Das kann nur die Messung.
