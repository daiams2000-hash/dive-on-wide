# Ausgang — Arbeit auslagern, Daten nicht

Dive on Wide läuft auf deinem Rechner. Das ist der Sinn der Sache: Deine Dateien, deine Chats, deine Modelle bleiben hier.

Es gibt aber einen zweiten, sehr nützlichen Betrieb: **ein fremdes Gerüst befehligt Dive on Wide**. Claude Code, ein anderer Agent oder ein Skript schickt eine Aufgabe, das **lokale** Modell arbeitet sie ab, und nur die Antwort geht zurück. Das spart dem Gerüst sehr viel Arbeit — und dir sehr viel Geld, wenn dieses Gerüst nach Token abgerechnet wird.

Gemessen an einem echten Auftrag am 18.09.2026:

| Weg | Aufwand auf der Gegenseite |
|---|---|
| Ein fremdes Gerüst klickt die Oberfläche durch (Bildschirmfotos, Elementlisten) | 1 000–3 000 Token **je Klick** |
| Dasselbe über die Harness-Schnittstelle | ~150–300 Token für Auftrag plus Abfrage |
| Rhythmus, Mesh, `dowos` im Terminal | nichts — es ist niemand von außen beteiligt |

## Die Frage, um die es wirklich geht

Sobald etwas hinausgeht, ist es draußen. Ein fremdes Gerüst kann nicht glaubhaft versprechen, etwas „nicht zu lesen": Was in seinem Kontext landet, ist bei seinem Anbieter. Deshalb gilt hier ein einziges Prinzip:

> **Die Seite, die die Daten besitzt, entscheidet, was hinausgeht — nicht die Seite, die fragt.**

Praktisch heißt das: Dive on Wide beantwortet Anfragen von außen nur so ausführlich, wie **du** es eingestellt hast, filtert jede Antwort und schreibt jede Antwort in ein Buch.

## Vier Stufen

Einstellungen → **Ausgang** (oder `AUSGANG_STUFE` in der `.env`):

| Stufe | Was ein fremdes Gerüst erfährt |
|---|---|
| **aus** *(Voreinstellung)* | Nichts. Aufträge von außen werden abgewiesen. |
| **urteil** | Nur Maschinenfakten: fertig oder gescheitert, Schritte, Dauer, Werkzeugzählung, **Anzahl** geänderter Dateien. Kein Freitext, keine Dateinamen. |
| **zusammenfassung** | Dazu der Abschlusssatz des Agenten, die Schrittliste mit Werkzeugnamen und die **Namen** der geänderten Dateien. Keine Dateiinhalte, keine Befehlsausgaben. |
| **alles** | Das vollständige Protokoll, so viel wie die Oberfläche selbst zeigt. |

So sieht derselbe Auftrag auf Stufe `urteil` aus — gemessen, nicht ausgedacht:

```json
{"id": "36a43f55a762", "zustand": "fertig", "stufe": "urteil",
 "schritte": 4, "sekunden": 14.9, "unlesbare_antworten": 0,
 "werkzeuge": {"schreiben": 1, "lesen": 1, "fertig": 1},
 "dateien": {"neu": 1, "geaendert": 0, "geloescht": 0}}
```

285 Zeichen. Das Gerüst weiß, dass die Arbeit erledigt ist — und **nicht**, wie die Datei heißt, was darin steht oder was das Modell gedacht hat. Auf Stufe `zusammenfassung` werden daraus 634 Zeichen mit Dateinamen und Schrittliste. Der Unterschied ist nicht Geschmackssache, er ist geprüft (`tests/run_tests.py --nur ausgang`).

## Der Filter

Jede hinausgehende Antwort läuft durch `ausgang.filtern`, unabhängig von der Stufe:

- private Schlüssel (`-----BEGIN … PRIVATE KEY-----`), API-Schlüssel (`sk-…`, `ghp_…`, `AIza…`, `dow_…`), Bearer-Token
- Zuweisungen mit `KEY`, `TOKEN`, `SECRET`, `PASS`, `CREDENTIAL` im Namen — der **Name** bleibt stehen, damit die Lücke erklärt ist, der Wert nicht
- Mailadressen
- Pfade: der Arbeitsordner wird relativ (`./tests/x.py`), jeder andere Heimatpfad verschwindet ganz

Harmloses bleibt unangetastet: `ITERS = 300` und `Tests: 8 PASS` gehen unverändert hinaus.

## Das Ausgangsbuch

Jede Antwort nach draußen wird angefügt — nie geändert:

```
18.09. 10:18  Claude Code      zusammenfassung /api/extern/auftrag/36a4    634 Zeichen
18.09. 10:17  Claude Code      urteil          /api/extern/auftrag/36a4    285 Zeichen
18.09. 10:17  Claude Code      urteil          /api/extern/auftrag         112 Zeichen
Heute 6 Antworten, 1.6 kB nach draußen — Claude Code (5), Besitzer (lokal) (1)
```

Zu sehen in Einstellungen → Ausgang und im Lagebild, im Terminal mit:

```bash
python3 dowos_cli.py ausgang buch
python3 dowos_cli.py ausgang summe
```

Das Buch enthält bewusst **keine Inhalte**, nur Empfänger, Pfad, Stufe, Zeichenzahl und was der Filter entfernt hat. Ein Protokoll, das selbst zur Datenspur wird, wäre das Gegenteil des Zwecks.

## Die Schranke

Ein Gerüst-Schlüssel (Einstellungen → Ausgang → *+ Gerüst-Schlüssel*) hat die Rolle `harness` und kommt **ausschließlich** an `/api/extern/…`. Jeder andere Pfad antwortet mit 403:

```
Dieser Schlüssel darf nur die Harness-Schnittstelle benutzen (/api/extern/…).
Alles andere bleibt auf diesem Rechner.
```

Einstellungen, Chats, Dokumente, Wissen, Dateien, Modelle: unerreichbar. Auch die Rechte des Agenten kann ein Gerüst nicht mitschicken — Rechtestufe und Freigabe kommen aus den Einstellungen dieser Instanz.

## Die Schnittstelle

Drei Aufrufe, mehr braucht es nicht:

```bash
# Was gibt diese Instanz überhaupt heraus?
curl -H "X-Auth-Token: $KEY" http://localhost:3000/api/extern/info

# Aufgabe starten (das lokale Modell arbeitet)
curl -X POST -H "X-Auth-Token: $KEY" -H "Content-Type: application/json" \
     -d '{"aufgabe": "Die CSV-Ausgabe verliert Umlaute — finde die Ursache und behebe sie."}' \
     http://localhost:3000/api/extern/auftrag

# Nachsehen, was daraus wurde
curl -H "X-Auth-Token: $KEY" http://localhost:3000/api/extern/auftrag/<id>
```

`/api/extern/info` antwortet auch bei abgeschaltetem Ausgang — ein Gerüst soll erfahren, **warum** es nichts bekommt, statt im Dunkeln zu raten.

## Was das in der Praxis bedeutet

Ein Auftrag, bei dem das lokale Modell 964 Trainingsbeispiele mit über 2 MB Text durchgearbeitet hat, hinterließ beim fremden Gerüst acht Zahlen. Die 2 MB haben den Rechner nie verlassen — nicht, weil jemand es versprochen hat, sondern weil sie nie in einer Antwort standen. Und im Ausgangsbuch steht, wie viel es wirklich war.
