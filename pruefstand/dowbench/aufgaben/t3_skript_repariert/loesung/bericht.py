import json
werte = json.loads(open('werte.json').read())
summe = sum(w['betrag'] for w in werte)
open('bericht.txt', 'w').write('Summe: %.2f\n' % summe)
