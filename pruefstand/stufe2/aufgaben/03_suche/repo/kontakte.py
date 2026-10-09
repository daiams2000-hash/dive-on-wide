def finde(kontakte, begriff):
    return [k for k in kontakte if begriff in k["name"]]
