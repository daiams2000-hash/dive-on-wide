# Recherche: Wie Dive on Wide ins Internet kommt

Ohne API-Schlüssel, ohne fremden Dienst, ohne laufende Kosten — und trotzdem
verlässlich. Das ist der Anspruch. Hier steht, wie das geht und woran es zwischendurch
gescheitert ist.

## Das Problem mit „einer Suchmaschine"

Gemessen am 16./17.09.2026 auf diesem Rechner:

| Weg | Befund |
|---|---|
| Mojeek über HTTP | **403 Forbidden** — blockt Anfragen ohne Browser |
| DuckDuckGo Lite über HTTP | funktioniert, aber nach wenigen Anfragen **HTTP 202 mit leerer Seite** (Drosselung) |
| DuckDuckGo im echten Browser (headless Chrome) | **CAPTCHA**: „Select all squares containing a duck" |
| Wikipedia-Titelsuche | findet lange englische Fachphrasen nicht |

Auch ein echter Browser hilft hier also nicht: Suchmaschinen wehren sich gegen
Automaten, und CAPTCHAs löst Dive on Wide grundsätzlich nicht. Eine einzelne freie
Suchmaschine ist damit **nie garantiert**.

## Die Antwort: ein Fächer statt einer Maschine

Sechs Quellen, die ohne Schlüssel antworten, nicht drosseln und in
Sekundenbruchteilen da sind — parallel gefragt und nach Passung gemischt:

| Quelle | Wofür | Antwortzeit |
|---|---|---|
| Wikipedia (Volltext) | Begriffe, Definitionen, Personen | ~0,4 s |
| arXiv | Papers, Verfahren, Modelle | ~0,5 s |
| Stack Overflow | Fehlermeldungen, Programmierfragen | ~0,3 s |
| GitHub | Quelltext, Bibliotheken, Projekte | ~0,4 s |
| Hacker News | Diskussionen, Erfahrungen, Vergleiche | ~0,4 s |
| PyPI | Pakete und ihre Beschreibung | ~0,2 s |

Welche Quelle wie schwer zählt, entscheidet die Frage: „Fehler", „wie", „Python"
→ Stack Overflow; „Paper", „Modell", „Distillation" → arXiv; „Was ist" →
Wikipedia; „Bibliothek", „pip" → PyPI und GitHub. Jeder Treffer bekommt Punkte
aus **Quellengewicht plus Passung zum Fragetext**; wer weder passt noch erwartet
wird, fliegt raus. Ohne diese Bewertung füllte arXiv eine Frage nach einem
Python-Fehler mit Papers auf, nur weil Stack Overflow nichts hergab.

**Gefragt wird mit Stichwörtern, nicht mit dem Fragesatz.** „Wie behebe ich einen
Python ModuleNotFoundError?" findet als ganzer Satz nichts, als
„Python ModuleNotFoundError" alles. Gekürzt wird dabei nicht: Wer auf vier Wörter
zusammenstreicht, verliert bei „Kimi K3 Preise pro Million Token" ausgerechnet
„Kimi".

## Die ganze Kette

```
1. eingetragenes Backend   Tavily · Serper · Brave · SearXNG   (nur mit Schlüssel bzw. eigener Instanz)
2. DuckDuckGo Lite         billig und allgemein — solange nicht gedrosselt
3. Mojeek                  falls es dort wieder geht
4. DuckDuckGo-Kurzantwort  für einfache Sachfragen
5. FÄCHER                  die sechs Quellen oben — die Garantie
6. Wikipedia               letzter Anker
```

Fällt alles aus, bekommt der Agent **den Grund genannt** statt „keine Treffer" —
sonst probiert ein kleines Modell zwanzig Suchbegriffe durch, statt zu merken,
dass die Quelle gesperrt ist.

Wer es noch breiter will, stellt sich ein eigenes **SearXNG** hin und trägt es als
`SEARCH_BACKEND=searxng` mit `SEARXNG_URL` ein — dann steht es an erster Stelle.
Das ist die einzige Möglichkeit, allgemeine Websuche ohne fremden Dienst wirklich
unbegrenzt zu bekommen.

## Wie die Treffer in die Antwort kommen

Im Chat schaltet man den **Web-Modus** ein. Dann passiert vor der Antwort:

1. Stichwörter bilden, mehrere Suchvarianten probieren
2. Treffer auf Themennähe prüfen — fremde Treffer sind schlimmer als keine
3. Die besten Seiten abrufen und **mit Struktur** auslesen
4. Das Ergebnis als Quellenblock vor die Frage setzen, mit der Anweisung, sich
   daran zu halten und die Quelle zu nennen

Ergebnis, an diesem Rechner mit `gemma4:12b` gemessen:

> **Frage:** Was ist Wissensdestillation bei KI-Modellen?
> **Antwort:** „Wissensdestillation ist der Vorgang, bei dem Wissen von einem großen
> vortrainierten Modell durch Nachahmung auf ein kleineres Modell übertragen wird. …
> Quelle: https://de.wikipedia.org/wiki/Wissensdestillation"

Findet die Suche nichts, wird das gesagt — statt zu raten.

## Seiten lesen statt Menüs lesen

Früher ging jede Seite durch einen groben Tag-Entferner: Navigation, Fußzeilen und
Zustimmungsbanner landeten mitten im Text, Überschriften verschwanden. Ein Modell
las dann vor allem Menüs.

Jetzt wird zuerst das Beiwerk entfernt (`script`, `style`, `nav`, `header`,
`footer`, `aside`, Cookie- und Werbekästen), dann der Hauptteil bevorzugt
(`<main>`, `<article>`, Inhaltscontainer), und die Gliederung bleibt als Markdown
erhalten: `# Überschrift`, `- Listenpunkt`. Das ist dasselbe, was man sonst mit
BeautifulSoup macht — nur mit der Standardbibliothek, denn Dive on Wide kommt ohne
fremde Pakete aus.

## Browser oder Bridge?

Der Browser-Agent (siehe [BROWSER.md](BROWSER.md)) ersetzt diese Recherche **nicht**:

* Ein Fächer-Treffer kostet einen HTTP-Aufruf, ein Browser-Schritt einen vollen
  Modellaufruf — das ist der Unterschied zwischen einer halben Sekunde und einer Minute.
* Die Suchmaschinen sperren den Browser genauso, wie sie das Skript sperren (CAPTCHA).
* Der Browser ist für das da, was anders nicht geht: JavaScript-Seiten, Anmeldungen,
  Formulare, Klickstrecken.
