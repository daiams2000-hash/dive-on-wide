import re

TOKEN = re.compile(r"\s*(\d+\.?\d*|[+\-*/()])")


def tokens(text):
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"Unerwartetes Zeichen an Position {pos}")
        out.append(m.group(1))
        pos = m.end()
    return out


def rechne(text):
    t = tokens(text)
    pos = 0

    def zahl():
        nonlocal pos
        tok = t[pos]
        if tok == "(":
            pos += 1
            wert = ausdruck()
            pos += 1
            return wert
        pos += 1
        return float(tok)

    def ausdruck():
        nonlocal pos
        wert = zahl()
        while pos < len(t) and t[pos] in "+-*/":
            op = t[pos]
            pos += 1
            rechts = zahl()
            if op == "+":
                wert += rechts
            elif op == "-":
                wert -= rechts
            elif op == "*":
                wert *= rechts
            else:
                wert /= rechts
        return wert

    return ausdruck()
