# Dive on Wide Mesh — Architektur und Plan

> Abgleich der beiden Brainstorms mit dem, was bereits gebaut ist, plus der
> daraus erweiterte Plan. Stand: 2026-08-07, Version 0.1015.
>
> Diese Datei ist die Entscheidungsgrundlage. Wo etwas offen ist, steht das
> hier als offene Frage — nicht als stillschweigende Annahme.

---

## 0. Was das Ganze ist

Ein **dezentrales Agenten-Betriebssystem**: reines Peer-to-Peer, kein zentraler
Server, alles nur im flüchtigen Speicher. Es trägt drei Dinge übereinander:

1. **Kommunikation** — Messenger und Foren, anonym, flüchtig, ohne Registrierung.
2. **Gebündelter Speicher** — Geräte geben freiwillig RAM ab, damit große
   Sprachmodelle laufen, die auf keinem einzelnen Gerät Platz hätten.
3. **Agenten** — Dive on Wide selbst läuft auf diesem Unterbau.

Arbeitsname aus dem Brainstorm: **All AI**.

---

## 1. Was aus den Brainstorms neu dazukommt

Diese Punkte standen noch nicht im Plan und sind gut. Sie werden übernommen:

### 1.1 Geräteschutz — die Seite, die ich bisher gar nicht hatte
Verschlüsselung im Netz nützt nichts, wenn das Gerät mitliest. Aus Brainstorm 1:

- **Bildschirmaufnahme sperren** — Android `FLAG_SECURE`, iOS Weichzeichner bei
  `isCaptured` und bei Fokusverlust.
- **App-Umschalter** — beim Verlassen sofort ein neutrales Bild überblenden,
  damit keine Chat-Vorschau im Betriebssystem-Zwischenspeicher landet.
- **Tastatur-Isolation** — `textNoSuggestions`, `flagNoPersonalizedLearning`.
  Standardtastaturen lernen Wörter und synchronisieren sie in die Cloud. Das
  ist ein echtes Leck, das die beste Krypto umgeht.
- **Kein Klartext auf der Platte** — hier gelöst dadurch, dass es *gar keine*
  Platte gibt. Damit entfällt SQLCipher komplett. (Der Brainstorm empfahl
  SQLCipher; das gehört zu einer Architektur mit Persistenz, nicht zu unserer.)

**Bewertung:** Der wichtigste Zugewinn aus den Brainstorms. Betrifft aber die
mobilen Hüllen, nicht den Python-Knoten — siehe offene Frage 3.

### 1.2 Kontaktanker über einen physisch ausgetauschten Code
Zwei Menschen tauschen im echten Leben einen Code. Daraus leiten beide Geräte
immer wieder dieselbe Treffpunkt-Adresse ab, ohne dass ein Dritter erkennen
kann, wer sich dort trifft.

**Das kollidiert scheinbar mit dem, was gebaut ist** — und löst sich sauber
auf, siehe Abschnitt 2.1. Wird gebaut.

### 1.3 Vertrauensanker gegen den Mann in der Mitte
QR-Code-Abgleich beziehungsweise Sicherheitsnummern beim ersten Schlüssel-
tausch. Ohne das ist jede Ende-zu-Ende-Verschlüsselung wertlos, weil man nicht
weiß, mit wem man verschlüsselt. Fehlte im bisherigen Plan. Wird gebaut.

### 1.4 Double Ratchet statt eines festen Sitzungsschlüssels
Bisher gebaut: ein X25519-Handschlag, daraus ein Sitzungsschlüssel. Das ist
korrekt, hat aber **keine nachträgliche Geheimhaltung**: Wer den Schlüssel
später bekommt, liest alles Vergangene. Double Ratchet dreht den Schlüssel bei
jeder Nachricht weiter.

**Bewertung:** Berechtigt und wichtig. Wird gebaut — aber erst, wenn die
Transportschicht steht, weil das Ratchet ohne Nachrichtenreihenfolge nicht
prüfbar ist.

### 1.5 Foren mit Lebenszyklus
Thread-Eröffner schließt den Thread → ein signierter Löschbefehl läuft durchs
Netz, alle Zwischenspeicher werden überschrieben. Dazu abonnierbare
**Filter-Agenten** statt zentraler Moderation.

**Ehrliche Einschränkung, die im Brainstorm fehlte:** Ein Löschbefehl ist eine
*Bitte*, keine Garantie. Ein Knoten, der den Inhalt behalten will, behält ihn.
Was die Architektur wirklich garantiert, ist die **TTL** — nach Ablauf hat kein
ehrlicher Knoten den Inhalt mehr, und ein unehrlicher hätte ihn ohnehin kopiert.
So muss es auch kommuniziert werden, sonst verspricht das Produkt etwas, das
es nicht halten kann.

### 1.6 Byzantinische Fehlererkennung
Bei verteilter Inferenz kann ein Knoten falsche Zwischenergebnisse liefern —
aus Defekt oder Absicht. Gegenmittel: dieselbe Teilberechnung stichprobenartig
doppelt rechnen lassen und vergleichen. Kostet Rechenzeit, ist aber
unverzichtbar, sobald Fremde Knoten stellen. War im Plan nicht enthalten.

### 1.7 Offline über Funk
Wi-Fi Direct und Bluetooth-Mesh für Geräte in Reichweite. Passt hervorragend
zur Entscheidung „nur LAN" und macht sie sogar stärker: Das Netz funktioniert
dann auch ohne Internetanschluss.

### 1.8 Verteilung ohne App-Store
Als Web-App statt als Store-Installation. Umgeht Freigabeprozesse und die
Frage, wer die App aus dem Laden nehmen darf.

---

## 2. Widersprüche, die entschieden werden müssen

### 2.1 Fester Kontaktcode ⇄ unverknüpfbare Identitäten — GELÖST

Der Brainstorm will einen Code, der **immer derselbe** ist. Gebaut ist
das Gegenteil: unabhängiger Zufall je Thema, **nichts** ist verknüpfbar.

Beides ist gleichzeitig erfüllbar, weil es zwei verschiedene Dinge sind:

| Ebene | Was | Lebensdauer |
|---|---|---|
| **Kontaktanker** | Ein Geheimnis zwischen **genau zwei** Menschen, persönlich getauscht | dauerhaft |
| **Treffpunkt** | `KDF(Anker, Tag)` → wechselt täglich | 1 Tag |
| **Identität** | Ed25519 je Sitzung/Thema, unabhängiger Zufall | 1 Sitzung |

Der Anker ist **paarweise**, kein Hauptschlüssel. Er verknüpft genau die zwei,
die ihn ohnehin kennen — und niemanden sonst. Ein Beobachter sieht nur täglich
wechselnde Zufallsadressen. Fällt ein Anker in fremde Hände, ist genau *ein*
Kontakt betroffen, nicht die ganze Identität.

**Das ist die Synthese, die beide Anforderungen hält.** Wird so gebaut.

### 2.2 Nur-LAN gegen global skaliert — ENTSCHIEDEN: beides, getrennt

Deine Entscheidung war: nur LAN und manuell getauschte Tickets, kein fremder
Bootstrap-Knoten. Die Brainstorms wollen ein weltweites Netz mit tausenden
Geräten und angeschlossenen Rechenzentren.

**Das geht nicht beides.** Ein reines LAN-Netz erreicht nie die Knotenzahl, die
ein 1,2-Billionen-Modell braucht. Vorschlag: **zwei Betriebsarten im selben
Code**, die der Nutzer bewusst wählt.

| | **Klause** (Standard) | **Weite** |
|---|---|---|
| Wer | LAN + persönlich getauschte Tickets | jeder mit einem Einladungsticket |
| Findet sich über | mDNS, Bluetooth, Wi-Fi Direct | Peers, die man schon kennt |
| Modelle | was auf den eigenen Geräten Platz hat | große Modelle im Verbund |
| Anonymität | sehr hoch (kleiner, bekannter Kreis) | Onion-Routing nötig |
| **Schwäche** | zu klein für große Modelle | fremde Knoten, Vertrauen nötig |

Beide teilen denselben Unterbau. „Weite" ist kein anderes Produkt, sondern
derselbe Knoten mit mehr erlaubten Nachbarn.

> **Entschieden am 2026-08-07:** Beide Betriebsarten werden gebaut und sind
> **voneinander unabhängig**. Eine Klause funktioniert vollständig ohne die
> Weite und weiß nichts von ihr; die Weite entsteht nach und nach daneben.
> Ein Knoten läuft in genau EINER Betriebsart, und der Wechsel ist eine
> bewusste Handlung — kein Schalter im Hintergrund. So kann niemand
> versehentlich aus einem privaten Freundesnetz in ein offenes Netz rutschen.

### 2.3 Onion-Routing ⇄ verteilte Inferenz — TEILWEISE UNVEREINBAR

Das ist der härteste Punkt und stand in keinem Brainstorm.

Onion-Routing kostet Latenz **je Hop**. Verteilte Inferenz braucht **je Token**
einen Durchlauf durch **alle** Pipeline-Stufen. Beides multipliziert sich.

Gerechnet (60 Stufen, 8-Bit-Aktivierungen, siehe Abschnitt 3):

| Weg | je Token | Tokens/s | 500-Token-Antwort |
|---|---|---|---|
| direktes P2P im LAN | 1,8 s | 0,56 | 15 Min |
| P2P übers Internet | 3,6 s | 0,28 | 30 Min |
| **+ Onion-Routing** | **10,8 s** | **0,09** | **90 Min** |

**Vorschlag: Anonymität nach Ebene trennen.**
Der *Inhalt* ist immer Ende-zu-Ende verschlüsselt. Aber Onion-Routing nur für
**Nachrichten und Foren** — dort kosten 200 ms Mehrlatenz nichts. Die
**Aktivierungstensoren der Inferenz** laufen direkt zwischen den Knoten. Ein
Beobachter sieht dann, *dass* Knoten A und B an derselben Berechnung arbeiten —
aber nicht, **wessen** Anfrage das ist, weil der Auftraggeber anonym in die
Pipeline einspeist und der Ausgabestrom verschlüsselt zu ihm zurückläuft.

Damit bleibt der Schutz dort voll, wo er zählt, und die Rechenverteilung
bleibt benutzbar.

### 2.4 Ein Agent, der nicht abschaltbar ist — MUSS ENTSCHIEDEN WERDEN

Im Brainstorm wurde bestätigt: Ein Agent in einem Netz ohne zentrale Instanz
lässt sich nicht mehr stoppen, weil niemand alle Knoten kontrolliert.

**Das ist kein Vorteil, sondern eine Konstruktionsschwäche.** Ein System, das
sich nicht anhalten lässt, kann auch einen eigenen Fehler nicht anhalten. Und
niemand wird ein Gerät beisteuern, dessen fremde Last er nicht abwerfen kann.

**Vorschlag als festes Prinzip — nicht verhandelbar:**

> Jeder Knoten behält jederzeit das letzte Wort über seine eigene Hardware.
> Ein Halt-Befehl an einem Gerät wirkt sofort und braucht niemandes Zustimmung.
> Es gibt keinen Zustand, den ein Agent über einen Neustart hinweg im Netz
> überdauern lassen kann — die Zero-Persistence-Regel gilt auch für ihn.

Das kostet nichts an Funktion und macht das Ganze überhaupt erst verteilbar:
Freiwillige geben nur Hardware her, die sie zurücknehmen können.

### 2.5 Python für den Rechenpfad — ENTSCHIEDEN, aber mit Folgen
Der Brainstorm sagt richtig: Python ist für Tensor-Operationen zu langsam.
Gemessen ist es auch für den Transport zu langsam — **1,1 MB/s**.

Auflösung, konsistent mit deiner Entscheidung „PyNaCl optional":
Der Knoten in Python macht **Steuerung, Nachrichten, DHT, Routing**. Die
eigentliche Modellrechnung macht die Laufzeit, die ohnehin nativ ist —
**Ollama oder vLLM auf demselben Gerät**. Der Knoten schickt Aufträge dorthin
und holt Ergebnisse ab. Python rechnet nie selbst an einem Tensor.

---

## 3. Zahlen aus den Brainstorms, die nicht halten

Alle nachgerechnet, Rechenweg im Anhang.

### 3.1 Der Speicherbedarf war um das Dreifache zu niedrig
Vergessen wurde die **Ausfallsicherheit**. Bei 60 Pipeline-Stufen und 90 %
Verfügbarkeit je Knoten:

| Replikate je Stufe | Pipeline verfügbar | Speicherbedarf |
|---|---|---|
| 1 (Brainstorm-Annahme) | **0,18 %** | 640 GB |
| 2 | 54,7 % | 1.280 GB |
| **3** | **94,2 %** | **1.920 GB** |

Ohne Redundanz steht die Pipeline **praktisch immer** still — eine von 60
Stufen fällt fast sicher aus. Realistischer Bedarf ist also nicht 600 GB,
sondern **rund 1.900 GB**. Das verdreifacht die nötige Knotenzahl.

### 3.2 Die Geschwindigkeit war 10- bis 30-fach zu optimistisch
Brainstorm: 1–3 Token/s. Nachgerechnet: **0,09–0,56 Token/s** (Tabelle in 2.3).
Bandbreite ist nicht das Problem — eine Aktivierung ist nur 7 KB. **Latenz
mal Stufenzahl** ist das Problem.

Wichtig und im Brainstorm nicht getrennt: **Durchsatz skaliert, Wartezeit
nicht.** Mit vielen gleichzeitigen Anfragen bleibt die Pipeline gefüllt und das
Netz leistet insgesamt viel. Die *einzelne* Antwort bleibt trotzdem langsam.
Du sagtest, fünf Minuten wären in Ordnung — das trägt für kurze Antworten,
nicht für eine lange Programmiersitzung.

### 3.3 Das Verhältnis der Beiträge war nicht herleitbar
Brainstorm: PC 2,5 Mio. Credits/Monat, iPhone 10.600 — Verhältnis **236 : 1**.
Nachgerechnet in GB-Stunden (abgebbarer Speicher × Zeit × Verfügbarkeit):

| Gerät | GB-Stunden/Monat |
|---|---|
| iPhone 15 (2 GB abgebbar) | 1.008 |
| Laptop 16 GB (6 GB) | 1.512 |
| PC 64 GB + 20 GB VRAM (48 GB) | 20.736 |
| Server 512 GB (384 GB) | 270.950 |

Tatsächliches Verhältnis PC : iPhone = **21 : 1**, nicht 236 : 1.

**Grundsatz statt erfundener Zahlen:** Guthaben wird in **GB-Stunden**
gemessen — der Einheit, die auch verbraucht wird. Verdient wird in derselben
Einheit. Der Umrechnungskurs schwankt mit Angebot und Nachfrage. Das ist
herleitbar, prüfbar und balanciert sich selbst.

### 3.4 Rechtlicher Punkt, der in keinem Brainstorm vorkam
Sobald Guthaben **übertragbar** ist, wird daraus mit hoher Wahrscheinlichkeit
ein Finanzinstrument oder E-Geld — mit allem, was an Aufsicht daranhängt.

**Vorschlag: Guthaben ist nicht übertragbar.** Es ist ein Zählerstand für
„du hast beigetragen, du darfst beziehen", an das Gerät gebunden, nicht
handelbar, kein Marktwert. Das ist genau das reine Tauschmodell, das du
wolltest — und hält das Projekt aus der Finanzaufsicht heraus. Ein
handelbarer Token wäre eine Unternehmensentscheidung mit Anwaltsbedarf, keine
technische.

---

## 4. „Energetisch kohärent, harmonisch abgestimmt" — technisch übersetzt

Der Satz taucht in beiden Brainstorms auf. Er hat eine konkrete Bedeutung:
**Der Knoten darf das Gerät seines Besitzers niemals beeinträchtigen.**

Daraus wird ein eigenes Bauteil, der **Ressourcen-Statthalter**:

1. **Geräteprofil** — RAM, Kerne, Akku oder Netzteil, Wärmezustand, Netzart
   (WLAN/Mobilfunk/Kabel).
2. **Abgebbarer Anteil** — nie ein fester Prozentsatz. Was frei ist, hängt
   davon ab, was der Besitzer *gerade* tut.
3. **Laufende Rücknahme** — braucht der Besitzer Speicher, gibt der Knoten
   ihn **sofort** zurück und meldet sich von seiner Pipeline-Stufe ab. Die
   Ersatzknoten übernehmen. Das Gerät des Besitzers hat immer Vorrang.
4. **Harte Sperren** — kein Beitrag bei Akku unter Schwelle, bei Mobilfunk-
   verbindung, bei Wärmedrosselung, oder wenn der Besitzer es abschaltet.
5. **Sichtbarkeit** — eine Anzeige, die jederzeit zeigt, was gerade abgegeben
   wird. Kein Beitrag ohne ausdrückliche, jederzeit widerrufbare Zustimmung.

Das ist zugleich die Antwort auf deinen Punkt „keine Scam-App, die Leuten
Rechenleistung zieht": Der Unterschied zwischen Beitrag und Diebstahl ist
Sichtbarkeit und Widerrufbarkeit, nicht die Absicht.

---

## 5. Der erweiterte Bauplan

Fertig ist Schicht 0 und 1. Was neu dazukommt, ist **fett**.

| # | Schicht | Zustand |
|---|---|---|
| 0 | Krypto (Ed25519, X25519, XChaCha20-Poly1305, gegen RFC-Vektoren geprüft) | ✅ fertig |
| 0 | Flüchtiger Speicher, unverknüpfbare Identitäten | ✅ fertig |
| 1 | Arbeitsnachweis, Inhaltsadressierung, TTL, RAM-Speicher | ✅ fertig |
| 2 | Ressourcen-Statthalter + Geräteprofil (Abschnitt 4) | ✅ fertig |
| 2 | Kontaktanker + Treffpunkt-Ableitung (Abschnitt 2.1) | ✅ fertig |
| 2 | Vertrauensanker gegen Mann-in-der-Mitte (QR/Sicherheitsnummer) | ✅ fertig |
| 3 | Kademlia-DHT + Nachliefern | ✅ fertig, gemessen bis 20.000 Knoten |
| 4 | Transport: UDP, LAN-Rundruf, Tickets, NAT-Durchstich | ✅ fertig |
| 4 | Wi-Fi Direct / Bluetooth | offen — aus reinem Python nicht erreichbar |
| 5 | Double Ratchet (nachträgliche Geheimhaltung) | ✅ fertig |
| 6 | Onion-Routing — nur für Nachrichten/Foren, nicht für Tensoren | ✅ fertig |
| 7 | Foren: Themen-Feeds, Lebenszyklus | ✅ fertig · Filter-Agenten offen |
| 8 | RPC-Rechenverteilung; llama.cpp als Rechenwerk, Python nur Steuerung | ✅ fertig, nachgemessen |
| 8 | Prüfung fremder Rechenknoten (Relevanz, Wiederholung, Akte) | ✅ fertig, an echten Modellen gemessen |
| 9 | Guthaben in GB-Stunden, nicht übertragbar | ✅ fertig |
| 10 | Einbindung in die Dive-on-Wide-Oberfläche | ✅ fertig |
| — | **Geräteschutz der mobilen Hülle** (Abschnitt 1.1) | **neu, eigenes Projekt** |

**Reihenfolge-Begründung:** Schicht 2 zuerst, weil sie offline vollständig
prüfbar ist und weil ohne den Ressourcen-Statthalter niemand guten Gewissens
ein Gerät beisteuern kann. Alles ab Schicht 4 hängt an der Entscheidung
aus 2.2.

---

## 6. Was ehrlich gesagt werden muss

- **Ein Handy kann kein Knoten sein.** Python-Stdlib läuft nicht auf iOS oder
  Android. Ein Handy kann die Oberfläche eines Knotens bedienen, aber keinen
  RAM einspeisen. Wenn „vom Handy RAM einspeisen" zum Ziel gehört, braucht es
  eine native Hülle — ein eigenes Projekt neben diesem, kein Nebenprodukt.
- **Onion-Routing im kleinen Kreis anonymisiert wenig.** Eine Kette aus drei
  Knoten in einem Netz von fünf schützt kaum. Der Schutz wächst mit dem Netz.
- **„Gelöscht" ist eine Bitte, keine Garantie.** Siehe 1.5.
- **Die Startkosten sind nicht null, nur weil keine Server laufen.** Es
  braucht Entwicklungszeit, und ein Netz unter etwa 100 verlässlichen Knoten
  trägt kein großes Modell. Der erste Nutzen muss also aus dem *kleinen* Netz
  kommen — Messenger und Foren tragen sich ab zwei Geräten.

---

## 7. Wofür diese Netze wirklich taugen

Der Einwand „mit 0,09 Token/s ist nichts zu holen" stimmt — aber die Zahl galt
für den schlimmsten Fall: 1,2 Billionen Parameter, 60 Stufen, Onion-Routing,
offenes Internet. **95 % dieser Zeit war reine Onion-Latenz, nicht Rechnen.**

### 7.1 Die Klause ist ein völlig anderes Regime

Nachgerechnet für 20 Nutzer und rund 200 GB (Rechenweg im Anhang):

| Aufbau | eine Antwort | Netz gesamt |
|---|---|---|
| 70B dicht, 8 Knoten im LAN | 1,4 Token/s | 11 Token/s |
| 70B dicht, 8 Knoten übers Internet | 1,1 Token/s | 11 Token/s |
| **235B MoE, 12 Knoten im LAN** | **4,3 Token/s** | **54 Token/s** |
| 70B, 4 Knoten mit Grafikkarte im LAN | 10,9 Token/s | 46 Token/s |

Drei Erkenntnisse, die die erste Rechnung verdeckt hatte:

1. **Nicht Rechenleistung ist der Engpass, sondern Speicherbandbreite.** Je
   Token muss jedes Gewicht einmal gelesen werden. Verteilt man das Modell,
   verteilt man auch die Bandbreite — die Summe wächst mit der Knotenzahl.
   Deshalb wird es mit mehr Knoten *schneller*, nicht langsamer.
2. **MoE-Modelle sind für verteilte Inferenz wie gemacht.** Bei 235B mit 22B
   aktiven Parametern liest jeder Knoten nur ein Zehntel seiner Gewichte je
   Token. Das ist der ganze Sprung von 1,4 auf 4,3 Token/s.
3. **Im LAN ist Latenz kein Thema.** 1 ms je Hop statt 180 ms. Der gesamte
   Netzanteil einer Antwort liegt bei 8–12 ms.

**In 200 GB passen:** Llama-70B (35 GB), Qwen3-235B MoE (118 GB), nicht aber
DeepSeek-V3 671B (336 GB). Also durchaus Modelle, die auf keinem einzelnen
Gerät der Gruppe laufen würden.

### 7.2 Der eigentliche Denkfehler: Pipeline statt Parallelität

Ein Modell über Knoten aufzuteilen ist der **schwierigste** Weg, ein P2P-Netz
zu nutzen — er zwingt alle Knoten, nacheinander zu arbeiten. Fast alles andere
ist **peinlich parallel**: keine Pipeline-Latenz, und die Leistung wächst
linear mit der Knotenzahl.

**A. Arbeit, die von Natur aus parallel ist**
- **Einbettungen und semantische Indizes.** Jedes Dokument wird unabhängig
  eingebettet. 200 Geräte = 200-facher Durchsatz, ohne jede Koordination. Ein
  durchsuchbarer Index über ein ganzes Archiv, ohne dass ein Dokument das Haus
  verlässt.
- **Massentranskription** (Whisper) — jede Audiodatei ein eigener Auftrag.
- **OCR ganzer Archive**, Klassifikation, Extraktion aus vielen Dokumenten.
- **Parameterstudien und Monte-Carlo-Läufe** — klassisches Grid Computing,
  nur ohne zentrale Verwaltung.

**B. Agenten-Schwärme statt eines großen Modells**
Statt ein 235B-Modell über 20 Geräte zu spannen: **20 vollständige 8B-Modelle
gleichzeitig**, je eines pro Gerät. Null Tensor-Verkehr zwischen Knoten, weil
jedes Modell lokal komplett ist. Das Netz koordiniert **Aufträge**, keine
Tensoren — und genau das ist Dive on Wide bereits (Orchestrator, Agenten,
Pipelines). Zwanzig Agenten, die parallel je einen Teil einer Recherche
bearbeiten, schlagen ein großes Modell, das seriell durch eine Pipeline tropft.

**Das ist die natürlichste Nutzung dieses Netzes und braucht keine neue
Technik — nur die Transportschicht darunter.**

**C. Spekulatives Dekodieren**
Ein kleines Modell auf dem eigenen Gerät schreibt einen Entwurf, das große
verteilte Modell **prüft mehrere Token auf einmal**. Prüfen ist parallel,
Erzeugen wäre seriell. Das verwandelt das Latenzproblem in ein
Durchsatzproblem und bringt typisch das Drei- bis Achtfache.

**D. Das Netz als Modell-Bibliothek**
Inhaltsadressierung ist bereits gebaut. Damit wird das Mesh zur Verteilstelle
für Modellgewichte — wie BitTorrent, aber signiert und inhaltsadressiert.
Kein Latenzproblem, löst ein echtes Ärgernis (Bandbreite, Verfügbarkeit,
Sperren) und ist fast geschenkt.

**E. Gemeinsames Feintuning ohne gemeinsame Daten**
Jeder trainiert lokal einen kleinen Adapter (LoRA) auf seinen eigenen Daten;
geteilt werden nur die Adapter, nie die Daten. Latenztolerant, datenschutz-
freundlich, und für Forschung ein echtes Argument.

**F. Lange Aufträge über Nacht**
Bei 1,4 Token/s sind acht Stunden rund **40.000 Token** — ein vollständiger
Entwurf, eine durchgearbeitete Codebasis, eine Literaturübersicht. Niemand
schaut dabei zu. Das ist kein Chat, das ist Stapelverarbeitung, und dafür ist
Latenz gleichgültig.

**G. Kommunikation ohne Internet**
Die Klause über Bluetooth und Wi-Fi Direct funktioniert ohne Anschluss — bei
Ausfällen, auf dem Land, auf Veranstaltungen, überall wo Netz fehlt oder
gesperrt ist. Hier ist das Mesh selbst das Produkt, nicht die KI darauf.

### 7.1b Kademlia: finden, ohne alle zu fragen

Bis 0.1023 wurde alles per Rundruf verbreitet. In einer **Klause** ist das
genau richtig — zwanzig Geräte, ein Paket, jeder hat es, und zwar sofort. In
der **Weite** ist es das Ende: Bei tausend Knoten kostet jede Veröffentlichung
tausend Pakete, und jeder müsste alles aufbewahren, was irgendwer je abgelegt
hat.

`mesh/kademlia.py` dreht das um. Der Abstand zweier Kennungen ist ihr XOR; ein
Inhalt liegt bei den **K = 20** Knoten, deren Kennung seiner Adresse am
nächsten ist; gesucht wird, indem man immer den fragt, der dem Ziel näher ist
als man selbst. Gemessen in einer simulierten Welt (`Suche` nimmt die
Fragefunktion von außen entgegen, deshalb geht das ohne Netz):

| Knoten | log₂(N) | Runden | Fragen | Anteil des Netzes | Trefferquote |
|---:|---:|---:|---:|---:|---:|
| 100 | 6,6 | 3,8 | 21 | 20,7 % | 59/60 |
| 1.000 | 10,0 | 4,7 | 24 | 2,4 % | 60/60 |
| 5.000 | 12,3 | 5,5 | 26 | 0,5 % | 60/60 |
| 20.000 | 14,3 | 6,1 | 28 | **0,14 %** | 60/60 |

**Zweihundertmal mehr Knoten kosten ein Drittel mehr Fragen.** Das ist der
ganze Punkt.

**Drei Dinge, die beim Bauen erst durch Messen sichtbar wurden:**

1. **Die Abbruchregel war zu lasch.** Der erste Entwurf hörte auf, sobald die
   besten drei gefragt waren — und fand nur 28 von 40 Zielen, weil im Feld noch
   ein näherer Knoten ungefragt stand. Kademlia verlangt: erst wenn **alle K
   Nächsten** gefragt sind, ist Schluss.
2. **Die Empfangsseite fehlte ganz.** Tabellen füllen sich in Kademlia
   hauptsächlich dadurch, dass man **gefragt** wird. Ohne diese eine Zeile
   lernt ein fleißiger Knoten die ganze Welt kennen, während die Welt ihn nie
   kennt — 35 von 40 statt 40 von 40.
3. **Die Auffrischungs-Kennung landete im falschen Eimer.** Das entscheidende
   Bit wurde auf 1 gesetzt statt gekippt; war es schon 1, waren beide gleich.
   240 von 240 Proben daneben, ohne dass irgendetwas einen Fehler meldete.

**Der Älteste hat Vorrang.** Ist ein Eimer voll, wird nicht der Neue
aufgenommen und der Älteste verdrängt, sondern umgekehrt: Der Älteste wird
angetippt und bleibt, wenn er antwortet. Das ist die wichtigste
Sicherheitseigenschaft von Kademlia — ein Netz, das den Neuesten bevorzugt, ist
mit einem Nachmittag Rechenzeit zu kapern. Dazu kommt: Die Kennung muss aus dem
Schlüssel folgen **und** einen Arbeitsnachweis tragen, sonst sucht sich ein
Angreifer zwanzig Kennungen dicht bei einer Zieladresse und kontrolliert alles,
was dort liegt.

### 7.1c Die Weite betritt man mit einem Ticket — auch von nebenan

Beim Bauen der DHT fiel etwas auf, das dort schon länger stand: **Zwei fremde
Weite-Knoten im selben LAN fanden sich allein über den Rundruf**, ohne dass je
ein Ticket getauscht wurde. README und diese Datei versprachen das Gegenteil.

Das Versprechen ist das richtige. Im Café oder im Uni-Netz sitzt man mit
Fremden im selben Netz, und deren Anwesenheit ist kein Einverständnis; der
Rundruf verriet außerdem jedem im LAN, dass hier ein Weite-Knoten läuft. Seit
0.1023 zählt in der Weite nur, was über den **eigenen Port** hereinkommt — den
kennt, wer ein Ticket eingelöst hat oder von einem bereits bekannten Knoten
weitergereicht wurde. In der Klause bleibt der Rundruf genau das, was er sein
soll.

### 7.1f Traut man einem fremden Rechenknoten?

Eine Sprachmodell-Antwort lässt sich **nicht** nachprüfen. Zwei ehrliche Knoten
mit demselben Modell geben verschiedenen Text; schon eine andere Quantisierung
reicht. Mehrheitsentscheid funktioniert bei freiem Text nicht.

Prüfbar ist etwas Kleineres und trotzdem Wichtiges: **ob überhaupt gerechnet
wurde.** Der billige Betrug im Netz ist nicht das raffiniert falsche Ergebnis,
sondern gar keins — Müll schicken, Guthaben kassieren, Strom sparen.

**Ein Irrweg, der fast ausgeliefert worden wäre.** Der erste Entwurf verschickte
Aufgaben mit Formatvorgabe: „Beginne mit dem Kennwort K1A2B3 und nenne das
Ergebnis von 1399 + 379." Gemessen:

| | Formatprobe bestanden |
|---|---|
| gemma4:12b (ehrlich) | 5 von 5 |
| **qwen2.5:0.5b (ehrlich)** | **1 von 5** |

Diese Prüfung maß **Anweisungstreue**, nicht Ehrlichkeit. Sie hätte schwache,
aber völlig ehrliche Geräte als Betrüger gebrandmarkt — genau die Geräte, aus
denen ein Freundesnetz besteht. Sie ist jetzt ein *Können-Test*, der
ausdrücklich nicht ins Urteil eingeht.

**Was stattdessen geprüft wird**, beides an echten Modellen gemessen:

1. **Relevanz** — wie viel Inhalt der Frage kommt in der Antwort vor?
2. **Wiederholung** — antwortet ein Knoten auf *verschiedene* Fragen mit
   demselben Text, rechnet er nicht. Der schärfste Nachweis, den es hier gibt.
3. **Leere** — keine Antwort ist keine Arbeit.

**Die Zählweise war wichtiger als die Schwelle**, und auch das zeigte erst die
Messung. Ganze Wörter zu vergleichen ließ eine tadellose Antwort mit 17 %
durchfallen: „Peer-to-Peer-**Netzen**" in der Frage gegen
„Peer-to-Peer-**Netze**" in der Antwort, und Befehlswörter wie „nenne" oder
„zwei" stehen in keiner Antwort. Über 12 echte Antworten gegen 12
Betrugsmuster:

| Zählweise | ehrlich | Betrug | Abstand |
|---|---|---|---|
| ganze Wörter | 0,40–1,00 | 0,00–0,25 | 0,15 |
| **Wortstamm 4, ab 5 Zeichen** | **0,50–1,00** | **0,00** | **0,50** |

Der Stamm fängt die deutsche Beugung, die Mindestlänge wirft Befehlswörter
hinaus. Die Schwelle liegt bei 0,30 — mitten in der Lücke.

**Im Zweifel für den Knoten.** Zwei Beanstandungen sind nötig, und ein
einzelner Aussetzer sperrt niemanden: Pakete gehen verloren, Modelle
verhaspeln sich. Ein fälschlich ausgesperrter ehrlicher Knoten ist teurer als
ein durchgerutschter Betrüger — der Erste geht und kommt nicht wieder.

**Was das nicht leistet:** Ein Knoten, der ein *schlechteres* Modell fährt als
angekündigt, besteht alles — er rechnet ja. Das ist nicht erkennbar, und
Dive on Wide behauptet es nicht.

**Und die Akten sind flüchtig.** Ein Vertrauensurteil, das einen Neustart
überlebt, wäre eine dauerhafte Bewertung von Menschen. Wer sich neu verbindet,
fängt neu an. Das kostet Genauigkeit und ist es wert.

### 7.1e Der Fehler, den man selbst nie bemerkt: Verstärkung

Beim Durchsehen des frisch geschriebenen DHT-Codes fiel eine Zahl auf: Eine
Kademlia-Antwort ist **539 Byte**, die zugehörige Frage **57** — Faktor 9,5.
UDP prüft den Absender nicht. Wer eine fremde Adresse als Absender einträgt,
lässt also beliebig viele Dive-on-Wide-Knoten gleichzeitig mit dem Neunfachen auf
sein Opfer eindreschen.

Das ist die unangenehmste Sorte Fehler: **Man bemerkt ihn im eigenen Betrieb
nie**, weil der Schaden woanders entsteht. Jeder Knoten verhält sich dabei
vollkommen korrekt — er beantwortet eine Frage.

Zwei Schranken, und die zweite ist die wichtigere:

* **Je Absender**, 20 Antworten in 10 Sekunden. Eine echte Suche fragt jeden
  Knoten ein- bis zweimal (gemessen: 28 Fragen für ein Netz mit 20.000 Knoten,
  auf viele Befragte verteilt) — sie merkt von der Bremse nichts.
* **Insgesamt**, 300 in 10 Sekunden. Ohne diese wechselt ein Angreifer einfach
  die gefälschte Absenderadresse und umgeht die erste vollständig.

Damit ist der Beitrag eines Knotens zu einem solchen Angriff auf **16 kB/s
gedeckelt**, unabhängig davon, wie viel hineingeschickt wird. Wer gebremst
wird, bekommt **keine** Meldung — eine Antwort „du wirst gebremst" wäre selbst
wieder ein Paket an das Opfer.

Die Adressliste räumt sich auf; sonst wüchse sie mit jeder je gesehenen
Absenderadresse, und gefälschte gibt es unbegrenzt.

**Beim selben Durchgang gefunden:** `nachliefern()` ging über *alle*
gespeicherten Sätze, und jeder kostet eine vollständige Suche plus bis zu 20
Sendungen. Bei vollem Speicher (10.000 Sätze) hätte ein Knoten alle zehn
Minuten selbst ein Paketgewitter ausgelöst — die Last erzeugt, gegen die
Kademlia antritt. Jetzt 50 Adressen je Runde, **reihum**, damit über mehrere
Runden trotzdem alles drankommt.

### 7.1d NAT-Durchstich: erreichbar werden, ohne Server

Die Weite nannte sich global und war es nicht. Ein Ticket trägt eine Adresse —
und hinter einem Heimrouter existiert die eigene Adresse von außen nicht. Zwei
Leute mit normalen Anschlüssen konnten sich schlicht nicht erreichen.

Zwei Schritte, beide ohne fremde Infrastruktur:

**1 · Spiegel.** Man weiß selbst nicht, wie man von außen aussieht — Adresse
und Port vergibt der Router. Also fragt man jemanden, den man ohnehin kennt:
„Unter welcher Adresse siehst du mich?" Genau das tun STUN-Server; hier kann es
jeder bereits bekannte Knoten. Kein zentraler Dienst, kein Anbieter, der
mitschreibt. Gefragt werden **mehrere** — und wenn zwei verschieden antworten,
ist das keine Panne, sondern die Diagnose (siehe unten).

**2 · Stups.** Um B zu erreichen, bittet A einen Knoten R, den beide kennen:
„Sag B, er soll bei meiner Außenadresse anklopfen." B schickt daraufhin ein
paar Pakete zu A. Die kommen bei A vielleicht nicht an — ihr Zweck ist, in **Bs
Router** den Rückweg zu öffnen. Gleichzeitig klopft A bei B. Eine der beiden
Richtungen trifft auf einen schon offenen Weg. R erfährt dabei nur, *dass* zwei
miteinander sprechen wollen, nicht worüber.

**Zwei Prüfungen, ohne die das ein Angriffswerkzeug wäre:**

* **Nur Bekannte dürfen stupsen.** Sonst schickt ein Fremder einen Stups, und
  hilfsbereite Knoten prasseln gemeinsam auf ein Ziel seiner Wahl ein — ein
  Verstärker, gebaut aus lauter freundlichen Teilnehmern.
* **Nur öffentliche Zieladressen.** Ein Stups auf `192.168.x.x` ließe das Mesh
  auf ein Gerät im lokalen Netz eines Opfers los, das mit alldem nichts zu tun
  hat. Private, lokale, Anbieter-NAT- und Sonderadressen werden abgewiesen.

**Was das nicht leistet.** Bei einem *symmetrischen* NAT vergibt der Router je
Ziel einen anderen Port; die gespiegelte Adresse ist dann für den Anruf eines
Dritten wertlos, und der Durchstich scheitert. Daran ist ohne einen
weiterleitenden Server nichts zu machen. Dive on Wide **erkennt** den Fall — zwei
Nachbarn melden verschiedene Ports — und sagt es samt Abhilfe
(Portweiterleitung, oder ein Knoten mit fester Adresse als Brücke), statt es
schweigend zu versuchen.

**Ehrlicher Erprobungsstand:** Die Mechanik ist über echte UDP-Sockets geprüft
— Spiegelung, Weiterreichen, beide Abwehrprüfungen. Alles davon lief auf einem
Rechner, also über `127.0.0.1`. **Durch zwei echte Heimrouter ist es nie
gegangen.** Wer es zuerst versucht, ist der Erste.

### 7.2b Was Dive on Wide für verteilte Inferenz tatsächlich baut

Trotz allem, was in 7.2 steht, ist ein Modell über mehrere Geräte zu spannen
eine berechtigte Anforderung — für das eine große Modell, das sonst gar nicht
läuft. Also ist es gebaut. Mit einer klaren Arbeitsteilung:

| Schritt | Wer macht es |
|---|---|
| Wer kann mitrechnen und mit wie viel Speicher? | Dive on Wide (`mesh/knoten.py`) |
| Wie fallen die Schichten auf die Geräte? | Dive on Wide (`mesh/verteilt.py`) |
| Die Tensor-Arbeit je Schicht | **llama.cpp**, nie Python |
| Zwischenergebnisse zwischen den Geräten | **llama.cpp RPC**, nie Python |
| Anzeige, Diagnose, Ein-/Ausschalten | Dive on Wide |

Die Zeile „Python rechnet nie selbst an einem Tensor" gilt unverändert. Der
Mechanismus zum Aufteilen existiert bereits — die **RPC-Rückseite von
llama.cpp**: auf jedem Gerät ein `rpc-server`, und ein Hauptprozess verteilt
die Schichten darauf. Dive on Wide erfindet ihn nicht neu, es plant und steuert ihn.

**Der Plan richtet sich nach Kapazität, nicht nach Kopfzahl.** Ein Gerät mit
32 GB trägt mehr als eines mit 8 GB. Gleichmäßig aufzuteilen hieße, sich am
schwächsten Gerät zu orientieren und den Rest zu verschenken. Der Führende
bekommt zusätzlich den Kontextspeicher aufgebürdet und deshalb weniger
Schichten.

**Das Angebot ist aus, bis man es einschaltet.** Ein Gerät, das Schichten
eines fremden Modells hält, ist für die ganze Sitzung gebunden — das muss man
wollen. Angekündigt wird es im Ruf als `rpc`-Port; 0 heißt „ich mache nicht
mit". Ein Knoten ohne dieses Feld (ältere Fassung) gilt als nicht teilnehmend.

**Erprobungsstand: es läuft — nachgemessen.** llama.cpp wurde mit
`-DGGML_RPC=ON` gebaut, zwei Rechenknoten gestartet, ein Modell darüber
aufgeteilt. Mit Dive on Wides eigenem Plan und Dive on Wides eigenen Aufrufen:

| | |
|---|---|
| Plan | 8 / 8 / 8 Schichten auf drei Stufen |
| Geschätzt | 90,9 Token/s |
| **Gemessen** | **88,8 Token/s** (40 Token in 0,45 s) |
| **Gegenbeweis** | Ein Rechenknoten abgeschaltet → **die Inferenz bricht ab** |

Die letzte Zeile ist die einzige, die wirklich etwas beweist: Ohne sie könnte
llama.cpp die Rechenknoten stillschweigend ignoriert und alles lokal gerechnet
haben. Genau das war der erste Verdacht — das Protokoll erwähnte RPC mit keinem
Wort, und 123 Token/s klangen nach rein lokaler Arbeit.

**Was die Zahlen nicht sagen:** Alle drei „Geräte" waren derselbe Rechner. Es
gab keine echte Netzlaufzeit und keinen getrennten Speicher; die Schätzung traf
deshalb so genau, weil ihr Netzanteil praktisch null war. Über wirklich
getrennte Geräte hinweg gelten die Zahlen aus 3.1, nicht diese.

**Zwei Stolperstellen, die niemand erraten kann:**

* Der Rechenknoten heißt **`ggml-rpc-server`**, nicht `rpc-server`. Wer nach
  dem alten Namen sucht, findet nichts — und schließt daraus, dass es nicht
  geht. Genau das ist hier zuerst passiert.
* Auf macOS muss der Rechenknoten **auf Metal festgenagelt** werden (`-d MTL0`).
  Sonst wählt er den BLAS-Rücken, der `RMS_NORM` nicht kennt und mitten im
  ersten Durchlauf abbricht. Der Hauptprozess meldet dann nur „Remote RPC
  server crashed or returned malformed response" — eine Meldung, die einem
  nicht sagt, dass ein Gerät auszuwählen ist. `verteilt.rechenknoten_aufruf()`
  setzt es von allein.

Was Homebrew ausliefert, ist weiterhin ohne RPC. `werkzeuge/llamacpp_rpc_bauen.sh`
baut es und prüft danach nach, dass `--rpc` wirklich da ist.

### 7.3 Universitäten: der richtige Köder ist nicht Geschwindigkeit

Der Einwand ist berechtigt — mit Geschwindigkeit gewinnt man dort niemanden,
gegen einen Cloud-Anbieter verliert man sie immer. **Der Hebel ist ein
anderer:**

1. **Die Daten verlassen das Haus nicht.** Interviews, Patientenakten,
   Prozessakten, unveröffentlichte Messreihen — dafür gibt es Ethikvoten und
   Datenschutzauflagen, die eine Cloud schlicht verbieten. Heute heißt das:
   entweder gar keine Sprachmodelle, oder eine Regelverletzung. Ein Netz, das
   das Institut nie verlässt, löst ein Problem, für das es bisher **keine**
   bequeme Lösung gibt. Das ist mit Abstand das stärkste Argument.
2. **Die Rechnerpools stehen 16 Stunden am Tag still.** Sie sind bezahlt, sie
   laufen, sie werden gekühlt. Nachts sind sie leer. Kein Beschaffungsvorgang,
   kein Budget, keine Ausschreibung — die drei Dinge, die an einer Hochschule
   Monate kosten.
3. **Nachvollziehbarkeit.** Alles inhaltsadressiert: Wer eine Auswertung
   wiederholt, bekommt beweisbar dieselben Gewichte und dieselben Eingaben.
   In der Forschung ist das ein Wert an sich.

**Ehrlich zum Weg hinein:** Eine Hochschul-IT wird fremde P2P-Software im
Campusnetz nicht begrüßen. Der realistische Einstieg ist **ein Lehrstuhl oder
eine Fachschaft**, nicht die Institution — eine Gruppe mit sensiblen Daten und
ohne Budget. Genau dort ist die Klause fertig einsetzbar, ohne dass die Weite
je gebraucht würde.

### 7.4 Was daraus für die Reihenfolge folgt

Der erste Nutzen kommt **nicht** aus verteilter Inferenz. Er kommt aus:
Messenger und Foren (tragen sich ab zwei Geräten) → parallele Stapelarbeit
(Einbettungen, Transkription) → Agenten-Schwärme → und erst dann verteilte
Modelle. Jede Stufe ist für sich nützlich, keine wartet auf die nächste.

---

## Anhang — Rechenweg

- Gewichte: 1,2·10¹² Parameter × 4 Bit / 8 = 600 GB; + ~40 GB KV-Cache.
- Ausfallsicherheit: P(Pipeline) = (1−(1−v)^r)^s mit v=0,9, s=60 Stufen.
- Latenz: Aktivierung = 7168 Werte × 1 Byte (8-Bit) = 7,0 KB je Hop;
  Zeit je Token = Stufen × Hop-Latenz.
- Beitrag: abgebbare GB × 720 h × Verfügbarkeitsfaktor.
- Klause-Rechnung: Stufenzeit = (Modellgroesse / Knoten x aktiver Anteil)
  / Speicherbandbreite; Antwortzeit = Stufen x Stufenzeit + Stufen x Hop.
  Annahmen: 50 GB/s je Alltagsgeraet, 400 GB/s je Grafikkarte, 1 ms Hop im
  LAN, 25 ms uebers Internet.
