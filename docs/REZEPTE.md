# Trainingsrezepte

Ein **Rezept** ist ein Dokument, das einen ganzen Trainingslauf beschreibt: welches
Modell, welche Daten, welches Verfahren, welche Prüfung. Man kann es sichern,
weitergeben und in einem halben Jahr noch verstehen — anders als acht Zahlen, die
irgendwann in Formularfelder getippt wurden.

Die Idee stammt aus dem Projekt [Soup](https://github.com/MakazhanAlpamys/Soup)
(Feintuning über eine einzige Konfigurationsdatei, `batch_size: auto`). Dive on Wide setzt
sie mit eigenen Grundsätzen um:

* **„auto“ heißt nicht „egal“.** Zu jeder automatisch gesetzten Zahl gehört ein Satz,
  warum sie so ist — und wo möglich stammt sie aus **gemessenen** eigenen Läufen
  (Schritte je Sekunde, Spitzenspeicher aus deinen Trainingslogs).
* **Die Daten werden vorher untersucht**, nicht hinterher beklagt.
* **Undichte Daten starten nicht.** Stehen Prüfbeispiele auch im Training, bricht
  Dive on Wide ab: sonst misst der Val-Loss Auswendiglernen.
* Ausgeführt wird über die vorhandene Trainings-Werkbank — Lagebild, Frühstopp und
  „ein schwerer Auftrag zur Zeit“ gelten weiter.

## Ein Rezept

```yaml
name: Werkbank-Agent
basis: qwen3-4b-2507-mlx-4bit     # Name eines Modells aus Training → Modelle
aufgabe: sft

daten:
  quelle: storage/training/daten/uebung_20260914_1950   # Datei oder Ordner
  format: auto            # auto | chat | sharegpt | alpaca | frage_antwort | text
  val_anteil: 0.1
  max_laenge: auto        # auto = 95-%-Länge deiner Daten, auf 256 gerundet
  ziel: ""                # wohin der gebaute Datensatz kommt (leer = automatisch)

training:
  epochen: 1
  lr: auto
  batch: auto
  grad_accum: auto
  lora:
    rang: auto
    schichten: auto
  geduld: 3               # Frühstopp nach so vielen Prüfungen ohne Fortschritt
  seed: 17

pruefen:
  pruefstand: 20
  datenwert_seeds: 3

notiz: |
  Wofür dieses Modell gedacht ist.
```

Statt `daten.quelle` (Rohdaten, die Dive on Wide umformt und aufteilt) kann auch
`daten.fertig` stehen — ein Ordner, der schon `train.jsonl` und `valid.jsonl` enthält.

Geschrieben wird YAML (eine klar umrissene Teilmenge: Einrückung, `schlüssel: wert`,
Listen mit `-`, Blocktexte mit `|`) oder JSON. Was die Teilmenge nicht kennt, wird
**mit Zeilennummer abgelehnt** statt stillschweigend anders verstanden.

## Bedienung

In der Oberfläche: *Training → 2b · Rezept*. Im Terminal:

```bash
dowos rezept vorlagen               # welche Vorlagen es gibt
dowos rezept vorlage werkbank > mein.yaml
dowos rezept pruefen mein.yaml      # nur die Daten untersuchen
dowos rezept plan mein.yaml         # vollständiger Bericht, ohne etwas zu schreiben
dowos rezept starten mein.yaml      # Datensatz bauen und Training starten
```

`plan` schreibt nichts und startet nichts — es sagt nur, was passieren würde.

## Was die Datenprüfung findet

| Befund | Warum das zählt |
|---|---|
| unlesbare Zeile | kaputtes JSON — sonst fehlt die Hälfte der Daten unbemerkt |
| unbekanntes Format | ein Beispiel, das zu keinem der fünf Formate passt |
| leere Antwort | ohne Antwort lernt das Modell nichts |
| Dublette | mehrfach gleiche Beispiele verschieben das Gewicht |
| sehr kurze Antwort | meist ein Fehler beim Erzeugen |
| Rollenfolge | beginnt mit der Antwort, oder zwei Antworten hintereinander |
| zu lang | wird beim Training abgeschnitten |
| gemischtes Format | geht, sollte aber Absicht sein |
| **Überschneidung Training/Prüfteil** | der stillste Messfehler überhaupt — bricht ab |

Urteil: **geeignet**, **mit Auflagen** oder **nicht geeignet** (dann startet nichts).

Die Längen werden aus den Zeichen geschätzt und auch so ausgewiesen
(„geschätzt (3,6 Zeichen je Token)“). Wer genau zählen will, kann den Tokenizer des
Modells benutzen (`rezept.tokenizer_zaehler`) — dafür braucht es das Trainer-Python
mit `transformers`.

## Erkannte Datenformate

| Format | Sieht so aus |
|---|---|
| `chat` (ChatML/OpenAI/Dive on Wide) | `{"messages": [{"role": "user", …}, {"role": "assistant", …}]}` |
| `sharegpt` | `{"conversations": [{"from": "human", "value": …}, …]}` |
| `alpaca` | `{"instruction": …, "input": …, "output": …}` |
| `frage_antwort` | `{"question"/"frage"/"prompt": …, "answer"/"antwort"/"completion": …}` |
| `text` | `{"text": …}` |

Alles wird in das Nachrichtenformat gebracht, das mlx-lm erwartet. Bei `alpaca` wird
`input` an die Frage angehängt, damit nichts verloren geht.

## Aufteilen ohne Leck

Der Prüfteil wird über den **Fingerabdruck** des Beispiels bestimmt, nicht über den
Zufall der Reihenfolge. Folgen: dieselbe Aufteilung bei jedem Lauf, und wenn später
Beispiele dazukommen, wandert kein altes Prüfbeispiel ins Training.

## Was ein Rezept nicht kann

* **Andere Verfahren als SFT** (`dpo`, `orpo`) sind nicht gebaut — das Rezept sagt das
  mit Grund, statt etwas Ähnliches zu tun.
* **Wissen einspeichern.** Feintuning lernt Form und Tonfall, nicht Wahrheit. Für
  Fakten ist Nachschlagen (Gedächtnis, Dateien, Werkzeuge) fast immer besser.
* **Beweisen, dass es geholfen hat.** Die automatisch gesetzten Zahlen sind
  Erfahrungswerte. Ob ein Datensatz den Schüler wirklich besser macht, zeigt erst der
  Datenwert-Test über mehrere Seeds (siehe [DESTILLATION.md](DESTILLATION.md)).
