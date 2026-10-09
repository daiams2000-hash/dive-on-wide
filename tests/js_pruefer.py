# -*- coding: utf-8 -*-
"""Prüft, ob im JavaScript der Oberfläche jede Zeichenkette geschlossen ist.

Kein JavaScript-Parser — nur die eine Fehlerklasse, die eine ganze Datei
unbrauchbar macht: eine Zeichenkette, die zu früh endet. Genau das ist am
22.09.2026 passiert (ein gerades Anführungszeichen mitten in einem deutschen
Satz), und danach tat in der Oberfläche kein einziger Klick mehr etwas —
während alle 433 Tests grün blieben, denn sie prüfen die Schnittstelle, nicht
das Skript.

Der Automat muss dreierlei auseinanderhalten, sonst meldet er Unsinn:

* **Reguläre Ausdrücke** dürfen Anführungszeichen enthalten: `.replace(/"/g, …)`.
* **Template-Literale** dürfen über Zeilen gehen — und sich verschachteln:
  `` `a ${b ? `c` : "d"} e` ``. Ohne Zustandsstapel gerät man dabei aus dem Takt.
* **Kommentare** dürfen alles enthalten, auch einzelne Apostrophe.

Klammern werden bewusst nicht gezählt: Bei verschachtelten Template-Literalen
erzeugt jede einfache Bilanz Fehlalarme, und Fehlalarme nimmt niemand ernst.
"""

FLAGS = "gimsuyd"


def _regex_moeglich(text, i):
    """Steht hier ein regulärer Ausdruck oder ein Geteiltdurch?

    Faustregel wie in jedem JS-Tokenizer: Nach einem Wert (Name, Zahl,
    schließende Klammer) ist `/` eine Division, sonst beginnt ein regulärer
    Ausdruck."""
    j = i - 1
    while j >= 0 and text[j] in " \t\n\r":
        j -= 1
    if j < 0:
        return True
    return text[j] not in ")]}" and not (text[j].isalnum() or text[j] in "_$")


def js_pruefen(text):
    """Leerer String = in Ordnung, sonst die Fundstelle im Klartext."""
    i, n, zeile = 0, len(text), 1
    # Stapel offener Template-Literale; je Eintrag die Klammertiefe des
    # ${…}-Ausdrucks, in dem wir gerade stecken (None = direkt im Template).
    stapel = []

    while i < n:
        c = text[i]

        if stapel and stapel[-1] is None:            # direkt in einem Template
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                zeile += 1
            elif c == "`":
                stapel.pop()
            elif c == "$" and i + 1 < n and text[i + 1] == "{":
                stapel[-1] = 0                        # ab jetzt Code im Template
                i += 2
                continue
            i += 1
            continue

        if c == "\n":
            zeile += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            ende = text.find("*/", i + 2)
            if ende < 0:
                return "Blockkommentar ab Zeile %d nie geschlossen" % zeile
            zeile += text.count("\n", i, ende)
            i = ende + 2
            continue
        if c == "/" and _regex_moeglich(text, i):
            j, in_klasse = i + 1, False
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_klasse = True
                elif text[j] == "]":
                    in_klasse = False
                elif text[j] == "\n":
                    break                             # regulärer Ausdruck endet an der Zeile
                elif text[j] == "/" and not in_klasse:
                    break
                j += 1
            if j < n and text[j] == "/":
                i = j + 1
                while i < n and text[i] in FLAGS:
                    i += 1
                continue
        if c == "`":
            stapel.append(None)
            i += 1
            continue
        if c in "\"'":
            start, quote = zeile, c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "\n":
                    return ("Zeichenkette in Zeile %d endet nicht vor dem Zeilenumbruch "
                            "— oft ein gerades Anführungszeichen mitten im Text" % start)
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            else:
                return "Zeichenkette ab Zeile %d nie geschlossen" % start
            continue
        if stapel and stapel[-1] is not None:        # Code innerhalb ${…}
            if c == "{":
                stapel[-1] += 1
            elif c == "}":
                if stapel[-1] == 0:
                    stapel[-1] = None                 # zurück ins Template
                else:
                    stapel[-1] -= 1
        i += 1

    if stapel:
        return "Template-Literal nie geschlossen (Backtick offen)"
    return ""
