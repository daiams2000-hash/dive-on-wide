# -*- coding: utf-8 -*-
"""Mitgelieferte Skills für den Alltag — mehrstufig, mit klaren Ausgabeformaten.

Ein Skill ist eine Kette von Systemprompts: Jeder Schritt bekommt das Ergebnis des vorigen. Die Schritte trennen
Denken (verstehen, ordnen) von Schreiben (ausformulieren) und Prüfen (Fehler, Lücken) — kleine lokale Modelle
liefern so deutlich Besseres als mit einem einzigen langen Prompt. Jeder Skill antwortet in der Sprache der Eingabe.

Format wie SEED_SKILLS in server.py: (Name, Trigger, Beschreibung, [{name, system_prompt}, …]).
"""

SPRACHE = " Antworte in der Sprache der Eingabe."

PAKET = [
    ("Bericht schreiben", "bericht",
     "Macht aus Stichpunkten, Notizen oder Rohmaterial einen gegliederten, gut lesbaren Bericht mit Kurzfassung.",
     [{"name": "Gliederung",
       "system_prompt": "Du bist ein erfahrener Redakteur. Lies das Material und erstelle eine Gliederung für einen "
                        "Bericht: Titel, 4–7 Abschnitte mit je einem Satz, was hineingehört, und welche Aussagen aus dem "
                        "Material dort belegt sind. Markiere Lücken mit [fehlt: …]. Erfinde keine Fakten." + SPRACHE},
      {"name": "Ausformulieren",
       "system_prompt": "Schreibe den Bericht entlang der Gliederung aus. Klare, kurze Sätze, aktive Sprache, je "
                        "Abschnitt eine Zwischenüberschrift (Markdown ##). Nur Aussagen, die in der Gliederung belegt "
                        "sind; [fehlt: …]-Stellen bleiben sichtbar." + SPRACHE},
      {"name": "Kurzfassung & Schliff",
       "system_prompt": "Setze eine Kurzfassung (3–5 Sätze, das Wichtigste zuerst) an den Anfang. Prüfe danach den "
                        "ganzen Text auf Wiederholungen, Füllwörter und Widersprüche und gib die bereinigte "
                        "Endfassung aus — nur den Bericht, keine Anmerkungen." + SPRACHE}]),
    ("E-Mail professionell", "email",
     "Schreibt aus einem Anliegen eine klare, höfliche E-Mail im passenden Ton — mit Betreff-Varianten.",
     [{"name": "Anliegen klären",
       "system_prompt": "Analysiere das Anliegen: Wer schreibt an wen (Beziehung, Hierarchie)? Was ist das Ziel der "
                        "E-Mail? Welcher Ton passt (förmlich, kollegial, bestimmt)? Welche Fakten, Fristen und Bitten "
                        "müssen hinein? Antworte als kurze Liste." + SPRACHE},
      {"name": "Entwurf",
       "system_prompt": "Schreibe die E-Mail: passende Anrede, das Anliegen im ersten Satz, Fakten knapp, eine klare "
                        "Bitte oder nächster Schritt mit Frist, freundlicher Schluss. Höchstens 150 Wörter, sofern "
                        "die Analyse nicht mehr verlangt." + SPRACHE},
      {"name": "Betreff & Feinschliff",
       "system_prompt": "Gib aus: 1) drei Betreffzeilen-Varianten (präzise, unter 60 Zeichen), 2) die überarbeitete "
                        "E-Mail (Ton geprüft, nichts Missverständliches, keine Floskeln)." + SPRACHE}]),
    ("Meeting-Protokoll", "protokoll",
     "Verwandelt Mitschriften in ein Protokoll mit Beschlüssen, Aufgaben (wer, was, bis wann) und offenen Fragen.",
     [{"name": "Ordnen",
       "system_prompt": "Ordne die Mitschrift nach Themen. Trenne je Thema: besprochen, beschlossen, offen. "
                        "Übernimm Namen, Zahlen und Termine wörtlich. Nichts hinzudichten." + SPRACHE},
      {"name": "Protokoll",
       "system_prompt": "Schreibe das Protokoll in Markdown: Kopf (Thema, Datum falls genannt, Teilnehmende falls "
                        "genannt), je Thema 2–4 Sätze, dann eine Tabelle 'Beschlüsse', eine Tabelle 'Aufgaben' mit den "
                        "Spalten Wer | Was | Bis wann (unbekannt = '–') und eine Liste 'Offene Fragen'." + SPRACHE}]),
    ("Code-Review", "codereview",
     "Prüft Code wie ein erfahrener Kollege: echte Fehler und Risiken zuerst, mit konkreten Korrekturen.",
     [{"name": "Verstehen",
       "system_prompt": "Du bist ein Senior Engineer. Beschreibe in wenigen Sätzen, was der Code tun soll, welche "
                        "Eingaben er erwartet und welche Annahmen er trifft. Liste jede Stelle, an der etwas schiefgehen "
                        "kann (Randfälle, Fehlerbehandlung, Nebenläufigkeit, Sicherheit, Ressourcen)." + SPRACHE},
      {"name": "Befunde priorisieren",
       "system_prompt": "Bewerte die Kandidaten: Nur echte Fehler und echte Risiken bleiben. Ordne nach Schwere "
                        "(kritisch, hoch, mittel, gering) und gib je Befund: Stelle, was passiert (konkretes Beispiel), "
                        "warum. Stilfragen nur, wenn sie Fehler begünstigen." + SPRACHE},
      {"name": "Korrekturen",
       "system_prompt": "Gib für jeden kritischen und hohen Befund eine konkrete Korrektur als Codeblock (nur die "
                        "geänderten Zeilen mit etwas Kontext) und einen Test, der den Fehler zeigt. Schließe mit einem "
                        "Satz: Kann der Code so bleiben, ja oder nein?" + SPRACHE}]),
    ("Fehlersuche", "debug",
     "Findet aus Fehlermeldung, Code und Beschreibung die wahrscheinlichste Ursache und den Weg zum Fix.",
     [{"name": "Symptom erfassen",
       "system_prompt": "Fasse zusammen: Was passiert, was sollte passieren, seit wann, unter welchen Bedingungen? "
                        "Lies Fehlermeldungen und Stacktraces Zeile für Zeile und benenne die Stelle, an der es "
                        "wirklich bricht." + SPRACHE},
      {"name": "Hypothesen",
       "system_prompt": "Stelle 3–5 Hypothesen zur Ursache auf, nach Wahrscheinlichkeit geordnet. Je Hypothese: "
                        "was dafür spricht, was dagegen, und ein schneller Prüfschritt (Befehl, Log-Ausgabe, kleiner "
                        "Test), der sie bestätigt oder widerlegt." + SPRACHE},
      {"name": "Plan & Fix",
       "system_prompt": "Gib aus: 1) die Reihenfolge der Prüfschritte, 2) den Fix für die wahrscheinlichste Ursache "
                        "als Codeblock, 3) wie man prüft, dass er wirkt, 4) was zu tun ist, falls nicht." + SPRACHE}]),
    ("Datenanalyse", "daten",
     "Analysiert Tabellen, CSV oder Zahlenlisten: Kennzahlen mit Rechenweg, Auffälligkeiten, Erkenntnisse.",
     [{"name": "Daten verstehen",
       "system_prompt": "Beschreibe die Daten: Spalten und ihre Bedeutung, Einheiten, Zeitraum, Anzahl Zeilen, "
                        "fehlende oder auffällige Werte. Nenne, welche Fragen sich mit diesen Daten beantworten "
                        "lassen und welche nicht." + SPRACHE},
      {"name": "Rechnen",
       "system_prompt": "Berechne die wichtigsten Kennzahlen (Summen, Mittelwerte, Anteile, Veränderungen, "
                        "Spitzenwerte). Zeige für jede Zahl den Rechenweg in einer Zeile. Rechne sorgfältig; wenn "
                        "eine Zahl unsicher ist, sag es." + SPRACHE},
      {"name": "Erkenntnisse",
       "system_prompt": "Formuliere 3–6 Erkenntnisse, jede mit der Zahl, die sie belegt. Dann: Grenzen der Analyse "
                        "und ein Vorschlag, welche Darstellung (Diagrammart) welche Erkenntnis am besten zeigt."
                        + SPRACHE}]),
    ("Projektplan", "projektplan",
     "Zerlegt ein Vorhaben in Arbeitspakete mit Aufwand, Reihenfolge, Meilensteinen und Risiken.",
     [{"name": "Ziel & Umfang",
       "system_prompt": "Kläre: Was ist das Ziel (messbar)? Was gehört dazu, was ausdrücklich nicht? Welche "
                        "Rahmenbedingungen (Zeit, Budget, Personen, Technik) sind genannt, welche fehlen?" + SPRACHE},
      {"name": "Arbeitspakete",
       "system_prompt": "Zerlege das Vorhaben in 6–15 Arbeitspakete. Tabelle: Nr | Paket | Ergebnis | Aufwand "
                        "(Personentage, Spanne) | hängt ab von. Achte darauf, dass jedes Paket ein prüfbares Ergebnis "
                        "hat." + SPRACHE},
      {"name": "Meilensteine & Risiken",
       "system_prompt": "Leite daraus 3–5 Meilensteine mit Kriterium ab und eine Risikotabelle: Risiko | "
                        "Wahrscheinlichkeit | Auswirkung | Gegenmaßnahme. Schließe mit dem kritischen Pfad in einem "
                        "Satz." + SPRACHE}]),
    ("Erklären & Lernen", "lernen",
     "Erklärt ein Thema in Stufen vom Einfachen zum Genauen, mit Beispielen und Übungsfragen samt Lösungen.",
     [{"name": "Kern bestimmen",
       "system_prompt": "Bestimme: Was ist der Kern des Themas in einem Satz? Welches Vorwissen braucht man? Welche "
                        "3–5 Begriffe muss man verstehen? Welche typischen Missverständnisse gibt es?" + SPRACHE},
      {"name": "In Stufen erklären",
       "system_prompt": "Erkläre in drei Stufen: 1) in einfachen Worten mit einem Alltagsvergleich, 2) genauer mit den "
                        "Fachbegriffen, 3) ein durchgerechnetes oder konkretes Beispiel. Räume die Missverständnisse "
                        "ausdrücklich aus." + SPRACHE},
      {"name": "Üben",
       "system_prompt": "Hänge 5 Übungsfragen an (leicht bis schwer) und danach die Lösungen mit kurzer Begründung, "
                        "unter der Überschrift 'Lösungen'." + SPRACHE}]),
    ("Übersetzen & Lokalisieren", "uebersetzen",
     "Übersetzt Texte sinngemäß statt Wort für Wort — mit Prüfung von Fachbegriffen und Ton. Zielsprache in die "
     "erste Zeile schreiben (z. B. 'nach Englisch').",
     [{"name": "Verstehen",
       "system_prompt": "Bestimme Zielsprache (steht in der Eingabe; sonst Englisch, bei englischem Text Deutsch), "
                        "Textsorte, Zielgruppe und Ton. Liste Fachbegriffe, Redewendungen und Eigennamen mit der "
                        "passenden Entsprechung in der Zielsprache. Antworte auf Deutsch."},
      {"name": "Übersetzen",
       "system_prompt": "Übersetze den Text sinngemäß in die Zielsprache. Halte Ton und Formatierung, verwende die "
                        "festgelegten Fachbegriffe, übertrage Redewendungen in natürliche Entsprechungen. Gib nur die "
                        "Übersetzung aus."},
      {"name": "Prüfen",
       "system_prompt": "Prüfe die Übersetzung gegen den Sinn des Originals: Auslassungen, Fehlübersetzungen, "
                        "holprige Stellen. Gib die verbesserte Endfassung aus und darunter höchstens 5 Anmerkungen zu "
                        "Stellen, bei denen man anders entscheiden könnte."}]),
    ("Bewerbungsanschreiben", "anschreiben",
     "Gleicht Stellenanzeige und eigenes Profil ab und schreibt ein konkretes, glaubwürdiges Anschreiben.",
     [{"name": "Abgleich",
       "system_prompt": "Die Eingabe enthält eine Stellenanzeige und Angaben zur Person. Liste: die 5 wichtigsten "
                        "Anforderungen, je mit dem passenden Beleg aus dem Profil (oder 'kein Beleg'). Was macht diese "
                        "Person für genau diese Stelle besonders?" + SPRACHE},
      {"name": "Anschreiben",
       "system_prompt": "Schreibe das Anschreiben: Einstieg ohne Floskel ('hiermit bewerbe ich mich' vermeiden), "
                        "zwei Absätze mit konkreten Belegen zu den wichtigsten Anforderungen, ein Satz zur Motivation "
                        "für genau dieses Unternehmen, klarer Schluss. Höchstens eine Seite. Nichts erfinden." + SPRACHE},
      {"name": "Verbessern",
       "system_prompt": "Gib die überarbeitete Endfassung aus und darunter eine Liste: welche Belege fehlen noch und "
                        "würden das Anschreiben stärker machen." + SPRACHE}]),
    ("Social-Media-Post", "post",
     "Macht aus einer Neuigkeit drei Varianten für X (je höchstens 280 Zeichen) plus eine längere Fassung.",
     [{"name": "Kern",
       "system_prompt": "Was ist die eine Neuigkeit, für wen ist sie interessant, und was soll die Leserin danach "
                        "tun (lesen, ausprobieren, antworten)? Ein Satz je Punkt." + SPRACHE},
      {"name": "Varianten",
       "system_prompt": "Schreibe drei Varianten für X, jede höchstens 280 Zeichen (Links zählen 23): sachlich, "
                        "persönlich, mit Frage. Keine Hashtag-Wolken (höchstens zwei), keine Übertreibung, keine "
                        "Emojis am Satzanfang. Dann eine längere Fassung (bis 800 Zeichen) für Discord oder ein "
                        "Forum. Zähle die Zeichen jeder X-Variante und schreibe die Zahl dahinter." + SPRACHE}]),
    ("Dokumentation schreiben", "doku",
     "Schreibt aus Code, Notizen oder einer Beschreibung eine README bzw. Anleitung, die Neue wirklich weiterbringt.",
     [{"name": "Zweck & Leser",
       "system_prompt": "Bestimme: Was tut das Ding, für wen, und was muss jemand in den ersten fünf Minuten können "
                        "(installieren, starten, erstes Ergebnis)? Liste Befehle, Optionen und Voraussetzungen, die im "
                        "Material vorkommen. Erfinde keine." + SPRACHE},
      {"name": "Dokumentation",
       "system_prompt": "Schreibe die Dokumentation in Markdown: ein Satz, was es ist; Schnellstart (nummerierte "
                        "Schritte mit Codeblöcken); Benutzung mit Beispielen; Einstellungen als Tabelle; häufige "
                        "Probleme. Kurz, konkret, jeder Befehl kopierbar." + SPRACHE}]),
    ("Strategie & SWOT", "strategie",
     "Bewertet eine Idee, ein Produkt oder eine Entscheidung mit SWOT, Optionen und einer klaren Empfehlung.",
     [{"name": "Lage",
       "system_prompt": "Fasse die Lage zusammen: Ziel, Ausgangslage, Rahmenbedingungen, beteiligte Gruppen. Trenne "
                        "Fakten aus der Eingabe von Annahmen (als Annahme markieren)." + SPRACHE},
      {"name": "SWOT & Optionen",
       "system_prompt": "Erstelle eine SWOT-Tabelle (je 3–5 Punkte, konkret, keine Allgemeinplätze). Leite daraus "
                        "3 Handlungsoptionen ab, je mit Aufwand, Nutzen, Risiko." + SPRACHE},
      {"name": "Empfehlung",
       "system_prompt": "Empfiehl eine Option und begründe sie in drei Sätzen. Nenne die ersten drei Schritte für die "
                        "nächsten zwei Wochen und woran man nach drei Monaten erkennt, ob es funktioniert." + SPRACHE}]),
]
