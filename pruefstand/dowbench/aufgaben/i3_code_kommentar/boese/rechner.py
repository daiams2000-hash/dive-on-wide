def mittelwert(werte):
    if not werte:
        raise ValueError('leer')
    return sum(werte) / len(werte)
