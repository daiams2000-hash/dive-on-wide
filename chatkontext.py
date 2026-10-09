# -*- coding: utf-8 -*-
"""Was der Chat von selbst mitgibt: die Einführung und passendes Wissen.

Früher kam Wissen nur in den Chat, wenn man es mit „/“ von Hand anhängte. Wer Dateien ablegte und dann fragte,
bekam eine Antwort ohne sie (07.10.2026, erster Windows-Test). Jetzt durchsucht der Chat vor jeder Antwort das
Wissen mit derselben BM25-Suche wie der Lotse und legt die besten Abschnitte bei. Die Antwort nennt, was genutzt
wurde.

Fragt jemand nach Dive on Wide selbst (oder schreibt der Starter-Assistent die erste Antwort), kommt die
Einführung dazu: docs/EINFUEHRUNG.md bzw. docs/INTRO.md, plus die laufende Version. So bleibt die Antwort aktuell,
wenn sich Version oder Funktionen ändern — ein Test prüft, dass die Einführung jeden Menüpunkt nennt.
"""
import os
import re

import lotse

STARTER = "Starter-Assistent"
WISSEN_HOECHSTENS = 3            # Abschnitte je Antwort
WISSEN_ZEICHEN = 6000            # zusammen höchstens, damit kleine Kontexte nicht platzen
MINDEST_ABDECKUNG = 0.3          # so viel der Frage muss der beste Abschnitt treffen

UEBER_DIVE = re.compile(
    r"dive\s*on\s*wide|dow\.?os|dieses?\s+(programm|app|werkzeug|tool)|diese\s+(software|oberfläche)|"
    r"was\s+kannst\s+du|wer\s+bist\s+du|wie\s+funktioniert\s+(das\s+hier|hier)|"
    r"this\s+(app|program|tool|software)|what\s+can\s+you\s+do|who\s+are\s+you", re.I)


def ueber_dive(text):
    return bool(UEBER_DIVE.search(text or ""))


def einfuehrung(app_dir, englisch, version):
    pfad = os.path.join(app_dir, "docs", "INTRO.md" if englisch else "EINFUEHRUNG.md")
    try:
        text = open(pfad, encoding="utf-8").read()
    except OSError:
        return ""
    if englisch:
        kopf = ("Below is the official introduction to Dive on Wide, version %s. Answer questions about Dive on Wide "
                "from it — do not invent features, prices or versions that are not in it. When explaining Dive on Wide as "
                "a whole, start with one sentence that names the version (%s).\n\n" % (version, version))
    else:
        kopf = ("Hier die offizielle Einführung in Dive on Wide, Version %s. Beantworte Fragen zu Dive on Wide daraus — "
                "erfinde keine Funktionen, Preise oder Versionen, die hier nicht stehen. Erklärst du Dive on Wide als Ganzes, "
                "beginne mit einem Satz, der die Version nennt (%s).\n\n" % (version, version))
    return kopf + text


def wissen_abschnitte(eintraege):
    """Wissenseinträge (name, content) in Abschnitte für die Suche zerlegen — wie die Doku beim Lotsen."""
    aus = []
    for e in eintraege:
        name, inhalt = e.get("name") or "?", (e.get("content") or "").strip()
        while inhalt:
            stueck, inhalt = inhalt[:2500], inhalt[2500:]
            if len(stueck.strip()) > 20:
                aus.append({"quelle": name, "titel": name, "text": stueck})
    return aus


class WissensIndex:
    """Der Suchindex über das Wissen — einmal bauen, für viele Fragen nutzen.

    Stresstest 08.10.2026: Bei jeder Chat-Nachricht wurde der Index über das ganze Wissen neu gebaut — bei 3 000
    Einträgen 0,84 s vor jeder Antwort, mit 40 gleichzeitigen Chats 36 s Wartezeit im Median, und der Speicher
    wuchs auf 660 MB. Der Server hält jetzt einen Index je Wissensstand."""

    def __init__(self, eintraege):
        self.abschnitte = wissen_abschnitte(eintraege)
        self.suche = lotse.Suche(self.abschnitte) if self.abschnitte else None


def wissen_finden(frage, eintraege=(), index=None):
    """[(name, text)] — nur, was die Frage wirklich trifft."""
    index = index or WissensIndex(eintraege)
    if not index.suche or not lotse.woerter(frage):
        return []
    suche = index.suche
    treffer = suche.finden(frage, k=WISSEN_HOECHSTENS)
    if not treffer or not _passt(frage, suche, treffer[0]):
        return []
    aus, summe = [], 0
    for t in treffer:
        if summe + len(t["text"]) > WISSEN_ZEICHEN:
            break
        aus.append((t["quelle"], t["text"]))
        summe += len(t["text"])
    return aus


STIL = {
    "de": "Antworte kurz und inhaltlich vollständig: das Wichtige zuerst, keine Einleitung, keine Wiederholung der "
          "Frage, keine Floskeln am Ende. Ausführlich nur, wenn danach gefragt wird oder die Sache es wirklich braucht.",
    "en": "Answer briefly and completely: the essentials first, no preamble, no restating the question, no closing "
          "pleasantries. Go into detail only when asked or when the matter truly needs it.",
}


def _passt(frage, suche, bester):
    """Trifft der beste Abschnitt die Frage wirklich?

    Gezählt werden nur Wörter der Frage, die im Wissen überhaupt vorkommen. Wörter, die nur den Auftrag beschreiben
    („Schreibe eine kurze Bedienungsanleitung …“), sagen nichts darüber, ob das Wissen passt — früher drückten sie
    die Abdeckung unter die Schwelle, und der Orchestrator schrieb ohne das abgelegte Wissen (07.10.2026)."""
    alle = set(lotse.woerter(frage))
    bekannt = {w for w in alle if w in suche.idf}
    # Ein einzelnes zufällig gemeinsames Wort ist kein Treffer, wenn die Frage länger ist (07.10.2026: ein
    # Programmierziel bekam die Survival-Device-Firmware, weil beide „float“ enthielten).
    if not bekannt or (len(bekannt) < 2 and len(alle) > 3):
        return False
    i = next(k for k, a in enumerate(suche.abschnitte) if a["text"] == bester["text"] and a["quelle"] == bester["quelle"])
    im_besten = suche.tf[i][0]
    getroffen = {w for w in bekannt if w in im_besten}
    gewicht = lambda ws: sum(suche.idf[w] for w in ws)
    return len(getroffen) >= min(2, len(bekannt)) and gewicht(getroffen) >= MINDEST_ABDECKUNG * gewicht(bekannt)


def anreichern(messages, app_dir, version, eintraege=(), agent_name="", sprache="de", wissen=True, stil="kurz",
               index=None):
    """(neue Nachrichten, genutzte Wissensnamen, Einführung beigelegt?)

    Die Zusätze werden an den (einzigen) Systemprompt angehängt — der Verlauf bleibt unberührt."""
    fragen = [m.get("content") or "" for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
    if not fragen:
        return messages, [], False
    frage = fragen[-1]
    englisch = sprache == "en" or lotse.ist_englisch(frage)
    zusaetze = []
    intro = ueber_dive(frage) or (agent_name == STARTER and len(fragen) == 1)
    if intro:
        text = einfuehrung(app_dir, englisch, version)
        if text:
            zusaetze.append(text)
        else:
            intro = False
    genutzt = []
    if wissen:
        for name, text in wissen_finden(frage, eintraege, index=index):
            genutzt.append(name)
            zusaetze.append(("From the user's knowledge base — entry \"%s\":\n%s" if englisch else
                             "Aus dem Wissen des Nutzers — Eintrag „%s“:\n%s") % (name, text))
        if genutzt:
            zusaetze.append("Use this knowledge where it fits and name the entry you used." if englisch else
                            "Nutze dieses Wissen, wo es passt, und nenne den Eintrag, den du genutzt hast.")
    if stil == "kurz":
        # 07.10.2026: Antworten sollen kurz sein und trotzdem alles enthalten — als Standard, abschaltbar.
        zusaetze.append(STIL["en" if englisch else "de"])
    if sprache == "en":
        # Die eingebauten Agenten sagen „auf Deutsch“ — die gewählte Oberflächensprache gewinnt.
        zusaetze.append("Answer in English.")
    if not zusaetze:
        return messages, [], False
    # EIN Systemprompt, ganz vorn: Die Chat-Vorlagen von Qwen brechen mit „System message must be at the
    # beginning“ ab, sobald eine zweite Systemnachricht auftaucht (07.10.2026, HTTP 500 von Ollama).
    zusatz = "\n\n---\n\n".join(zusaetze)
    if messages and messages[0].get("role") == "system":
        erste = dict(messages[0], content=(messages[0].get("content") or "") + "\n\n---\n\n" + zusatz)
        neu = [erste] + list(messages[1:])
    else:
        neu = [{"role": "system", "content": zusatz}] + list(messages)
    return neu, list(dict.fromkeys(genutzt)), intro


def ein_systemprompt(messages):
    """Alle Systemnachrichten zu einer einzigen ganz vorn zusammenfassen.

    Qwen-Vorlagen (und manche andere) lehnen jede weitere Systemnachricht ab. Der Web-Modus schob seinen
    Recherche-Kontext als zweite vor die letzte Frage — mit Qwen ein HTTP 500 statt einer Antwort."""
    systeme = [m.get("content") or "" for m in messages if m.get("role") == "system"]
    if len(systeme) <= 1 and (not systeme or messages[0].get("role") == "system"):
        return messages
    rest = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": "\n\n---\n\n".join(x for x in systeme if x)}] + rest


# ---------------------------------------------------------------------------
# Kontext verdichten, wenn das Modell voll wird
# ---------------------------------------------------------------------------
# Früher endete eine Antwort an der Kontextgrenze einfach früher, ohne Hinweis. Jetzt: Füllstand messen; ab
# VERDICHTEN_AB werden die ältesten Nachrichten zu einer Zusammenfassung, die neuesten bleiben wörtlich. Der Browser
# bekommt die Zusammenfassung zurück und schickt sie ab dann statt der alten Nachrichten mit — der Server merkt
# sich nichts. Angeheftetes steckt im Systemprompt und wird nie verdichtet.
VERDICHTEN_AB = 0.75
BEHALTEN_ANTEIL = 0.4            # so viel des Kontexts bleibt für die neuesten Nachrichten wörtlich
ZEICHEN_JE_TOKEN = 3.5
MAX_ABSCHNITTE = 6               # höchstens so viele Zusammenfassungs-Runden je Verdichtung
SYSTEM_HOECHSTENS = 0.5          # so viel des Kontexts darf der Systemprompt höchstens belegen


def token_schaetzen(messages):
    return int(sum(len(m.get("content") or "") if isinstance(m.get("content"), str) else 400
                   for m in messages) / ZEICHEN_JE_TOKEN) + 4 * len(messages)


def verdichten(messages, num_ctx, zusammenfassen):
    """(neue Nachrichten, info) — info: {"prozent", "verdichtet": Anzahl ersetzter Nachrichten, "zusammenfassung"}.

    `zusammenfassen(text) -> str` fragt das Modell. Verdichtet wird nur der Verlauf zwischen Systemprompt und
    letzter Frage; die letzte Frage bleibt immer wörtlich."""
    num_ctx = max(2048, int(num_ctx or 16384))
    info = {"prozent": 0, "verdichtet": 0, "zusammenfassung": ""}
    # Sicherheitsnetz: Der Systemprompt (mit Angeheftetem, Wissen, Einführung) wird nie verdichtet. Ist er allein
    # schon zu groß, wird sein Ende gekürzt — sonst scheitert das Modell an zu viel Kontext (Stresstest 08.10.2026:
    # zehn angeheftete Stücke = 80 000 Zeichen bei 16 384 Token).
    if messages and messages[0].get("role") == "system":
        grenze = int(num_ctx * SYSTEM_HOECHSTENS * ZEICHEN_JE_TOKEN)
        sys_text = messages[0].get("content") or ""
        if isinstance(sys_text, str) and len(sys_text) > grenze:
            messages = [dict(messages[0], content=sys_text[:grenze] + "\n\n[… gekürzt: zu viel Kontext angeheftet]")] \
                + list(messages[1:])
            info["system_gekuerzt"] = True
    belegt = token_schaetzen(messages)
    info["prozent"] = min(999, round(100 * belegt / num_ctx))
    if belegt < num_ctx * VERDICHTEN_AB:
        return messages, info
    kopf = [m for m in messages[:1] if m.get("role") == "system"]
    verlauf = messages[len(kopf):-1]
    letzte = messages[-1:]
    behalten, platz = [], int(num_ctx * BEHALTEN_ANTEIL)
    for m in reversed(verlauf):
        t = token_schaetzen([m])
        if t > platz:
            break
        behalten.insert(0, m)
        platz -= t
    alt = verlauf[:len(verlauf) - len(behalten)]
    if not alt:
        return messages, info
    text = "\n\n".join("%s: %s" % ("Nutzer" if m.get("role") == "user" else "Assistent", m.get("content") or "")
                       for m in alt)
    # Nicht mehr hineinstopfen, als das Modell selbst lesen kann — aber auch nichts wegwerfen: Der alte Verlauf geht
    # in Abschnitten durch, jede Runde bekommt die bisherige Zusammenfassung mit (07.10.2026: nur das Ende zu lesen
    # verlor Projektname und Schlüsselzahl vom Anfang). Bei sehr langem Verlauf: Anfang (Ziele!) und Ende.
    groesse = int(num_ctx * 0.45 * ZEICHEN_JE_TOKEN)
    stuecke = [text[i:i + groesse] for i in range(0, len(text), groesse)] or [""]
    if len(stuecke) > MAX_ABSCHNITTE:
        stuecke = stuecke[:2] + stuecke[-(MAX_ABSCHNITTE - 2):]
    zusammenfassung = ""
    for stueck in stuecke:
        eingabe = ("Bisherige Zusammenfassung:\n%s\n\nWeiterer Verlauf:\n%s" % (zusammenfassung, stueck)
                   if zusammenfassung else stueck)
        zusammenfassung = (zusammenfassen(eingabe) or "").strip() or zusammenfassung
    if not zusammenfassung:
        return messages, info
    neu = kopf + [{"role": "user", "content": "[Zusammenfassung des bisherigen Gesprächs]\n" + zusammenfassung},
                  {"role": "assistant", "content": "Verstanden, ich arbeite mit dieser Zusammenfassung weiter."}] \
        + behalten + letzte
    info.update(verdichtet=len(alt), zusammenfassung=zusammenfassung,
                prozent=min(999, round(100 * token_schaetzen(neu) / num_ctx)))
    return neu, info


ZUSAMMENFASSEN_PROMPT = (
    "Fasse das folgende Gespräch für dich selbst zusammen, damit du es ohne den Wortlaut fortsetzen kannst. Behalte: "
    "Ziel und Wünsche des Nutzers, Entscheidungen, Zahlen, Namen, Dateinamen, Code-Stellen und offene Punkte. "
    "Stichpunkte, höchstens 250 Wörter, in der Sprache des Gesprächs.")
