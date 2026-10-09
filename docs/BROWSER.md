# Der Browser-Agent

Dive on Wide kann einen echten Browser steuern: Seiten öffnen, klicken, tippen, lesen —
gesteuert von deinem lokalen Modell. Das ist etwas anderes als die Websuche
(`websuche`/`webseite`), die nur Text abruft. Beides hat seinen Platz, siehe unten.

## Einrichtung

Der Browser-Agent benutzt [browser-use](https://github.com/browser-use/browser-use).
Das Paket zieht mehrere hundert Megabyte nach — Dive on Wide selbst bleibt bei der
Standardbibliothek. Deshalb liegt die Abhängigkeit in einer **eigenen Umgebung**
und wird als Unterprozess gerufen, genau wie das Training:

```bash
python3 -m venv ~/DowOS/venv-browser
~/DowOS/venv-browser/bin/pip install browser-use ollama
```

Dive on Wide findet diese Umgebung von selbst (`~/DowOS/venv-browser`, daneben oder im
Programmordner). Liegt sie woanders, trägt man den Pfad unter `BROWSER_PYTHON` ein.
Ein Chrome oder Chromium auf dem Rechner genügt — ein extra Chromium-Download ist
seit browser-use 0.13 nicht mehr nötig.

Stand und Fehlendes zeigt *Deep Research → Browser-Agent*.

## Zwei Modi, auswählbar

| | Eigener Browser | Mein Chrome |
|---|---|---|
| Profil | eigenes, leeres (`~/.dowos-browserprofil`) | dein laufendes Chrome |
| Anmeldungen | keine | **deine** |
| Einrichtung | nichts weiter | Chrome mit Fernsteuerung starten |
| Wofür | öffentliche Seiten, Recherche | Seiten hinter Anmeldung, eigene Konten |

**Mein Chrome** braucht einen Chrome mit offener Fernsteuerung. Chrome ganz beenden
und einmal so starten:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
```

Dann im Browser-Agenten `http://127.0.0.1:9222` eintragen. **Ehrlich dazu:** In diesem
Modus arbeitet der Agent mit deinen Cookies und Anmeldungen und sieht alles, was du
eingeloggt siehst. Seiteninhalte können außerdem Anweisungen enthalten, die ein Modell
befolgt. Für Recherche im offenen Netz ist der eigene Browser die bessere Wahl.

## Zwei Steuerungen, auswählbar

Wie der Agent den Browser bedient, ist eine zweite Wahl — unabhängig davon,
welchen Browser er benutzt.

### Dive on Wide (Vorgabe)

Derselbe Aufbau wie bei Computer-Use: Der **Planer** (dein lokales Modell)
entscheidet, was zu tun ist. Als Augen bekommt er die **Elementliste der Seite** —
nummeriert, als Text:

```
[41]<label id=vector-main-menu-dropdown-label />
[9]<input aria-label=Wikipedia durchsuchen />
[290]<a image_alt=Wikipedia />
```

Er antwortet im Werkbank-Format, das kleine Modelle beherrschen:
`{"gedanke":"…","werkzeug":"klicken","argumente":{"nummer":9}}`.
Werkzeuge: `oeffnen`, `klicken`, `tippen`, `scrollen`, `lesen`, `zeigen`, `fertig`.

Im Browser ist die Elementliste die **genauere** Erdung als Pixel: Die Seite sagt
selbst, was anklickbar ist, und eine Nummer trifft immer. Der Grounding-Server
(LocateAnything-3B, dieselbe Stelle wie bei Computer-Use) ist deshalb hier nur der
Notnagel — für das, was nicht im DOM steht: Bilder, Zeichenflächen, eingebettete
Ansichten. Dafür gibt es das Werkzeug `zeigen`: Es schickt ein Bildschirmfoto an
den Grounder, bekommt Koordinaten zurück und übersetzt sie über
`get_dom_element_at_coordinates` wieder in ein echtes Element. Ist kein Grounder
eingerichtet, sagt das Werkzeug das — und verweist auf die Elementliste.

### browser-use

Die Agentenschleife des Pakets: Bildschirmfoto je Schritt, Antwort in einem
verschachtelten JSON-Schema (`AgentOutput`).

### Was davon hier läuft

Gemessen am 16.09.2026, MacBook Pro M4 Pro, 24 GB, Modell `gemma4:12b`,
Aufgabe „Öffne die Wikipedia-Seite zur Wissensdestillation und erkläre sie in
einem Satz“:

| Steuerung | Ergebnis |
|---|---|
| browser-use | **Fehlschlag** — „Ollama returned invalid JSON for structured output“, kommt nicht über Schritt 1 |
| Dive on Wide | **2 Schritte**, richtige Antwort |

Und die schwerere Aufgabe (über das Suchfeld nach „Low-Rank Adaptation“ suchen,
Treffer anklicken, Antwort formulieren) mit der Dive-on-Wide-Steuerung: **7 Schritte**,
richtige Antwort — inklusive Tippen ins Suchfeld und Klicken in der Trefferliste.

Weitere Modelle mit der browser-use-Schleife:

| Modell | Bilder | Ergebnis |
|---|---|---|
| `qwen2.5-coder:14b` | nein | Ollama antwortet auf jeden Schritt mit HTTP 400, der Agent tappt blind herum |
| `qwen3.8:27b-mlx` | ja | **Metal-Speicherfehler** — 24 GB reichen für 27 B plus Bildschirmfotos nicht |

Kurz: Nicht die Modelle waren zu schwach, sondern Format und Nutzlast zu schwer.
Mit der Elementliste statt Bildschirmfotos und einem flachen JSON löst ein
12-B-Modell dieselben Aufgaben.

### Was den Speicher wirklich gesprengt hat

Drei Läufe starben am Metal-Speicherfehler, bevor die Ursache im Ollama-Log stand:

```
23:06:32  starting mlx runner subprocess  model=qwen3.8:27b-mlx
23:08:00  runtime OOM detected
```

**Mitten im Browserlauf lud Dive on Wide sein eigenes 27-B-Standardmodell** — 16 GB, und
der Browser-Agent verhungerte. Daraus drei Regeln, die jetzt im Code stehen:

* Vor jedem Browser-Auftrag werden andere Modelle entladen (wie vor dem Training).
* **Ein Browser-Auftrag zur Zeit** — der zweite wird begründet abgewiesen.
* Im Verlauf steht nur die **aktuelle** Seitenliste. Hängt man jede an, wächst der
  Kontext mit jedem Schritt; ab Schritt drei war Schluss.

Dazu zwei Kleinigkeiten, die kleine Modelle brauchen: Die Planerantwort ist auf
300 Token gedeckelt (sonst verheddert sich gemma4 in Wiederholungen, „token repeat
limit reached“), und wer drei Schritte auf derselben Seite steht, bekommt das gesagt.

### Wie verlässlich ist das?

Ehrlich: **nicht verlässlich genug für unbeaufsichtigte Arbeit.** Von drei Läufen der
Suchaufgabe lösten zwei sie (5 und 7 Schritte), einer blieb auf der Hauptseite hängen
und lief ins Schrittlimit. Ein 12-B-Modell verliert manchmal den Faden — es stürzt
dann nicht ab, es kommt nur nicht an. Für „schau mal nach und sag mir was“ reicht
es; für Abläufe, auf die man sich verlässt, nicht.

## Browser-Agent oder Websuche?

| | Websuche (`websuche`/`webseite`) | Browser-Agent |
|---|---|---|
| Kosten je Fund | ein HTTP-Aufruf, Bruchteile einer Sekunde | Seite rendern + **ein Modellaufruf je Schritt** |
| Kommt an | Text öffentlicher Seiten | auch JavaScript-Seiten, Zustimmungsbanner, angemeldete Bereiche |
| Nachvollziehbar | Titel, Adresse, Textausschnitt — protokolliert | Bericht plus besuchte Adressen |
| Speicher | winzig | Bildschirmfotos und Seitenbäume im Kontext |
| Sucht | über Suchmaschinen-Schnittstellen | müsste selbst ins Suchfeld tippen — genau der Verkehr, gegen den sich Suchmaschinen wehren |

Die Websuche bleibt der Standard: billige Breite. Der Browser-Agent ist die teure
Tiefe für das, was anders nicht erreichbar ist. Er ersetzt die Websuche nicht.
