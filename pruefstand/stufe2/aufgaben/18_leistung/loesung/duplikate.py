def gemeinsame(a, b):
    menge = set(b)
    return [x for x in a if x in menge]


def doppelte(werte):
    gesehen, doppelt = set(), set()
    for x in werte:
        if x in gesehen:
            doppelt.add(x)
        gesehen.add(x)
    return [x for x in dict.fromkeys(werte) if x in doppelt]
