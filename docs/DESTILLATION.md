# Destillation — Spitzenmodelle als Lehrer für eigene, lokale Modelle

*Training → Destillation mit Lehrermodellen.* Große Modelle lösen Übungsaufgaben
zu deinen Themen, Dive on Wide prüft jede Lösung unabhängig, und aus dem, was die
Prüfung besteht, wird je Thema ein Datensatz für ein kleines lokales Modell.

> **Nutzungsbedingungen.** Aufgaben, Code und Lösungswege gehen an den Anbieter
> des Lehrers. Viele Anbieter untersagen, mit ihren Ausgaben Modelle zu
> entwickeln, die mit ihnen konkurrieren. Ob deine private Nutzung erlaubt ist,
> entscheidest du anhand der Bedingungen deines Anbieters. Dive on Wide verlangt vor
> dem ersten Auftrag eine Bestätigung je Anbieter.

---

## Ablauf

```
Themen ─► Aufgabenfabrik ─► Lehrer löst als Werkbank-Agent ─► Orakel ─► Tore ─► Label ─► Datensatz je Thema
```

1. **Lehrer einrichten:** Unter *Einstellungen → Modelle & Provider* einen Anbieter
   anlegen — OpenAI, Anthropic (`https://api.anthropic.com/v1`), Moonshot/Kimi,
   OpenRouter oder jeder andere OpenAI-kompatible. Neue Modelle sind nur neue
   Namen; nichts ist fest eingebaut.
   **Austauschbar:** Welches Modell gerade führt, ändert sich. Unter *Training → Rollen →
   Lehrer* stellst du den Standard-Lehrer um; die Destillation übernimmt ihn als ersten
   Lehrer. Aufträge speichern, welcher Lehrer welche Probe erzeugt hat — ein Wechsel
   macht alte Proben nicht ungültig.

   **Abo-Lehrer (Claude Code, zum Testen):** Opus 5, Fable 5.1 oder Sonnet 5 über dein
   Claude-Abo — ohne Token-Kosten, aber auf das 5-Stunden- und Wochenlimit angerechnet.
   Jeder Schritt ist ein Aufruf `claude -p` ohne Werkzeuge, ohne Sitzungsspeicher, in einem
   leeren Ordner. Ist das Limit erreicht, pausiert der Auftrag (*Kontingent*) und läuft beim
   nächsten Start weiter; *Abo: höchstens Aufrufe* begrenzt einen Auftrag zusätzlich. Einmal
   nötig: im Terminal `claude` starten und `/login`.
   **Die Bedingungen von Anthropic schränken ein, Claude-Ausgaben zum Trainieren von
   KI-Modellen zu verwenden.** Proben eines Abo-Lehrers prüfen deshalb nur das System —
   Orakel, Tore, Kosten- und Schrittstatistik. Der Export übernimmt sie nie in einen
   Datensatz, auch nicht als Negativbeispiel; Fabrik und „Lösen lassen“ lehnen einen
   Abo-Lehrer ab. Für echte Datensätze einen Lehrer wählen, dessen Bedingungen das erlauben.

2. **Preis eintragen:** Dollar je 1 Mio. Eingabe- und Ausgabe-Token. Bei OpenRouter
   holt *Preise von OpenRouter holen* sie selbst. Lokale Lehrer (Ollama, Server auf
   127.0.0.1) kosten nichts.
3. **Themen:** eines je Zeile, z. B. „CSV-Import mit fehlerhaften Zeilen“. Die Fabrik
   entwirft dazu kleine Python-Projekte mit versteckten Tests und prüft jeden Entwurf,
   bevor er Aufgabe wird. Alternativ die vorhandenen Übungsaufgaben nehmen.
4. **Kosten schätzen:** niedrig / erwartet / hoch — aus den gemessenen Werten deiner
   bisherigen Läufe (Schritte, wachsender Kontext je Schritt, Antwortlänge,
   Ausbeute der Fabrik). Ohne eigene Läufe mit vorsichtigen Annahmen; ein Probelauf
   mit einer Aufgabe macht die Schätzung genauer.
5. **Budget setzen und starten.** Das Budget ist eine harte Grenze: Vor jedem
   Lehreraufruf wird der ungünstigste Fall reserviert (Eingabe plus volle maximale
   Ausgabe). Reicht der Rest nicht, endet der Auftrag sauber. Abgerechnet wird mit den
   Token, die der Anbieter meldet. Reißt eine Verbindung ab, wird vorsichtshalber der
   ungünstigste Fall verbucht.
6. **Proben ansehen, Datensatz exportieren**, dann wie gewohnt trainieren.

**🗄 Archivieren** sichert einen Auftrag vollständig — Aufgaben, Lösungswege, Orakel-Befunde,
Lehreraufrufe, Kosten — mit SHA-256 je Datei, etwa auf eine externe SSD. Was Budget oder
Abo-Kontingent gekostet hat, muss nie neu erzeugt werden. Die Aufgaben liegen im
Prüfstand-Format und eignen sich als **Prüfsatz**: Andere Schüler werden darauf gemessen
(`python3 pruefstand/stufe2.py --aufgaben-ordner <archiv>/aufgaben --modelle …`), und sie
dienen als Kontrollaufgaben des Datenwert-Tests. Eine Aufgabe, die ein Abo-Lehrer geschrieben
hat, bleibt für Trainingsdaten gesperrt — auch wenn später ein anderer Lehrer sie löst.

Beim Export hält Dive on Wide je Thema ein Fünftel der Aufgaben als **Kontrollaufgaben** zurück
(`holdout.json`): Auf ihnen misst der Datenwert-Test, ob ein Schüler durch den Datensatz besser wird.

**Aufgaben verteilen nach Lernlücke** (Standard): Bei mehreren Themen bekommt die nächste
Aufgabe das Thema mit dem höchsten erwarteten Nutzen — Ausbeute der Fabrik × Anteil
GOLD-Proben des Lehrers × Lernlücke des Schülers (aus früheren Datenwert-Tests) ÷ schon
vorhandene GOLD-Proben. Die Ausbeuten werden als Wahrscheinlichkeiten gezogen
(Thompson-Sampling): Ein Thema ohne Erfahrung wird ausprobiert, eines, bei dem der Lehrer
immer scheitert oder der Schüler schon alles kann, bekommt kaum noch Budget.

Mehrere Lehrer lösen dieselbe Aufgabe — der Datensatz begrenzt, wie viel von einem
Lehrer stammen darf, damit das kleine Modell nicht nur einen Stil nachahmt.

---

## Wie geprüft wird

Dem Lehrer wird nichts geglaubt. Ob eine Lösung stimmt, entscheidet ein
**unabhängiges Orakel** (`orakel.py`):

- Es baut eine frische Kopie: Aus dem Arbeitsordner des Lehrers kommen nur die
  Quelldateien, die Tests stammen aus dem Aufgabenspeicher.
- Es läuft in der Sandbox, ohne Netz, ohne Schlüssel, mit Python im Isolationsmodus;
  der Projektordner steht hinter der Standardbibliothek, damit keine Datei
  `unittest.py` das echte Modul ersetzt.
- Es meldet Manipulation: veränderte Tests, `sitecustomize.py`, `.pth`-Dateien,
  Dateien mit Namen von Standardmodulen, Symlinks.

Daraus werden **Tore**, jedes PASS, FAIL oder UNVERIFIED — nicht Gemessenes ist nie 0:

| Tor | Prüft |
|---|---|
| G0 Infrastruktur | Sandbox vorhanden |
| G1 Ausführung | Der Lehrer hat wirklich Befehle ausgeführt und den Lauf beendet |
| G2 Konsistenz | Behauptete und tatsächliche Änderungen stimmen, Digests stimmen, „fertig“ widerspricht nicht dem Orakel |
| G3 Orakel | Sichtbare und versteckte Tests bestehen, keine Manipulation |
| G4 Robustheit | Dasselbe unter anderem Hash-Seed und umgekehrter Testreihenfolge |
| G5 Kausalität | Ohne Änderung scheitert es, mit besteht es — und welche Datei es trägt (Ablation) |
| G6 Transfer | Datenwert-Test: Wird der Schüler auch auf dem Prüfstand (anderes Thema) besser? |
| G7 Schüler-Uplift | Datenwert-Test: Wird der Schüler auf den Kontrollaufgaben des Themas besser? |

**Labels** folgen allein aus den Toren:

| Label | Bedeutung | Im Datensatz |
|---|---|---|
| GOLD_CAUSAL | alle Tore bestanden | ja |
| GOLD_EXECUTION | G0–G5 bestanden | ja |
| SILVER | nur bis zum Orakel belegt | auf Wunsch |
| BRONZE | echte Ausführung, Lösung scheitert — Kontrast | nein |
| REJECT | Manipulation oder „fertig“ gegen das Orakel | nein |
| UNVERIFIED | Infrastruktur fehlt | nein |

**Kategorien** halten auch fest, was schiefging: `verified_success`,
`verified_recovery` (erst roter, dann grüner Testlauf), `verified_failure`,
`verified_bailout`. Gescheiterte Lösungen landen im Export getrennt in
`negativ.jsonl` — als Kontrast, nie als Vorbild.

Jede Probe (`storage/training/destillation/<auftrag>/proben/`) enthält Rohfakten
jedes Orakellaufs (Befehl, Zeiten, Exit-Code, SHA-256 der Ausgaben), SHA-256-Digests
mit genauer Eingabedefinition, die Provenance jedes Lehreraufrufs (angefragtes und
antwortendes Modell, Hash des System-Prompts, Token, Kosten), die Evidenzkette und
eine Selbstprüfung.

---

## Datenwert-Test (G6, G7)

*Destillation → Datenwert-Test.* Der Verlauf nannte das den entscheidenden Schritt: den
Wert eines Datensatzes messen statt behaupten.

- Variante **A**: das Schüler-Grundmodell. Variante **AB**: dasselbe, trainiert mit dem
  Themen-Datensatz. Gleiches Trainingsbudget (Iterationen, Lernrate, Rang; ohne
  Frühstopp), je Seed ein Training.
- Gemessen wird auf Aufgaben, die in keinem Training vorkommen: den **Kontrollaufgaben**
  des Themas (G7) und dem **Prüfstand** (G6 Transfer).
- Auswertung gepaart je Seed: Differenz der Lösungsquote mit 95-%-Intervall.
  **PASS** nur, wenn das Intervall ganz über 0 liegt; **FAIL**, wenn es darunter liegt
  (der Datensatz schadet); sonst **UNVERIFIED**. Ein Seed allein ergibt nie PASS.
- Das Ergebnis landet in genau den Proben, die im Datensatz waren, und ihr Label wird
  neu berechnet. Mit G6 und G7 PASS wird aus GOLD_EXECUTION **GOLD_CAUSAL**.

Rechne mit mehreren Stunden GPU je Test (je Seed ein Training und zwei Messreihen).

## Was (noch) nicht geht

- **Nur ausführbar prüfbare Python-Aufgaben.** Für Wissens- oder Text-Datensätze
  gibt es kein Orakel; sie könnten höchstens SILVER werden und sind noch nicht eingebaut.
- **GOLD_CAUSAL kostet GPU-Stunden:** Es braucht den Datenwert-Test (unten) mit
  positivem Ergebnis auf den Kontrollaufgaben *und* auf dem Prüfstand.
- **Grenze des Orakels:** Getesteter Code und Tests laufen in einem Prozess. Code, der
  gezielt den Testläufer manipuliert, könnte ein Ergebnis fälschen. Manipulationsprüfung
  und Varianten verringern das, ein Beweis gegen einen gezielten Angreifer ist es nicht.

Hintergrund und Herleitung: `doku/DESTILLATION.md` (nicht im Repository).
