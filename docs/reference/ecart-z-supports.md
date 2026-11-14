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

### Variabilité observée du style arborescent fin

Avec le même coupon et le même binaire, `tree_slim` reste embarqué
après tranchage. Face au témoin `organic`, seul `support_style` diffère
dans les réglages avant et après tranchage.

| Mesure du coupon | `organic` | `tree_slim`, essai 1 | `tree_slim`, essai 2 |
| --- | ---: | ---: | ---: |
| Segments `Support` | 20 019 | 15 203 | 15 514 |
| Longueur `Support` | 7 087,421 mm | 3 464,342 mm | 3 560,624 mm |
| Écart Z médian estimé | 0,200 mm | 0,735 mm | 0,735 mm |
| Projection estimée | 177,5 mm² | 80,5 mm² | 79,0 mm² |
| Temps et matière estimés | 990 s ; 3,28 g | 958 s ; 3,00 g | 959 s ; 3,02 g |

Les deux copies `tree_slim` ont la même empreinte d’entrée. Leurs G-code
et leurs dépôts diffèrent même après exclusion de la date : répétabilité
NON VÉRIFIÉE sur ce coupon. La source et les 646 profils relus sont
intacts. Cet essai ne qualifie ni le retrait ni le contact physique (#89).

### Style arborescent hybride

Sur le même coupon, deux tranchages indépendants avec `tree_hybrid`
donnent le même G-code hors date. Seul `support_style` diffère du
témoin `organic` dans les réglages, avant et après tranchage.
Les segments `Support` passent de 20 019 à 855 et leur longueur
totale de 7 087,421 à 7 246,577 mm. La médiane Z estimée passe
de 0,200 à 0,180 mm et la projection de 177,5 à 146,0 mm².
Le temps estimé passe de 990 à 969 s, avec une masse estimée de
3,28 à 3,52 g. La source et les 646 profils relus sont intacts.
La répétabilité numérique observée se limite à ce coupon et à
ces deux rejeux ; la validation physique reste NON VÉRIFIÉE (#89).

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

### Style normal par défaut

Sur le même coupon et le même binaire, deux rejeux indépendants avec
`support_style=default` conservent cette valeur dans le 3MF tranché.
Seul ce réglage diffère du témoin `grid` avant et après tranchage.
Les deux rejeux concordent hors date et les trajets de support et
d’interface mesurés sont identiques à ceux du témoin. La médiane Z
reste à 0,18 mm, la projection à 121 mm² et les estimations à
984 s et 3,64 g. La source et les 646 profils relus sont intacts.
L’absence d’effet observé sur ce coupon ne démontre pas une
équivalence générale entre `default` et `grid` (#80, #89).

### Limitation aux régions critiques

Sur le coupon à support organique et interface concentrique, deux
tranchages de la copie avec `support_critical_regions_only=1`
conservent la valeur. C’est la seule différence de réglage dans le 3MF
de sortie par rapport à la référence à 0. Les deux rejeux concordent
hors date et reproduisent les trajets de support de la référence :
20 019 segments, 7 087,421 mm, écart Z médian de 0,20 mm et projection
de 177,5 mm². Les estimations restent à 990 s et 3,28 g. La source,
la référence tranchée et les 646 profils relus sont intacts.
Ce coupon ne discrimine pas l’option. Il faut une géométrie comportant
des régions dont le statut critique diffère pour qualifier son effet (#89).

Sur le même coupon, la désactivation de `support_remove_small_overhang`
(1 à 0) donne aussi deux G-code identiques hors date entre eux.
La valeur est conservée et constitue la seule différence de réglage
dans le 3MF tranché. Les trajets de support et d’interface, l’écart Z
et les estimations restent identiques à ceux du témoin.
La source et les 646 profils relus sont intacts. Il faut un témoin
comportant des petits surplombs isolés pour qualifier l’effet (#89).

### Témoin à quatre surplombs isolés

Le témoin dédié comporte quatre piliers séparés, avec des tablettes
en porte-à-faux à 11,9 mm du plateau. Chaque zone est relue dans une
boîte sous sa tablette, entre 8 et 12 mm de hauteur. Avec ASN 2.0.0.3
et le style organique, seul `support_remove_small_overhang` change
entre l’entrée et la sortie des variantes comparées.

| Débord × largeur | Corps sans → avec filtre | Interface sans → avec filtre |
| --- | ---: | ---: |
| 1,4 × 2 mm | 143,3 → 0 mm | 6,8 → 0 mm |
| 2,5 × 3 mm | 196,8 → 196,8 mm | 17,2 → 17,2 mm |
| 4,5 × 5 mm | 316,2 → 331,9 mm | 42,0 → 42,0 mm |
| 10 × 12 mm | 942,8 → 894,1 mm | 110,5 → 110,5 mm |

Les mesures sont des longueurs d’axes déposés dans les boîtes, pas
des volumes. Le filtre supprime le support sous le plus petit débord ;
les trois autres gardent leur interface, avec des trajets du corps
qui peuvent varier. Le temps et la masse de la plaque passent de
959 s et 4,61 g à 936 s et 4,49 g. La médiane Z reste à 0,40 mm.
Le rejeu filtré reproduit le G-code hors date. Les sources et les
646 profils relus sont intacts. Les quatre tailles ne définissent
pas un seuil universel et le comportement à l’impression reste
NON VÉRIFIÉ (#89).

### Régions critiques sur les quatre surplombs isolés

Sur le même témoin, avec le filtre des petits surplombs désactivé,
les archives d’entrée et de sortie ne diffèrent que par le réglage
`support_critical_regions_only` (0 à 1) dans les réglages contrôlés.
Dans les boîtes sous les débords de 1,4 × 2 et 2,5 × 3 mm, les
trajets de support et d’interface passent à zéro. Les débords de
4,5 × 5 et 10 × 12 mm conservent leurs 42,0 et 110,5 mm d’interface.
Au total, les trajets de corps de support passent de 6 713,337 à
4 360,789 mm. La médiane Z reste à 0,40 mm et la projection
estimée passe de 64,0 à 56,25 mm². La plaque passe de 959 à
915 s et de 4,61 à 4,37 g estimés. Le rejeu à partir de la même
copie reproduit le G-code hors date. Les sources et les 646 profils
relus gardent leurs empreintes. La tenue des débords et le retrait
du support restent à observer sur cette géométrie (#89).

### Comparaison des deux options ensemble

Les quatre variantes sont retranchées à partir du même témoin avec
ASN 2.0.0.3. La relecture des 3MF confirme que seules les deux
options étudiées diffèrent. Un débord est compté sans support lorsque
sa boîte de mesure ne contient ni trajet de support ni trajet d’interface.

| Options actives | Débords sans support dans les boîtes | Corps de support sur la plaque | Masse estimée | Durée estimée |
| --- | --- | ---: | ---: | ---: |
| Aucune | Aucun | 6 713,337 mm | 4,61 g | 959 s |
| Petits surplombs | 1,4 × 2 mm | 5 519,369 mm | 4,49 g | 936 s |
| Régions critiques | 1,4 × 2 et 2,5 × 3 mm | 4 360,789 mm | 4,37 g | 915 s |
| Les deux | 1,4 × 2 et 2,5 × 3 mm | 4 360,789 mm | 4,37 g | 915 s |

Avec les deux options, les commandes du G-code sont identiques à
celles de la variante « régions critiques » seule. Après exclusion
de l’horodatage, seul le commentaire du réglage des petits surplombs
diffère. Le rejeu des deux options reproduit le G-code hors date.
L’effet combiné est établi sur ce témoin et ce binaire (#89).

### Interface, jeu de première couche, expansion et angle

Sur le témoin à quatre débords, avec ASN 2.0.0.3 et les deux filtres
désactivés, chaque variante change un seul réglage. Il est conservé
dans le 3MF tranché. Les deux tranchages de chaque variante concordent
hors date. Les longueurs ci-dessous couvrent la plaque entière.

| Style et changement | Corps de support avant → après | Interface avant → après | Masse et durée avant → après |
| --- | ---: | ---: | --- |
| Organique, couches supérieures d’interface de 2 à 0 | 6 713,337 → 6 566,221 mm | 176,478 → 0 mm | 4,61 → 4,58 g, 959 → 953 s |
| Organique, jeu de première couche de 0,2 à 0,8 mm | 6 713,337 → 6 631,200 mm | 176,478 → 176,478 mm | 4,61 → 4,60 g, 959 → 957 s |
| Organique, expansion de 0 à 1,5 mm | 6 713,337 → 6 713,337 mm | 176,478 → 176,478 mm | 4,61 g, 959 s |
| Organique, angle seuil de 45 à 65° | 6 713,337 → 6 713,337 mm | 176,478 → 176,478 mm | 4,61 g, 959 s |
| Grille normale, expansion de 0 à 1,5 mm | 7 985,513 → 11 218,070 mm | 1 035,312 → 1 035,312 mm | 5,02 → 5,41 g, 940 → 970 s |

Le jeu ne modifie les trajets du corps que sur la première couche
(Z=0,20 mm, 401 à 381 segments). Sans interface supérieure,
la médiane Z estimée passe de 0,40 à 0,20 mm. Avec l’expansion en
grille, elle reste à 0,18 mm et la projection à 67,25 mm².
En organique, l’expansion et l’angle conservent les mêmes empreintes
de trajets de support. Les dessous horizontaux de ce témoin ne
discriminent pas les deux angles. Les sources et les 646 profils
relus gardent leurs empreintes. La tenue et le retrait restent
à mesurer (#89).

### Angle de déclenchement sur des dessous inclinés

Un second témoin comporte trois dessous inclinés à 32,48°, 52,40°
et 73,58° par rapport au plateau, mesurés sur les faces modélisées.
En organique, relever `support_threshold_angle` de 45 à 65° ajoute
2 109,4 mm d’axes de support dans la boîte sous le dessous à 52,40°.
La boîte sous le dessous à 32,48° passe de 1 905,1 à 2 002,6 mm
et celle à 73,58° reste sans dépôt de support. La plaque passe de
3,20 à 3,41 g et de 1 149 à 1 185 s estimés. La médiane Z estimée
reste à 0,40 mm. Seul l’angle seuil diffère dans les réglages
avant et après tranchage. Le rejeu concorde hors date et les sources
et les 646 profils relus gardent leurs empreintes (#89).

### Écart Z inférieur au-dessus d’une surface de la pièce

Un témoin en C place un support normal en grille dans la cavité
entre une semelle et un toit. Dans une fenêtre à l’intérieur de la
semelle, les trajets du dessus de la pièce sont à Z=4,00 mm.
On relève le premier dépôt de l’interface basse dans cette même
fenêtre, puis on retranche sa hauteur déclarée de 0,20 mm pour
obtenir l’altitude nominale de son dessous.

| Consigne Z inférieure | Premier dépôt d’interface | Dessous nominal | Écart à la semelle |
| --- | ---: | ---: | ---: |
| 0,20 mm | 4,40 mm | 4,20 mm | 0,20 mm |
| 0,60 mm, deux tranchages | 4,80 mm | 4,60 mm | 0,60 mm |

Le dernier dépôt de l’interface haute reste à Z=17,82 mm. Les
estimations passent de 6,88 à 6,85 g et de 1 261 à 1 260 s.
Avec ASN 2.0.0.3, seule la consigne inférieure diffère dans les
réglages avant et après tranchage. Le rejeu concorde hors date.
La source et les 646 profils contrôlés gardent leurs empreintes.
Le retrait et les marques sur la semelle restent à observer (#89).

En contrôle, la consigne inférieure reste à 0,20 mm et seul
`support_on_build_plate_only` est activé. Les deux tranchages
suppriment les 3 010 mm d’axes de corps et les 1 152 mm d’interface
dans la fenêtre de la cavité. Le rapport d’écart devient NON MESURABLE,
car aucun écart n’est observable. La masse et la durée estimées
passent de 6,88 à 4,72 g et de 1 261 à 1 156 s. Les deux G-code
de la variante concordent hors date (#89).

### Motif de base avec un support organique

Sur le coupon à pilier et tablette de l’étude d’interface, deux variantes
`support_base_pattern=rectilinear` conservent cette valeur. Seule cette clé
diffère de la référence `default` dans les réglages projet. Avec le support
organique et l’interface concentrique, les deux variantes et la référence
donnent les mêmes empreintes de dépôt par rôle, soit 20 019 segments de
support et 64 d’interface. L’écart Z médian reste de 0,20 mm. Le slicer
estime 990 s et 3,28 g dans les trois cas. Le coupon, la référence et les
646 profils vérifiés gardent leurs empreintes. Aucun effet du changement
n’a été observé sur ce coupon ; les autres géométries et types de support
restent NON VÉRIFIÉS (#89).
