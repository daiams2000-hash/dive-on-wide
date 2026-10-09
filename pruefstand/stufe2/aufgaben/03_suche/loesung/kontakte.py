def finde(kontakte, begriff):
    b = begriff.strip().casefold()
    if not b:
        return list(kontakte)
    return [k for k in kontakte if b in k["name"].casefold()]
