# Énumérations d’Anycubic Slicer Next

`benchab controle enumerations-asn -- ARCHIVE.3mf` compare les valeurs
énumérées présentes aux observations connues pour le binaire configuré.
La commande relève sa version et son SHA-256 sans démarrer le moteur, lit le
3MF sans écrire, et n’attribue pas au moteur la production de cette archive.
Le dictionnaire typé conserve les valeurs exactes, casse et espaces compris.

- **PASS** : la valeur a été conservée dans le 3MF tranché et un effet a été
  mesuré dans le rôle G-code concerné, dans les conditions du témoin.
- **FAIL** : la valeur contredit le type ou un remplacement a été observé.
- **NON VÉRIFIÉ** : binaire, clé ou valeur sans observation suffisante ; l’absence
  du catalogue ne prouve pas qu’une valeur est refusée.

## Binaire observé

ASN 2.0.0.3, SHA-256
`618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d`.
Le témoin S a été copié avant chaque variante et tranché par le lanceur commun.
Les valeurs ont été relues dans le 3MF et les dépôts comparés par rôle. La source
et les empreintes de 646 profils installés ou utilisateur sont restées intactes.
Les rapports et fichiers de recette restent dans l’atelier privé.

| Réglage | Valeurs conservées | Mesure numérique |
| --- | --- | --- |
| `sparse_infill_pattern` | `gyroid`, `grid`, `rectilinear`, `zigzag`, `crosshatch` | Trajectoires distinctes ; 5 502, 468, 456, 456 et 1 602 segments déposés dans cet ordre. |
| `internal_solid_infill_pattern` | `rectilinear`, `monotonic` | Trajectoires de remplissage plein interne distinctes. |
| `top_surface_pattern` | `monotonicline`, `rectilinear` | Trajectoires de surface supérieure distinctes. |
| `bottom_surface_pattern` | `monotonic`, `rectilinear` | Trajectoires de surface inférieure distinctes. |
| `alternate_extra_wall` | `0`, `1` | Sur le témoin S, 540 → 576 segments de paroi interne ; paroi externe inchangée. |
| `wall_sequence` | `inner wall/outer wall`, `outer wall/inner wall`, `inner-outer-inner wall` | Ordres de dépôt des parois distincts. |

Avec `zig-zag`, le moteur a sorti `zigzag` et les mêmes trajectoires qu’avec
`zigzag` explicite. Une valeur fictive a donné `crosshatch`. Dans les deux cas,
le moteur a terminé normalement et BENCHAB a refusé l’intégrité du résultat.
Une variante explicite `crosshatch` a conservé la valeur et produit les mêmes
trajectoires que le repli de la valeur fictive. Cette variante est aussi la
valeur de remplissage clairsemé dans les défauts exportés par ce binaire.

Lors des essais de peau et squelette verrouillés (motif clairsemé
`lockedzag`, valeur `rectilinear`), les champs
`locked_skin_infill_pattern` et `locked_skeleton_infill_pattern` étaient
absents des sorties et l’intégrité a refusé chacune. Ils sont également absents
des défauts exportés. Leur effet reste NON VÉRIFIÉ ; cela ne prouve pas une
absence dans toutes les configurations.

Le [code source publié d’Anycubic](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L2182)
fournit des noms candidats. Aucun lien n’est établi entre cette révision et
le binaire mesuré ; seuls le 3MF natif et le G-code tranché appuient les
observations du tableau. Rien ici ne valide une vitesse ou une tenue physique.

## Interface de support

Le même binaire ASN a tranché un coupon synthétique composé d’un pilier de
12 × 14 × 15 mm et d’une tablette de 28 × 14 × 3 mm, avec un recouvrement de
0,2 mm. Les supports étaient de type arbre automatique, style organique,
sur le plateau seulement, avec deux couches d’interface en haut et en bas.
Les empreintes des 646 profils relus et du coupon sont restées identiques.

Les profils système Kobra S1 pour buse de 0,4 mm déclarent `auto`. La valeur
candidate `concentric` figure dans le [code source publié d’Anycubic](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L4664-L4682).
Ce lien fournit un nom candidat ; il n’identifie pas la révision du binaire.

| Valeur demandée | Valeur embarquée | Segments d’interface | Longueur nominale | Temps et masse estimés du 3MF |
| --- | --- | --- | --- | --- |
| `auto` | `auto` | 100 | 525,374 mm | 989 s ; 3,28 g |
| `concentric` | `concentric` | 64 | 523,232 mm | 990 s ; 3,28 g |
| `concentric`, répétition | `concentric` | 64 | 523,232 mm | 990 s ; 3,28 g |

La relecture des paquets confirme que seule `support_interface_pattern`
diffère dans leurs réglages projet, et que les empreintes des trajectoires
du rôle `Support interface` diffèrent du témoin. Les deux essais
`concentric` donnent la même empreinte de trajectoires. Les autres valeurs,
styles, matériaux, la mesure d’écart Z et le retrait physique restent hors
de ce résultat (#80 et #89). Aucun réglage n’est adopté automatiquement.

Le moteur a terminé avec un code nul, mais chaque journal natif contient
les mêmes cinq lignes d’erreur de calcul des zones de placement et des
triangles d’exclusion. L’interpréteur G-code n’a relevé aucune erreur dans
les trois sorties. La qualification porte sur la comparaison des motifs
dans les fichiers obtenus ; ces messages n’établissent pas la fiabilité
générale du moteur pour ce coupon.

### Écart Z nominal sur les mêmes archives

Une relecture sans retranchage a comparé les dépôts des rôles de pièce et
de support sur les trois 3MF. Le rôle auxiliaire `Custom` est hors de la
mesure. Les altitudes viennent des déplacements extrudés interprétés ;
`HEIGHT` et `WIDTH` servent au dessous nominal et à la projection.
Aucune altitude n’est déduite du réglage demandé ou d’un commentaire
de couche seul.

Avec une grille XY de 0,5 mm et une recherche limitée à 2 mm, la projection
repère 770 cellules pour `auto` (192,5 mm²) et 710 pour les deux
`concentric` (177,5 mm²). Pour les minima locaux sur les cellules mesurées,
la médiane est de 0,20 mm dans les trois cas ; le troisième quartile passe
de 0,40 à 0,60 mm. Le budget historique de deux millions de points était
insuffisant ; cette relecture a fixé un plafond de quinze millions et n’a
relevé aucune erreur de lecture ni dépôt retenu sans dimensions.

Ces mesures sont une projection nominale du coupon, limitée aux paires
à 2 mm ou moins. Elles ne mesurent ni la surface de contact physique, ni le
retrait ou les marques ; les autres conditions restent non vérifiées (#89).

## Remplissage clairsemé cubique de support

Sur une copie du témoin S, le même binaire a conservé `supportcubic` dans
deux sorties identiques octet pour octet. La comparaison avec le témoin
`gyroid` ne change que `sparse_infill_pattern` dans les réglages projet
des archives tranchées. Le [code source publié par Anycubic](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp)
a fourni un nom candidat, sans preuve de correspondance avec le binaire.

| Motif embarqué | Segments clairsemés déposés | Déplacements (nombre, longueur) | Îlots (total, < 3 mm) | Estimations du trancheur |
| --- | ---: | --- | --- | --- |
| `gyroid` | 5 500 | 44 ; 462 mm | 12 ; 0 | 313 s ; 2,37 g |
| `supportcubic`, deux essais | 303 | 56 ; 384 mm | 30 ; 0 | 303 s ; 2,29 g |

Les segments dépendent de la discrétisation du motif. Le seuil de 3 mm
porte sur la longueur déposée continue, pas sur une surface du slicer.
Les deux répétitions et le témoin n'ont produit aucune erreur de lecture du
G-code. Les trois journaux natifs contiennent les deux mêmes messages
d'exclusion malgré un code nul. Sur une autre géométrie, une boîte
synthétique de 24 × 22 × 12 mm, le trancheur s'est arrêté avec un code
`-11` dès le témoin gyroïde : aucun effet n'y est vérifié. Les 646 profils
vérifiés et les entrées restent intacts dans les deux études.

Le résultat qualifie cette valeur pour le binaire et le témoin S, sans
conclure à la stabilité du moteur, à une économie sur un autre modèle ou à
une résistance physique. Aucun motif n'est appliqué automatiquement
(#80, #81).

## Crosszag et repli de 2dlattice

Deux copies du témoin S avec `sparse_infill_pattern=crosszag` conservent ce
motif sur le même binaire ASN 2.0.0.3. Seule cette clé diffère du témoin
`gyroid` dans les réglages relus. Les 456 segments clairsemés mesurent
4 405,063 mm, contre 5 500 segments et 3 941,970 mm dans le témoin. Le
trancheur estime 305 s et 2,41 g, contre 313 s et 2,37 g. La répétition
donne la même empreinte de trajets, sans diagnostic du lecteur G-code.

La demande `2dlattice` donne un code moteur nul, mais embarque
`crosshatch` avec 1 602 segments clairsemés. L’intégrité de la copie
refuse ce remplacement. Le catalogue signale FAIL pour cette chaîne et
ce binaire ; aucune valeur équivalente ou autre version n’est supposée.

Les sources et les 646 profils relus sont intacts. L’angle, le décalage
et les champs de peau et squelette ne sont pas qualifiés par ces essais
(#80, #81). La durée et la masse sont des estimations du fichier.

### Paroi supplémentaire alternée

Sur le binaire identifié ci-dessus, les copies du témoin S tranchées avec
`alternate_extra_wall=0` et `1` diffèrent par cette seule clé, avant et
après tranchage. La longueur de paroi interne passe de 4 296,59 à
5 274,25 mm ; la paroi extérieure reste à 2 337,17 mm. Les estimations passent
de 313 à 316 s et de 2,37 à 2,48 g. Le rejeu à `1` donne les mêmes
trajets de dépôt et estimations ; les 646 profils relus et l’archive
d’origine gardent leurs empreintes. Ce constat ne qualifie pas les autres
valeurs, ni la résistance, ni les dimensions de la pièce imprimée.

### Hauteur maximale du remplissage combiné

La [définition ASN épinglée](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L2996-L3008)
décrit un maximum en mm ou en pourcentage de la buse. Sur le témoin S
(couches nominales de 0,20 mm, buse déclarée de 0,40 mm), la combinaison
reste activée dans les trois copies. La seule différence de réglage
entre les deux cas est `infill_combination_max_layer_height`.

| Valeur conservée | Niveaux Z de dépôt clairsemé | Hauteur déclarée dans le G-code | Segments et longueur | Estimation ASN |
| --- | ---: | ---: | ---: | ---: |
| `50%` | 12 | 0,20 mm | 5 500 ; 3 941,97 mm | 313 s ; 2,37 g |
| `100%`, deux rejeux | 6 | 0,40 mm | 2 692 ; 1 944,59 mm | 306 s ; 2,32 g |

Les niveaux Z sont lus sur les mouvements de dépôt ; la hauteur est une
métadonnée du G-code. Les trajets extérieurs sont identiques et les deux
rejeux à `100%` donnent les mêmes trajets clairsemés. La source et les 646
profils relus sont inchangés. Les avertissements ASN sur la température de
plateau et la dureté de buse restent présents dans les trois sorties.
Le résultat est limité au binaire ASN 2.0.0.3 déjà identifié et à ce coupon.
Il ne valide ni résistance, ni profil à adopter, ni impression (#80, #81).

### Rejeu de la hauteur combinée sur les archives M et L

Les deux archives ont des empreintes d’entrée distinctes mais donnent,
pour cette comparaison, les mêmes mesures et empreintes de trajets.
À `50%`, le clairsemé occupe 12 niveaux de dépôt, avec 5 370 segments
et 3 635,17 mm (359 s et 2,52 g estimés). À `100%`, il occupe sept
niveaux, avec 2 640 segments et 1 794,12 mm (349 s et 2,48 g estimés).
Le rejeu à `100%` retrouve les mêmes trajets et les parois extérieures
gardent la même empreinte entre réglages. Seule la hauteur maximale
de combinaison diffère avant et après tranchage. Les 646 profils
et les deux sources restent intacts. Les mesures identiques de M et L
n’établissent pas l’effet sur deux géométries indépendantes (#80, #81).

### Vérifier une observation contextuelle

`benchab controle enumerations-asn TRANCHE.3mf --contextuelles` inclut
les valeurs observées sous conditions. Pour `support_style`, les valeurs
`grid` et `snug` renvoient à la [recette des supports normaux](ecart-z-supports.md#styles-de-support-normal-grille-et-ajusté).
Le rapport exige dans le même 3MF le support déclaré actif, le type
`normal(auto)`, l’interface `concentric` et l’écart Z déclaré `0.18`
mm. Toute condition absente ou différente reste NON VÉRIFIÉE. Le verdict
décrit la déclaration et l’observation sur le coupon ; il ne prédit pas
les trajectoires d’une autre géométrie et ne valide pas le retrait.
Sans l’option, l’ensemble historique des contrôles reste inchangé (#80, #89).

La clé `infill_combination_max_layer_height` est aussi contextuelle. Les
valeurs `50%` et `100%` sont reliées aux témoins lorsque la combinaison
est active, avec la buse déclarée à 0,4 mm, une hauteur de couche déclarée
de 0,2 mm et un gyroïde à 20 %. Une autre valeur ou des conditions
différentes restent NON VÉRIFIÉES (#80, #81).

### Direction du remplissage rectiligne

Sur le témoin S et le binaire ASN 2.0.0.3 identifié ci-dessus, passer
`infill_direction` de `45` à `75` change la seule clé entre les deux sorties.
Les segments clairsemés déposés d’au moins 1 mm sont mesurés dans le repère
du plateau, modulo 180° et à 0,1° près. À `45`, on observe 114 segments à 45°
et 114 à 135°. À `75`, on observe 114 segments à 75° et 78 à 165°. Des
segments à 0° et 90° restent présents dans les deux cas. Le rejeu à `75`
retrouve les mêmes comptages. Le témoin source et les 646 profils relus
gardent leurs empreintes.

Les valeurs sont vérifiables avec `--contextuelles` pour le remplissage
rectiligne à 20 %, une buse déclarée de 0,4 mm, une couche de 0,2 mm et
des options de combinaison et d’alignement sur le modèle désactivées.
Le résultat décrit les directions déposées sur ce témoin. L’alignement
sur un effort de la pièce et sa tenue restent NON VÉRIFIÉS (#80).

### Options de peau et de squelette de lockedzag

Sur le témoin S, garder le motif `lockedzag` à 20 %, sans combinaison du
remplissage, avec le profil Kobra S1 de buse 0,4 mm et une couche de 0,2 mm.
Dans ce contexte, quatre copies ajoutent séparément `rectilinear` ou
`zigzag` pour `locked_skin_infill_pattern` ou
`locked_skeleton_infill_pattern`. Le binaire ASN identifié ci-dessus termine
avec le code 0 dans les quatre cas et garde `lockedzag`. Le champ ajouté
disparaît dans chaque sortie ; l’intégrité BENCHAB refuse ces quatre sorties.

Le témoin sans surcharge passe l’intégrité. Les cinq fichiers ont les
mêmes empreintes de dépôts par rôle, dont 1 575 segments de remplissage
clairsemé et 1 023 de plein interne. La lecture ne relève aucun diagnostic.
Le témoin et les 646 profils vérifiés gardent leurs empreintes. Le catalogue
signale l’omission uniquement pour le binaire et les réglages compagnons
observés. L’effet de ces options dans d’autres conditions reste NON VÉRIFIÉ
(#80).

### Alignement sur le repère de la pièce

Un coupon rectangulaire de 38 × 22 × 7 mm est placé à 0°, puis à 30° par
la transformation de l’instance dans le 3MF. Le motif rectiligne est à
20 %, direction déclarée 45°, sans combinaison, profil Kobra S1, buse
de 0,4 mm et couche de 0,2 mm. Les directions sont lues sur les segments
clairsemés déposés d’au moins 2 mm, modulo 180°, à 0,1° près.

| Rotation de la pièce | Alignement sur la pièce | Axes du motif dans le plateau |
| ---: | ---: | --- |
| 0° | 0 ou 1 | 45° et 135° |
| 30° | 0 | 45° et 135° |
| 30° | 1, avec rejeu | 75° et 165° |

À 30°, les deux variantes gardent les mêmes axes de paroi (30° et 120°).
Les segments clairsemés comportent aussi des liaisons selon ces axes.
La seule différence de réglage pour chaque rotation est
`align_infill_direction_to_model`. Le rejeu à 30° avec alignement
retrouve la même empreinte du 3MF tranché. Aucune erreur de l’interpréteur
et aucun changement des 646 profils relus. Le catalogue qualifie les deux
états avec `--contextuelles` pour ce binaire et ce profil. L’effet mécanique
et les autres géométries restent hors de cette observation (#80).

Sur ce même coupon sans rotation, le seuil
`minimum_sparse_infill_area` est passé de `0` à `600`, avec un
rejeu de la variante. La valeur est conservée et aucune autre clé du
projet tranché ne diffère. Les comptages restent de 1 027 segments de
remplissage clairsemé et 1 021 segments de remplissage plein interne.
L’essai ne distingue aucun effet sur cette géométrie. Un autre contour
ou un seuil différent doit être évalué séparément (#80).
