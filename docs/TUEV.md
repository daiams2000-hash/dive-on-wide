# Der TÜV für Trainingsdaten

Es gibt viele Werkzeuge, die ein Modell feintunen — Axolotl, LLaMA-Factory,
Unsloth, torchtune, mlx-lm. Am Ende liefern sie alle dasselbe: **einen Adapter und
eine Verlustkurve.** Die Frage, auf die es ankommt, beantwortet keines davon:

> Ist dieser Datensatz sein Training wert — und misst der Prüfsatz danach
> überhaupt noch etwas?

Der TÜV beantwortet den Teil, der **ohne GPU** geht. Er ist bewusst herauslösbar:
reine Standardbibliothek, kein Dive-on-Wide-Server, kein Mac nötig. Er prüft auch
Datensätze, die mit anderen Werkzeugen entstanden sind.

```bash
dowos tuev <datensatz> [--pruefsatz <ordner|datei>] [--bericht aus.md] [--json]
python3 tuev.py <datensatz> …        # ohne Dive on Wide, überall
```

## Was geprüft wird

| Prüfung | Frage | Folge |
|---|---|---|
| **Gesundheit** | Format, Dubletten, leere Antworten, Rollenfolge, Längen | Auflage oder Sperre |
| **Leckage** | Stehen Prüfbeispiele auch im Training? | **Sperre** — der Val-Loss misst dann Auswendiglernen |
| **Verseuchung** | Steckt der Prüfsatz schon in den Trainingsdaten? | **Sperre** ab 5 % der Aufgaben |
| **Herkunft** | Wer hat die Daten erzeugt, darf man damit trainieren? | **Sperre**, wenn das Manifest es verbietet |
| **Siegel** | Prüfsumme über den Inhalt | ordnet Bericht und Datensatz einander zu |

Urteil: **geeignet**, **mit Auflagen** oder **nicht geeignet**.

## Verseuchung — der stillste Messfehler

Ein Modell, das die Prüfaufgaben im Training gesehen hat, sieht großartig aus und
kann nichts. Gemessen wird über **acht Wörter lange Ketten**: Für jede Prüfaufgabe
wird der Anteil ihrer Wortketten bestimmt, der auch in den Trainingsdaten vorkommt.
Acht Wörter sind lang genug, dass zufällige Übereinstimmung selten ist, und kurz
genug, dass eine leicht umformulierte Aufgabe noch auffällt.

Am eigenen Bestand geprüft (17.09.2026):

| Datensatz | Prüfsatz | Ergebnis |
|---|---|---|
| `uebung_20260914_1950` (964 Beispiele) | Prüfstand (20 Aufgaben) | **0 % — sauber** |
| derselbe | Fabrik-Aufgaben (200) | **74 %** — erwartet, denn der Datensatz *ist* deren Lösung |

Der zweite Fall zeigt, wofür das Werkzeug da ist: Es zwingt dich, ausdrücklich zu
sagen, welcher Satz der Prüfsatz ist. Wer Übungs- und Prüfaufgaben vermischt, merkt
es sonst nie.

## Längen-Doktor: geteilt statt abgeschnitten

Ein Trainingslauf kann aus einem Grund scheitern, der nichts mit dem Datensatz zu tun hat: Der Trainer schneidet jedes Beispiel bei `max_seq` ab. Bei einem Mehrrunden-Gespräch verschwindet dabei die **letzte Antwort** — und damit alles, woran gelernt werden soll. Der Verlust ist dann 0/0, also NaN; die LoRA-Gewichte werden NaN, und ab da ist jeder weitere Schritt NaN. Der Lauf sieht „fertig" aus und der Adapter ist Schrott.

Gemessen am 17.09.2026 am ersten echten Datenwert-Lauf: `max_seq 3072`, 141 von 964 Beispielen länger als das, Verlust ab Schritt 1 NaN.

Deshalb geht jeder Datensatz vor dem Training durch den Längen-Doktor (`datenwert.daten_passend_machen`):

- gemessen wird mit dem **echten Tokenizer des Grundmodells**, nicht geschätzt;
- zu lange Gespräche werden **zwischen Frage-Antwort-Paaren** geteilt — jedes Stück behält die Systemzeile und endet mit einer vollständigen Antwort;
- ein einzelnes Paar, das allein nicht passt, wird **gemeldet und gezählt**, nicht verstümmelt;
- der Bericht (vorher/nachher, geteilt, verworfene Paare, längstes Beispiel) landet im Zustand des Laufs und ist damit nachweisbar.

Am echten Datensatz (964 Übungsbeispiele, `max_seq 3072`): 141 Beispiele geteilt, 1105 Beispiele danach, **kein einziges Paar verworfen**. Nichts ging verloren, und die NaN-Klasse ist damit weg.

Dazu gehört die zweite Vorbereitung: Vor jedem Training werden geladene Ollama-Modelle freigegeben. Gemessen am 18.09.2026: `max_seq 4096` (Spitze 9,6 GB) starb auf 24 GB nach 14 Minuten an `[METAL] Insufficient Memory`, weil daneben noch ein Chatmodell lag.

---

## Abnahmefahrt: drei Anfragen statt acht Stunden

Am 18.09.2026 lief die erste echte Messung eines trainierten Adapters **75 Minuten** und endete mit **0 von 12** Kontrollaufgaben — gegen 7 von 12 des Grundmodells. Das sah nach „der Datensatz schadet" aus. Die Diagnose zeigte etwas anderes:

```
Schritt 1: lesen {"pfad": "noten.py"}        ← sauber
Schritt 2: lesen {"pfad": "tests/..."}       ← sauber, aber der Gedanke wird lang
Schritt 3: unlesbare Antwort (JSON nicht lesbar: Unterminated string …)
Schritt 4-6: dieselbe unlesbare Antwort, Kontext 1941 → 11252 Token
```

Der Adapter wiederholte ab dem dritten Schritt denselben Satz, bis das Token-Limit griff — das JSON war abgeschnitten und damit unlesbar. Zusätzlich standen Fremdmarken (`</think>`) vor der Antwort. Das Grundmodell zeigt beides nicht. **0 von 12 war kein Ergebnis über den Datensatz, sondern ein Gerüstfehler.**

Deshalb gibt es die Abnahmefahrt (`datenwert.abnahme`), die nach jedem Training läuft und vor jeder Messung entscheidet:

- Proben sind die **längsten** Beispiele des Datensatzes, Kontext bis zur letzten Antwort — genau der Ausschnitt, auf den das Training zielt, und genau die Länge, bei der der Adapter entgleiste. Kurze Proben hätten nichts gefunden.
- Geprüft wird nur, was ohne Aufgabenlösung feststellbar ist: **lesbares Protokoll** (JSON mit `gedanke`/`werkzeug`), **keine Endlosschleife** (derselbe Satz dreimal), **keine Fremdmarken** aus der Vorlage.
- Ein durchgefallener Adapter wird **nicht mit 0 bewertet, sondern gar nicht gemessen** — 0 wäre eine Aussage über den Datensatz, und die wäre falsch. Der Lauf bricht mit Grund und Handlungsvorschlag ab (weniger Iterationen, kleinere Lernrate, kleinerer Rang).

Kosten: drei Anfragen, rund eine Minute. Gespart: die Messung eines Satzes, gemessen 4–8 Stunden.

---

## Was der TÜV ausdrücklich NICHT prüft

Jeder Bericht endet mit dieser Liste — sie ist kein Kleingedrucktes, sondern der
Kern der Haltung:

* **Wirkung auf den Schüler.** Ob der Datensatz das Modell besser macht, zeigt erst
  der Datenwert-Test über mindestens drei Seeds (`datenwert.py`). Bis dahin steht
  dort `null`, nicht 0.
* **Transfer** auf fremde Aufgaben.
* **Inhaltliche Richtigkeit** einzelner Antworten. Der TÜV prüft Form, Herkunft und
  Überschneidung — nicht, ob eine Antwort stimmt.

## Fremde Konfigurationen mitbringen

```bash
dowos fremd mein_axolotl.yaml > mein_rezept.yaml
```

Übersetzt Axolotl, LLaMA-Factory, mlx-lm und Unsloth-Konfigurationen in ein Rezept.
Übernommen wird, was eine Entsprechung hat; **was keine hat, wird genannt** — mit
Grund:

```
# Nicht übernommen:
#   deepspeed = "zero2.json" — Dive on Wide trainiert auf einem Rechner, nicht auf einem Cluster.
#   flash_attention = true — MLX bringt seine eigene Aufmerksamkeit mit.
#   wandb_project = "mein-projekt" — Dive on Wide protokolliert lokal, nicht in einem fremden Dienst.
```

Eine Übersetzung, die stillschweigend die Hälfte verliert, ist schlimmer als keine:
Man trainiert dann etwas anderes, als man glaubt.

## Fehlerbuch: Gerüst oder Modell?

```bash
dowos fehlerbuch storage/werkbank
```

Wertet die Schrittprotokolle aus und ordnet jeden Fehlschlag einem Teil des Gerüsts
zu — Werkzeug, Sandbox, Netz, Format, Rechner. Aus den Läufen vom 16.09.2026:

| Fehlschlag | Teil | Anteil der Schritte |
|---|---|---|
| Suche liefert nichts | Netz | 23 % |
| ersetzen trifft nicht | Werkzeug | 5 % |
| fehlendes Paket / Installationsversuch | Sandbox | 4 % |

Kein einziger Fehlschlag jener Sitzung lag am Modell. Das ist der Grund, warum sich
Arbeit am Gerüst vor Arbeit am Modell lohnt.

### Strategieprobe

Zusätzlich wird am aufgezeichneten Verlauf durchgerechnet, was eine andere
Abbruchregel gebracht hätte: *„nach k gleichen Fehlversuchen abbrechen"* — wie viele
Schritte hätte das gespart, und hätte es dabei einen Lauf zerstört, der später doch
noch fertig wurde?

**Die Grenze dieser Methode gehört dazu:** Nachspielbar ist nur, was den
**Zeitpunkt** einer Entscheidung ändert. Wer den Prompt ändert, ändert die
Antworten des Modells — dafür sagt das aufgezeichnete Protokoll nichts, und es
braucht echte Läufe. Wer diese Grenze verwischt, misst Wunschdenken.
