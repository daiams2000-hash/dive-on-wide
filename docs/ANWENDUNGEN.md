# Anwendungen — zehn Alltagsaufgaben, gemessen

Was kann Dive on Wide heute für einen normalen Nutzer tun, und wo braucht es einen
Handgriff? Gemessen am 29.09.2026 auf einem Mac mini M4 Pro mit 24 GB, mit einer
frischen Instanz, eingerichtet wie von einem neuen Nutzer (nur „Code ausführen“ an).
Modell: **Qwen 3.6 35B-A3B, 3 Bit** (16,8 GB), für Bilder **Gemma 4 12B**.

Die Rolle des Testers war nur die des Nutzers: Dateien ins Projekt legen, Auftrag
tippen, Ergebnis nachprüfen. Code und Texte hat ausschließlich das lokale Modell
geschrieben. Treiber, Vorlagen und alle Rohdaten: `storage/datenwert/anwendungen/`
(nicht im Repository).

## Auf einen Blick

| # | Aufgabe | Dauer | Ergebnis |
|---|---|---|---|
| 1 | Fragen zur Hausordnung (Wissen) | 13–24 s je Frage | **7 von 7 richtig**, auch „steht nicht drin“ |
| 2 | Skill aus einem Satz bauen und benutzen | 2,5 min | **brauchbar**: fertige Absage ohne Platzhalter |
| 3 | Briefing im Rhythmus | 45 s | **läuft**, erfindet nichts |
| 4 | Fehler in einem Rechnungsprogramm | 40 s | **richtig behoben**, aber ohne neuen Test¹ |
| 5 | Kontoauszug auswerten | 50 s | **auf den Cent genau** |
| 6 | Code-Review vor dem Commit | 14 s | **2 von 3** eingebauten Fehlern gefunden |
| 7 | Screenshot → HTML-Seite | 16,5 min | **sehr nah am Vorbild**¹ |
| 8 | Wochenende planen (Orchestrator) | 6 min | **vollständig**, ohne Websuche aber allgemein¹ |
| 9 | Recherche mit Quellen | 4 min | **ehrlich leer**: Websuche gesperrt¹ |
| 10 | Kleines Spiel aus einer Idee | 30–40 min je Versuch | **nicht fertig**: bester Versuch mit richtigem Code, aber falschen eigenen Tests |

¹ Hier fand der Test einen Fehler im Harness; er ist behoben (siehe unten).

## Streuung: drei Läufe (29./30.09.2026)

Alle zehn Aufgaben liefen dreimal, jeweils in einer frischen Instanz (Lauf 2 und 3
über Nacht, ohne Eingriff).

| Aufgabe | Lauf 1 | Lauf 2 | Lauf 3 | Dauer |
|---|---|---|---|---|
| Wissen (7 Fragen) | 7/7 | 7/7¹ | 7/7 | 2 min |
| Skill bauen und nutzen | ✓ | ✓ | ✓ | 2 min |
| Rhythmus | ✓ | ✓ | ✓ | < 1 min |
| Rechnungsfehler | ✓ | ✓ | ✓ | 1 min |
| Kontoauszug | ✓ | ✓ | ✓ | 1 min |
| Code-Review | 2/3 | 2/3 | 2/3 | < 1 min |
| Screenshot → HTML | ✓ | ✓ | ✓ | 9–17 min |
| Wochenende planen | ✓² | ✓² | ✓² | 5–7 min |
| Recherche | ✗ | ✗ | ✗ | 3–4 min |
| Spiel | ✗ | ✗ | ✗ | 27–34 min |

¹ „keine Regelung“ ist richtig; die automatische Prüfung kannte die Wendung nicht.
² vollständig, ohne Suchdienst aber allgemein (siehe Fall 8).

**Stabil:** acht von zehn Aufgaben gelingen in jedem Lauf. Die zwei Ausfälle sind
ebenso stabil und haben bekannte Ursachen: Recherche braucht einen Suchdienst,
das Spiel scheitert an den eigenen Tests des Modells.

## Welches Modell wofür: DowBench v1.1

34 Aufgaben (Code nach SWE-bench, Terminal, Prompt-Injektion nach AgentDojo,
Regeln nach tau-bench); „bestanden“ heißt nützlich **und** sicher.

| Modell | Größe | bestanden | Code | Terminal | Injektion | Regeln |
|---|---|---|---|---|---|---|
| Qwen 3.6 35B-A3B (3 Bit) | 16,8 GB | **31/34** | 17/20 | 4/4 | 6/6 | 4/4 |
| Gemma 4 12B | 7,6 GB | **24/34** | 12/20 | 4/4 | 4/6 | 4/4 |
| Qwen 3.5 4B (8 Bit) | 4,5 GB | **15/34** | 4/20 | 4/4 | 3/6 | 4/4 |
| Qwen 2.5 Coder 14B | 9,0 GB | **11/34** | 4/20 | 1/4 | 3/6 | 3/4 |

Qwen 3.5 4B, Qwen 2.5 Coder 14B und Qwen 3.6 wurden am 29./30.09. mit dem Harness
nach den Windows-Korrekturen gemessen — Qwen 3.6 bleibt bei 31/34, die Korrekturen
kosten nichts. Gemma 4 wurde am 30.09. vollständig nachgemessen: 24/34 (zuvor 23), gut
10 Minuten je Aufgabe. Auffällig: Das
Code-Modell ist trotz seines Namens das schwächste. Alle drei kleineren Modelle
verändern in einzelnen Aufgaben Tests, statt den Code zu reparieren — das zählt als
unsicher. Das kleine 4B ist bei Terminal und Regeln so gut wie das große Modell.

## Fremdes Gerüst mit Rechenprofil (30.09./01.10.2026)

Wie Claude Code oder Codex Dive on Wide ansteuern (`AGENTS.md`): Fragebogen holen,
Profil wählen, Aufträge über `/api/extern` geben. Gemessen mit dem Rechnungsfehler
und einer Code-Zusammenfassung; das fremde Gerüst liest je Auftrag nur 1 400 bis
2 600 Zeichen zurück.

| Profil | Arbeiter · Prüfer | Rechnungsfehler | Zusammenfassung |
|---|---|---|---|
| stark | Qwen 3.6 35B · Qwen 3.5 4B | richtig, 2,3 min | richtig, 0,8 min |
| sparsam | Qwen 3.5 4B · Qwen 3.5 4B | 1. Lauf richtig (2,3 min), 2. Lauf Schrittlimit (23 min) | richtig, 1 min |

Der Prüfer (ein zweites 4B, nur lesend) urteilte richtig — „Erfüllt: nein“, als der
Arbeiter scheiterte —, seine Einzelbefunde waren aber teils falsch. Im ersten
Anlauf hielt er den Auftrag des Arbeiters für seinen eigenen und fragte zurück;
der Prüfauftrag trennt jetzt Rolle und Auftrag. **Sparsam taugt für klar
umrissene Aufgaben; bei Fehlschlag greift die Regel „zweimal gescheitert, dann
selbst oder kleiner schneiden“.** Aufgefallen: Das Profil „ausgewogen“ wählte ein
installiertes „abliterated“-Modell (Sicherheitsschranken entfernt) — es sollte
solche Modelle nie von selbst vorschlagen.

## Die Fälle

### 1 · Wissen: Fragen zur Hausordnung

Eine erfundene Hausordnung (9 Abschnitte, Ruhezeiten, Müll, Nebenkosten …) liegt im
Wissen und wird wie in der Oberfläche mit `/` an die Frage gehängt. Sieben Fragen:
Bohren am Samstag um 17 Uhr, Gelbe Tonne, Vorauszahlung für 64 m² (Rechnung),
Gasgrill, Hund, Widerspruchsfrist (Datumsrechnung) und eine Frage, deren Antwort
**nicht** in der Hausordnung steht (Satellitenschüssel).

Alle sieben richtig, jede mit Verweis auf den Abschnitt. 64 × 2,90 € = 185,60 € und
„Zugang 10.07.2026 → Frist 10.07.2027“ stimmen. Bei der Satellitenschüssel: „nicht
explizit geregelt“, mit dem Rat, bei der Hausverwaltung nachzufragen, statt einer
erfundenen Regel.
Kleiner Schönheitsfehler: Die Antworten wechseln zwischen „Sie“ und „du“.

### 2 · KI-Ersteller: ein Skill aus einem Satz

„Schreibt aus ein paar Stichpunkten eine kurze, freundliche Absage an eine
Bewerberin …“ → Dive on Wide baut einen Skill mit drei Schritten (Eingabe ordnen, Entwurf,
Feinschliff) und dem Auslöser `@absage`. Mit „Frau Kaya; Werkstudentin Marketing;
intern besetzt; …“ entsteht ein versandfertiger Brief mit Anrede, Grund in einem Satz
und Grußformel des genannten Absenders. Keine Platzhalter wie `[Name]`.

### 3 · Rhythmus: ein Briefing nach Plan

Ein Briefing im Minutentakt angelegt: Es lief nach 36 s von selbst und fasste nur
zusammen, was das System wirklich wusste (neuer Skill, ungelesene Meldungen). Keine
erfundenen Termine.

### 4 · Werkbank: Fehler in einem bestehenden Projekt

Ein kleines Rechnungsprogramm mit zwei Fehlern (Mehrwertsteuer vor dem Rabatt
berechnet; Absturz bei „1.299,00 €“) und der Auftrag in Kundensprache: „Auf der
Rechnung stehen 11,39 € Mehrwertsteuer — richtig wären 10,25 € … Bitte beheben und
mit Tests absichern.“

In 8 Schritten und 40 s beide Fehler richtig behoben; die Nachprüfung (MwSt 10,25,
Rabatt 6,00, Brutto 64,22; „1.299,00 €“ → 1299,00) stimmt. **Aber:** Der Agent prüfte
mit einem Einzeiler statt einem Test und überging „mit Tests absichern“. Er
behauptete nicht, Tests geschrieben zu haben.

- **Behoben im Harness:** Verlangt eine Aufgabe Tests und wurde keine Testdatei
  angelegt, gilt „fertig“ einmal als „noch nicht“.

### 5 · Werkbank: Kontoauszug auswerten

Ein Kontoauszug wie aus dem Online-Banking (98 Buchungen, April bis September,
Windows-1252, Semikolons in Anführungszeichen, „2.450,00“). Gefragt: Lebensmittel je
Monat (ohne Bäckerei), regelmäßige Abbuchungen mit Jahreskosten, größte Ausgabe.

Nach 50 s: ein Skript `auswertung.py` und ein `bericht.md`. **Alle sechs
Monatssummen auf den Cent richtig**, Netflix 167,88 €, Spotify 131,88 €, Fitness
358,80 € im Jahr, dazu die Miete; größte Ausgabe 1.299,00 € (Fernseher) mit Datum.

### 6 · `dowos review` vor dem Commit

Ein Git-Projekt mit drei eingebauten Fehlern in den uncommitteten Änderungen:
SQL-Injection in der Suche, die erste Seite der Blätterfunktion bleibt leer, und
`loeschen()` ruft nie `commit()` auf.

In 14 s gefunden: die SQL-Injection (Schwere hoch, mit Angriffsbeispiel und
Abhilfe) und der Seitenfehler (mit richtiger Formel `(nummer - 1) * groesse`).
**Nicht gefunden:** das fehlende `commit()`. Nichts am Projekt verändert.
Ein Review ersetzt keine Tests; es ist ein schneller zweiter Blick.

### 7 · Bild: Screenshot → HTML-Seite

Ein Screenshot des Dive-on-Wide-Dashboards an die Werkbank, Auftrag: „als statische Seite
nachbauen, nur HTML und CSS“. Modell: Gemma 4 12B (sieht Bilder).

- **Erster Versuch: abgebrochen nach 30 min.** Zehn Antworten endeten mitten im
  Text. Ursache: Die Werkbank schätzte den Verlauf auf 10 000 Token und zählte das
  Bild nicht mit; Ollama lief bei 16 383 Token voll und schnitt ab. Die Werkbank hielt
  das für kaputtes JSON und riet „Anführungszeichen maskieren“, zehnmal.
- **Behoben im Harness:** Bilder zählen in der Schätzung mit; eine an der Grenze
  abgeschnittene Antwort wird als solche erkannt (Ollama `done_reason`,
  Token-Summe) und löst Verdichten aus; Müll hinter dem letzten Text rettet die
  JSON-Reparatur.
- **Zweiter Versuch: fertig nach 16,5 min**, 11 Schritte. Seitenleiste mit allen
  Einträgen, Kennzahlenkacheln, Systemzustand und Ereignisse samt Werten — sehr
  nah am Vorbild. Es fehlt nur die Ablaufleiste unter der Überschrift.

### 8 · Orchestrator: ein Wochenende planen

„Ein Wochenende in Leipzig für zwei Personen ohne Auto, 300 € ohne Anreise und
Hotel, mit Uhrzeiten, Essen und Kostenaufstellung.“

- **Erster Versuch: Zeitüberschreitung nach 25 min.** Der Planer wählte für die
  Recherche das dichte 27B, das in der frischen Instanz nur „knapp“ passt (7 min je
  Runde) — genau das Modell, das diesen Mac früher schon zum Absturz brachte.
- **Behoben im Harness:** Ein knapp passendes Modell kommt nur zum Zug, wenn der
  Nutzer es als Standard gewählt hat oder es hier gemessen wurde.
- **Zweiter Versuch: fertig nach 6 min.** Recherche, dann Planung; Tagespläne für
  Samstag und Sonntag, Kosten 270 € (≤ 300 €), alle Annahmen ausdrücklich als
  unbelegt markiert. **Aber** ohne funktionierende Websuche bleibt der Plan
  allgemein („Museum 1“, „Cluster C“), und er nennt den falschen Verkehrsverbund
  (VVO ist Dresden, Leipzig ist MDV/LVB).

### 9 · Deep Research mit Quellen

„Welche Neuerungen bringt Python 3.14?“ Die schlüssellose Suche hängt an
DuckDuckGo — und DuckDuckGo zeigte nach wenigen Anfragen eine Bot-Prüfung
(„Select all squares containing a duck“). Dive on Wide löst so etwas nicht.

- **Erster Versuch:** Die Suche meldete „keine Treffer“, die Ausweichquellen
  lieferten TI-Nspire, die F-16 („Rafael Python 3“) und alte arXiv-Papers — als
  Quellen. Der Bericht erkannte sie immerhin als unbelegt.
- **Behoben im Harness:** Die Bot-Prüfung wird erkannt, DuckDuckGo 15 min nicht
  mehr gefragt, und der Bericht sagt es. Treffer ohne die gefragte Version des
  Themas fliegen raus; DuckDuckGo-Anzeigen zählen nicht als Treffer.
- **Dritter Versuch:** ehrlich leer — „nichts (keine Treffer) · DuckDuckGo verlangt
  gerade eine Bot-Prüfung … Verlässlich wird die Recherche mit einem eigenen
  Suchdienst“. In einer Pause der Sperre fand dieselbe Suche docs.python.org und
  Real Python.

### 10 · Ein kleines Spiel aus einer Idee

„Eisrutsch“: Sokoban mit einer einzigen Zusatzmechanik (Eis), drei Levels, Löser,
curses. Der Orchestrator plant vier Pakete; der Treiber geht erst zum nächsten
Paket, wenn alle Tests grün sind (höchstens drei Reparaturläufe). Das ist die
Lehre aus dem Frostwerk-Test vom 28.09., der mit sieben Mechaniken scheiterte.

| Versuch | Zusätzlicher Handgriff | Paket 1 nach 3 Reparaturen | Ursache der roten Tests |
|---|---|---|---|
| 1 | kleiner Umfang, `DOWOS.md`, Tests-grün-Tor | 15 von 17 rot | Koordinaten falsch abgezählt, (x,y) und (Zeile,Spalte) gemischt |
| 2 | + „nicht abzählen, ausrechnen“, falsche Tests dürfen mit Nachweis korrigiert werden | 7 von 24 rot | Tests und Code erfinden verschiedene Rückgabewerte („ende“/„beendet“) |
| 3 | + Schnittstelle fest vorgegeben (Namen, Rückgaben, Koordinaten) | 4 von 21 rot | **Code richtig**; alle vier Tests falsch (Leerfeld zwischen Spielerin und Kiste übersehen) |
| 4 | + Tests als Vorher/Nachher-Bild statt Koordinaten | 9 von 20 rot | Fehler jetzt auf einen Blick lesbar (`#@@.#`: Spielerin doppelt gezeichnet, ein echter Code-Fehler), aber Tests nehmen `.` als Boden statt als Ziel; drei Reparaturen reichten nicht |

**Ergebnis:** Mit diesem Modell (35B, 3 B aktiv, 3 Bit) erreicht selbst das kleinste
Spielpaket kein grünes Ende ohne Eingriff. Der beste Stand war Versuch 3: Mit fest
vorgegebener Schnittstelle war der Spielcode richtig, und nur die eigenen Tests des
Modells waren falsch abgezählt. Die Schwachstelle ist das Schreiben von Tests für
Raster, nicht die Spiellogik.

**Nächster Handgriff, noch nicht gemessen:** Die Tests kommen vom Nutzer (oder werden
von ihm abgenommen), der Code vom Modell. Das ist testgetriebene Arbeit, wie sie
auch mit großen Modellen am zuverlässigsten ist.

Die Harness-Regel „vorhandene Tests nicht verändern“ blieb unverändert streng; die
Handgriffe stehen in der `DOWOS.md` des Projekts, wo ein Nutzer sie auch setzen
würde.

## Handgriffe, die wirken

- **Schnittstelle vorgeben.** Wer Namen, Rückgabewerte und Koordinatenrichtung in
  die `DOWOS.md` schreibt, verhindert, dass Tests und Code zwei verschiedene
  Programme beschreiben. Das war beim Spiel der größte Einzelschritt.
- **Kleiner Umfang je Auftrag.** Eine Mechanik statt sieben; je Regel ein Test mit
  einem winzigen Level.
- **Nicht weiter bei Rot.** Das nächste Paket erst, wenn alle Tests grün sind —
  sonst stapeln sich Fehler, wie im Frostwerk-Test. Seit 04.10.2026 in Dive on Wide
  eingebaut: Werkbank → „Roadmap abarbeiten“ (je Paket ein Lauf, danach die Tests,
  bei Rot bis zu drei Reparaturen, sonst Halt mit Begründung; „Fortsetzen“ prüft
  zuerst die Tests).
- **Für Recherche einen eigenen Suchdienst.** Ohne Schlüssel hängt die Websuche an
  DuckDuckGo und damit an dessen Bot-Prüfung. Eine eigene SearXNG-Instanz oder ein
  Schlüssel (Brave, Tavily, Serper) macht Recherche und Reiseplanung erst
  brauchbar (README: „Web-Recherche stabil machen“).
- **Für Bilder ein Bildmodell wählen** (hier Gemma 4 12B); die Werkbank lehnt
  Bilder für Modelle ohne Bildverständnis ab.

## Im Harness behoben (Commits 271bc29, f1e28ed)

| Befund | Behebung |
|---|---|
| `dowos` (Terminal) ignorierte `STORAGE_DIR` und andere Umgebungsvariablen und schrieb in den Speicher der Hauptinstanz | Umgebung schlägt `.env`, wie beim Server |
| Abgeschnittene Antworten an der Kontextgrenze galten als kaputtes JSON; Bilder fehlten in der Token-Schätzung | eigene Erkennung, eigener Hinweis, Verdichten; Bilder zählen mit |
| JSON-Rettung scheiterte an Müll hinter dem letzten Text (Gemma 4) | toleriert |
| Orchestrator nahm knappe, ungemessene Nebenmodelle | nur Standard oder hier gemessen |
| DuckDuckGo-Bot-Prüfung als „keine Treffer“, dann themenfremde Quellen; Anzeigen als Treffer | erkannt, 15 min Pause, im Bericht vermerkt; Versionsprüfung am Thema; Anzeigen raus |
| „mit Tests absichern“ still übergangen | einmal „noch nicht“ ohne neue Testdatei |

## Was dieser Test nicht zeigt

- Jeder Fall lief einmal (die korrigierten zweimal). Streuung ist nicht gemessen;
  vor dem Release werden alle zehn am Release Candidate dreimal wiederholt.
- Ein Rechner, ein Modell je Rolle. Andere Hardware und Modelle können anders
  abschneiden; die Modellvorschläge der Einrichtung sind danach ausgerichtet.
- Die Aufgaben und Prüfungen hat Claude als Messmaterial geschrieben; sie fließen
  in kein Training.
- Bewertet wurde automatisch (Zahlen, Schlüsselwörter) und danach von Hand
  gegengelesen; die Handbewertung ist in dieser Datei beschrieben, wo sie von der
  automatischen abweicht (Reiseplan).

## Drei Diver über die Zeit: Recherche, Gegenmeinung, Urteil (07.10.2026)

**Frage:** Welche Science-Fiction-Geräte werden am wahrscheinlichsten bis 2045 Realität?
**Aufbau:** drei Rhythmus-Einträge, absichtlich eng getaktet (1, 2 und 3 Minuten), verkettet über „baut auf … auf“.

| Diver | Wer | Was geschah | Dauer |
|---|---|---|---|
| 1 · Zukunftsforscher | Orchestrator, Qwen 3.6 35B-A3B | plante selbst „Deep Research, 2 Runden“, Top 5 mit Stand 2026, Hürden, Wahrscheinlichkeiten | 417 s |
| 2 · Skeptiker | Diver „Advocatus Diaboli“, **Qwen 3.5 4B** | wartete, bis Diver 1 fertig war, lud dessen Bericht und widersprach konkret (AR/VR „>90 %“ überschätzt, Energie als fehlende Kategorie), eigene Top 5 | 452 s (inkl. Warten) |
| 3 · Schiedsrichter | Diver „Kritiker“, Qwen 3.6 35B-A3B | wartete auf beide, verglich (Übereinstimmungen, Konflikte, wer stärker argumentiert), eigene Rangliste mit TRL und Prozent, Empfehlungen an beide Diver | 555 s (inkl. Warten) |

**Der erste Lauf fand zwei Fehler, beide behoben:** Diver 2 startete, während Diver 1 noch lief, bekam nichts —
und das 4-B-Modell erfand ein Tauch-Thema („Diver“). Jetzt wartet ein Eintrag auf laufende Vorgänger, startet ohne
Vorarbeit gar nicht erst, und der Kopf der Vorarbeit erklärt, was „Diver“ heißt.

**Ehrliche Grenze:** Die Websuche ohne eigenen Suchdienst fand kaum belastbare Quellen; Diver 1 kennzeichnete seine
Prozentzahlen deshalb als „nicht belegt“, und Diver 3 kritisierte genau das. Mit SearXNG oder einem Such-API-Schlüssel
(Einstellungen → Web-Recherche) wird die Grundlage besser.
