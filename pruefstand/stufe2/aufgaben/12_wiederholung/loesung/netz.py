import time


def mit_wiederholung(funktion, versuche=3, basis=0.5, schlafen=time.sleep,
                     fehler=(ConnectionError, TimeoutError)):
    if versuche < 1:
        raise ValueError("versuche muss >= 1 sein")
    for n in range(versuche):
        try:
            return funktion()
        except fehler:
            if n == versuche - 1:
                raise
            schlafen(basis * 2 ** n)
