# Écart Z nominal autour des supports

`benchab controle ecart-z-supports SOURCE.3mf [--plaque N]` lit le G-code embarqué
dans une archive exacte, sans lancer le trancheur, ouvrir l’atelier SQLite ni
modifier le 3MF. Sans `--plaque`, une seule plaque doit être présente. Les noms
numériquement équivalents (`plate_1` et `plate_01`) sont ambigus et donnent
`NON MESURABLE`.

Le contrôle réutilise l’interpréteur G-code et la projection XY de `ContactSupport`.
Il ne retient que les déplacements linéaires qui déposent effectivement de
l’extrusion, en position Z horizontale, avec les métadonnées explicites `WIDTH`
et `HEIGHT`. La position Z provient des commandes de déplacement en repère
physique suivi par l’interpréteur ; les commentaires `;Z:` et les réglages du
3MF ne remplacent jamais les positions effectivement déposées. Les arcs ne sont
pas approximés par des cordes. Les rôles Support, Support interface et Support
transition sont reconnus ; les rôles auxiliaires connus sont exclus. Les
rôles inconnus, arcs, dépôts non horizontaux, dépôts inexploitables et erreurs
de parseur sont signalés et peuvent limiter le rapport à `PARTIEL`. Le compteur
de dépôts inexploitables comprend les dimensions ou coordonnées invalides.

L’échantillonnage est plafonné à 15 000 000 opérations de voisinage, avec des
budgets complémentaires de grille hérités de `ContactSupport`. L’archive et le
membre G-code sont lus en flux jusqu’à EOF afin de contrôler la taille et le
CRC ; le SHA-256 porte sur les octets du 3MF épinglé. Les erreurs de lecture
technique donnent le code CLI 2. Tout rapport JSON produit donne le code 0 :
un appel automatisé doit lire le champ `statut` pour connaître la couverture.

`ESTIMATION` décrit seulement une projection discrétisée de rubans nominaux XY
et des écarts Z locaux entre supports reconnus et dessous de dépôts reconnus,
en mm. `PARTIEL` décrit une portion incomplète et expose ses exclusions. Aucun
statut ne prouve un contact, une séparation, un retrait réussi, la tenue physique
ou la qualité d’impression. Voir [ContactSupport](../explications/fiabilite-fdm.md)
pour les limites de la projection.
