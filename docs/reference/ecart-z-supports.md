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
Les deux rejeux de la variante à 0,38 mm donnent les mêmes mesures entre
eux ; leur G-code ne diffère que par la date de génération. Les 3MF bruts gardent leurs empreintes
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

### Espacements de l’interface et de la base

Deux sondes sur le même coupon à support arborescent `organic` et
interface concentrique, avec le même binaire, ont
changé une seule clé à la fois. La valeur demandée reste embarquée dans
chaque archive. Les deux répétitions de chaque variante donnent le
même G-code hors date de génération.

| Mesure du coupon | Témoin | Interface de 0,5 à 0,8 mm | Base de 2,5 à 3,5 mm |
| --- | ---: | ---: | ---: |
| Segments `Support` | 20 019 | 18 919 | 20 019 |
| Segments `Support interface` | 64 | 48 | 64 |
| Longueur `Support interface` | 523,232 mm | 404,616 mm | 523,232 mm |
| Écart Z médian estimé | 0,2 mm | 0,2 mm | 0,2 mm |
| Projection discrète | 177,5 mm² | 152,5 mm² | 177,5 mm² |
| Estimations de matière et de durée | 3,28 g et 990 s | 3,22 g et 980 s | 3,28 g et 990 s |

`support_interface_spacing` a un effet numérique reproductible sur ce
coupon. Avec `support_base_pattern_spacing`, les coordonnées des trajets
des rôles de support mesurés gardent la même empreinte que le témoin.
Cette absence d’effet observé reste limitée à la géométrie et au type de
support essayés. Les sources et les 646 profils relus sont intacts. Les
avertissements du trancheur restent identiques et la recette du retrait
physique reste à faire (#89).

Sur le témoin à support normal `grid`, la même variation isolée de
`support_base_pattern_spacing` change les trajets. Les deux rejeux
passent de 921 à 786 segments `Support` et de 7 813,261 à 6 670,812 mm
d’axes de dépôt de ce rôle. La médiane Z reste à 0,18 mm. La projection
discrète passe de 121 à 113,5 mm² et les estimations du slicer de
3,64 à 3,49 g et de 984 à 982 s. Les rejeux concordent hors date et
les profils relus et sources restent intacts.

## Variabilité observée du style arborescent fort

Sur le même coupon synthétique, le style `tree_strong` est conservé
avec `support_type=tree(auto)` par ASN 2.0.0.3. Le binaire a le SHA-256
`618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d`.
Les deux entrées `tree_strong` sont identiques octet pour octet et ont
été tranchées dans deux dossiers distincts. Face au témoin `organic`, seul
`support_style` diffère dans les réglages, avant et après tranchage.

| Mesure du coupon | `organic` | `tree_strong`, essai 1 | `tree_strong`, essai 2 |
| --- | ---: | ---: | ---: |
| Segments `Support` | 20 019 | 22 669 | 22 638 |
| Longueur `Support` | 7 087,421 mm | 5 557,530 mm | 5 551,561 mm |
| Écart Z médian estimé | 0,200 mm | 0,735 mm | 0,735 mm |
| Estimation du temps | 990 s | 973 s | 972 s |
| Estimation de matière | 3,28 g | 3,26 g | 3,26 g |

Les G-code `tree_strong` diffèrent au-delà du commentaire de date, malgré
les empreintes d’entrée identiques. Les 646 profils relus et la référence
sont intacts. La variabilité observée ne suffit pas à en isoler la cause :
la répétabilité du style reste NON VÉRIFIÉE (#208, suite de #89). Aucun
choix de support ni verdict physique ne découle de cet essai.

### Comparer deux ou trois rejeux déjà publiés

`benchab controle rejeux-gcode RECU1.json RECU2.json --plaque 1`
lit les reçus privés du lanceur et relit les archives 3MF de sortie
identifiées par SHA-256. Les reçus doivent déclarer la même empreinte
source et la même empreinte de copie fournie au moteur. La commande
ne démarre pas le trancheur et n’authentifie pas les exécutions.
Un SHA de binaire absent des reçus reste NON VÉRIFIÉ.

Avec `--resume --sortie prive/rapports/rejeux.json`, le rapport intégral est
publié avant l’affichage court. Le résumé garde la plaque, le statut, le
code et les verdicts de comparaison. Les empreintes, les mesures et le
motif exact d’un refus restent dans le JSON privé. Sans option, le
rapport intégral est affiché.

Le rapport distingue l’égalité du G-code brut, l’égalité après exclusion
du seul horodatage de la deuxième ligne d’un bloc d’en-tête ASN complet,
l’égalité des dépôts de
support et celle de leurs agrégats (segments et mm par rôle). Le code 0
indique l’égalité hors date, 1 une divergence conservée, 2 une comparaison
NON VÉRIFIÉE faute de preuve ou de couverture. `--sortie` peut publier
un JSON privé neuf. Une divergence ne désigne ni une cause ni une variante
à adopter (#208).

Trois nouveaux rejeux hors ligne ont lu le même fichier `tree_strong`
depuis le même chemin source, avec des dossiers de travail distincts.
Deux ont donné 22 669 segments `Support` (5 557,530 mm) et un
22 638 segments (5 551,561 mm). Les archives et reçus de chaque essai
restent distincts. Le rapport de comparaison signale WARN ; le même
contrôle reconnaît, sur deux archives de l’essai XY, une différence
limitée à la date. Cela établit la variabilité dans les conditions
observées, sans isoler sa cause ni la fréquence des résultats (#208).
Les familles de support restent à qualifier dans #89.

`--resume --sortie RAPPORT.json` affiche le statut, la plaque, l’écart
minimum en mm et le chemin du rapport complet privé. Avec `--sortie`
seule, le rapport complet reste aussi affiché. NON MESURABLE garde
sa raison et le code 0 ; une erreur de lecture ou de destination
donne le code 2. Aucune base ou mesure d’atelier n’est créée (#35).

### Styles de support normal grille et ajusté

Avec `support_type=normal(auto)`, le binaire ASN 2.0.0.3 conserve
`support_style=grid` et `support_style=snug` sur le coupon à interface
concentrique. La [liste de styles publiée par ASN](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L222-L244)
identifie ces valeurs ; la comparaison d’intégrité confirme que cette
clé est la seule différence avant et après tranchage.

| Style | Segments Support | Segments Support interface | Écart Z médian nominal | Projection estimée | Temps et matière estimés |
| --- | ---: | ---: | ---: | ---: | ---: |
| `grid` | 921 | 126 | 0,18 mm | 121 mm² | 984 s ; 3,64 g |
| `snug`, deux rejeux | 859 | 96 | 0,18 mm | 137,5 mm² | 977 s ; 3,52 g |

Les deux rejeux `snug` partent des mêmes octets et reproduisent le G-code
à la date près. La source et les 646 profils sont intacts. Les avertissements
du slicer restent présents. Une projection plus grande ne prouve ni un
contact physique, ni un retrait plus facile. Le résultat ne s’étend pas à
un autre type de support ou à une autre géométrie (#80, #89).
