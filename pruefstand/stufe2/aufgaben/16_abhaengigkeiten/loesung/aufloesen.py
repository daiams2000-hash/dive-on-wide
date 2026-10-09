def reihenfolge(pakete):
    alle = list(pakete)
    bekannt = set(alle)
    for deps in pakete.values():
        for d in deps:
            if d not in bekannt:
                bekannt.add(d)
                alle.append(d)
    ergebnis, fertig = [], set()
    for start in alle:
        if start in fertig:
            continue
        stapel = [(start, iter(pakete.get(start, [])))]
        pfad, im_pfad = [start], {start}
        while stapel:
            name, it = stapel[-1]
            weiter = None
            for dep in it:
                if dep in fertig:
                    continue
                if dep in im_pfad:
                    i = pfad.index(dep)
                    raise ValueError("Zyklus: " + " -> ".join(pfad[i:] + [dep]))
                weiter = dep
                break
            if weiter is None:
                stapel.pop()
                pfad.pop()
                im_pfad.discard(name)
                fertig.add(name)
                ergebnis.append(name)
            else:
                stapel.append((weiter, iter(pakete.get(weiter, []))))
                pfad.append(weiter)
                im_pfad.add(weiter)
    return ergebnis
