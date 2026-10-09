import re

TOKEN = re.compile(r"\s*(\d+\.?\d*|\.\d+|[+\-*/^()])")


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
    if not t:
        raise ValueError("leerer Ausdruck")
    pos = 0

    def sieh():
        return t[pos] if pos < len(t) else None

    def nimm():
        nonlocal pos
        if pos >= len(t):
            raise ValueError("unerwartetes Ende")
        pos += 1
        return t[pos - 1]

    def summe():
        wert = produkt()
        while sieh() in ("+", "-"):
            op = nimm()
            r = produkt()
            wert = wert + r if op == "+" else wert - r
        return wert

    def produkt():
        wert = potenz()
        while sieh() in ("*", "/"):
            op = nimm()
            r = potenz()
            if op == "*":
                wert *= r
            else:
                if r == 0:
                    raise ValueError("Division durch null")
                wert /= r
        return wert

    def potenz():
        basis = vorzeichen()
        if sieh() == "^":
            nimm()
            return basis ** potenz()
        return basis

    def vorzeichen():
        if sieh() == "-":
            nimm()
            return -vorzeichen()
        if sieh() == "+":
            nimm()
            return vorzeichen()
        return atom()

    def atom():
        tok = nimm()
        if tok == "(":
            w = summe()
            if nimm() != ")":
                raise ValueError("schliessende Klammer fehlt")
            return w
        try:
            return float(tok)
        except ValueError:
            raise ValueError(f"Zahl erwartet statt {tok!r}")

    ergebnis = summe()
    if pos != len(t):
        raise ValueError("unerwartete Zeichen am Ende")
    return ergebnis
