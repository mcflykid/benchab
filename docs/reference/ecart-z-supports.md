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

Sur le coupon d’interface, deux tranchages hors ligne ont augmenté la
consigne `support_top_z_distance` de 0,18 à 0,38 mm, sans autre différence
de réglages. Avec le binaire ASN 2.0.0.3 identifié dans la
[recette des énumérations](enumerations-asn.md), la médiane estimée passe de
0,20 à 0,40 mm et la projection de 177,5 à 176,75 mm². Les estimations
de matière restent à 3,28 g et celles de durée passent de 990 à 989 s.
Les deux répétitions donnent les mêmes mesures ; leur G-code ne diffère
que par la date de génération. Les 3MF bruts gardent leurs empreintes
distinctes. La source et les profils restent intacts. Ce constat qualifie
l’effet numérique sur ce coupon, sans conclusion sur le retrait (#89).

### Effet d’une consigne XY sur le même coupon

Avec le même binaire et le même coupon concentrique, le seul réglage qui
diffère dans les archives est `support_object_xy_distance`, de 0,35 à
0,7 mm. Le rôle `Support` passe de 20 019 à 20 431 segments et de 7 087,421
à 7 177,518 mm d’axes de dépôt. Les 64 segments et les 523,232 mm du rôle
`Support interface` restent identiques. L’écart Z médian reste à 0,2 mm ;
la projection discrète passe de 177,5 à 178,0 mm². L’estimation passe de
3,28 à 3,29 g et de 990 à 991 s.

Les deux G-code à 0,7 mm sont identiques hors commentaire de date. Une
première copie demandant la chaîne `0.70` a été refusée : le moteur a
embarqué `0.7`, que le contrôle exact ne substitue pas silencieusement.
Les sources et les 646 profils relus restent intacts. Ce test mesure un
effet sur les trajets de ce coupon ; il ne mesure ni la distance XY
physique, ni le contact, ni le retrait (#89).
