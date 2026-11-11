# Flux séquentiel des membres 3MF

## Décision

Ajouter à `Archive3MF` un flux non seekable dont chaque `read()` rend au plus 64 Kio.
Réserver la taille déclarée dans le budget cumulatif à l’ouverture du membre, et non
octet par octet : même une sortie partielle consomme le budget qui empêche de rouvrir
le même membre pour décompresser le reste gratuitement. À EOF, le lecteur compare la
taille réelle et `zipfile` vérifie le CRC. Une lecture interrompue ferme le flux sans
forcer une décompression supplémentaire. Les membres échappés sont fermés à la sortie
de l’archive.

## Portée

Cette étape migre uniquement les lectures G-code de `benchab/gcode/lecture.py` et
`benchab/gcode/continuite.py`. Le cache SHA et le cache d’analyse existants restent en
place. `Archive3MF.ouvrir()` conserve son contrat mémoire. Les autres familles de
lectures ZIP ne sont pas migrées par cette étape.

## Vérifications ciblées

Les tests couvrent vrais ZIP, flux partiel, CRC à EOF, budget cumulatif, lecture
séquentielle, fermeture du flux échappé, cache G-code et résultats de continuité.
