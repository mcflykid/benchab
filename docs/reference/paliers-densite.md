# Paliers de densité

`benchab.conception.paliers_densite.modificateurs_radiaux` prépare une première
forme de champ de densité (#83).
L’appel prend un maillage fermé en mm, un centre explicite dans son repère et
un à cinq paliers. Chaque palier indique `rayon_mm` et `densite`, par exemple
`{"rayon_mm": 7, "densite": "35%"}`. Les rayons croissent avec un pas minimal de
0,2 mm ; la densité est une valeur entière en pourcentage. La matière hors du
dernier rayon n’est pas modifiée.

Le premier palier est l’intersection de la pièce et d’une sphère maillée.
Chaque palier suivant retire la région intérieure déjà attribuée. Le contrôle
vérifie les recouvrements en volume et le confinement à la pièce. Une région
trop petite, vide ou invalide donne NON VÉRIFIÉ et aucun candidat partiel.
Aucun ordre de priorité entre modificateurs n’est requis.

Le résultat fournit des volumes `modifier_part` pour un nouveau projet, sans
écrire dans le projet d’entrée. Un témoin synthétique vérifie que le producteur
3MF conserve les trois rôles et leurs densités. PASS signifie que ces
coquilles ont passé le contrôle géométrique numérique. Leur effet dans le
G-code doit être mesuré séparément. Les champs de distance aux surfaces,
la simulation de contraintes et la tenue physique ne sont pas établis par
cette étape.

Sur un coupon de 32 × 24 × 12 mm, trois paliers de rayons 5, 10 et 14 mm
ont été tranchés hors ligne avec Anycubic Slicer Next 2.0.0.3, binaire
SHA-256 `618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d`.
Seule la densité du cœur a varié de 20 % à 60 % dans les réglages relus
avant et après tranchage. Dans la boîte de mesure incluse dans le cœur,
les axes de dépôt clairsemé passent de 163,5 à 454,5 mm. Les deux boîtes
de contrôle dans les couronnes restent à 45,0 et 47,3 mm. Une répétition
à 60 % donne le même G-code hors commentaire daté. Les 646 profils relus
et la source de référence sont intacts.

Ce résultat porte sur trois boîtes intérieures de ce coupon et le motif
gyroïde. Il ne mesure pas l’ensemble de chaque couronne, ne qualifie pas
les avertissements natifs du trancheur et ne valide aucune pièce physique.

## Paliers depuis une face plane

`modificateurs_depuis_plan(piece, point_plan_mm, normale_sortante, paliers)`
emploie des paliers en `profondeur_mm` depuis un plan de face déclaré,
sans supposer que la face est horizontale. La normale indique le côté
extérieur ; la fonction contrôle qu’une face de la pièce coïncide avec le
plan et que la matière reste du côté intérieur. Un plan décalé, traversant
ou tangent seulement par une arête n’est pas accepté.

Les volumes produits sont des tranches parallèles, contenues dans la pièce
et disjointes en volume. Le témoin incliné vérifie les profondeurs, les
volumes analytiques et la conservation du maillage. L’effet G-code de
cette famille et les distances à une surface courbe restent à qualifier
(#83).

### Témoin natif de paliers plans

Un coupon de 32 × 24 × 12 mm, en deux bandes à 6 et 10 mm sous le
dessus déclaré, a été tranché hors ligne avec ASN 2.0.0.3, SHA-256
`618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d`.
L’unique réglage modifié est la densité du premier modificateur,
de 20 % à 60 % avant et après tranchage. Le dépôt clairsemé dans
deux boîtes intérieures mesurées donne :

| Zone | À 20 % | À 60 % |
| --- | ---: | ---: |
| Près de la face | 1 272,1 mm | 3 546,3 mm |
| Plus en profondeur (témoin) | 1 279,3 mm | 1 279,3 mm |

Les 61 448 lignes du G-code répété à 60 % ne diffèrent que sur la
date de génération. Les 646 profils relus et l’archive de référence
sont inchangés. Le résultat vaut pour ces boîtes et ce binaire ;
les avertissements natifs de température du plateau et de dureté de
buse ne sont pas levés. Aucun envoi ni essai de résistance n’a été fait.

## Paliers autour d’une fixation

`modificateurs_autour_fixation(piece, fixation, paliers)` mesure le voisinage
d’une fixation extérieure maillée, placée dans le repère de la pièce.
Chaque palier donne `distance_mm` et `densite`. Le noyau dilate le volume de
la fixation par une boule polyédrique inscrite, coupe le résultat à la
pièce et retire les zones déjà attribuées. Le rapport indique l’écart
radial maximal de la boule à la distance demandée. Une fixation qui
chevauche déjà la matière, une bande vide ou un échec du noyau empêche
la production des modificateurs. Le résultat de tranchage ci-dessous couvre une fixation témoin (#83).

`modificateurs_depuis_surface(piece, paliers)` répartit les densités en
bandes internes depuis l’ensemble de la surface d’un solide fermé.
Chaque palier déclare une `profondeur_mm` et une `densite`. L’érosion par
une boule polyédrique conserve un cœur dont la différence avec le solide
donne la bande cumulée. Le témoin rectangulaire vérifie les volumes à
deux profondeurs avec des cotes analytiques. Le budget de calcul peut
exiger un maillage plus simple.

### Effet mesuré dans le G-code

Sur un coupon de 32 × 24 × 12 mm, Anycubic Slicer Next 2.0.0.3 a
tranché chaque cas à 20 %, à 60 %, puis une seconde fois à 60 %.
Le SHA-256 du binaire correspond à celui du témoin plan ci-dessus.
Le seul réglage différent entre les deux densités, avant et après
tranchage, est `sparse_infill_density` du premier modificateur.
Les maillages et placements ont été relus et comparés entre variantes.

| Paliers et zone mesurée | Dépôt clairsemé à 20 % | À 60 % | Zone de contrôle, aux deux densités | Verdict |
| --- | ---: | ---: | ---: | --- |
| Surface, 3 et 5 mm | 116,6 mm | 345,6 mm | 716,0 mm | PASS |
| Fixation, 9 et 13 mm | 84,3 mm | 290,8 mm | 1 685,8 mm | PASS |
| Fixation, 4 et 8 mm, petite boîte | 11,0 mm | 0,0 mm | 1 685,8 mm | FAIL |

Chaque boîte cible est entièrement dans le premier palier. Chaque boîte de
contrôle en est disjointe. L’axe du dépôt est mesuré dans ces boîtes.
Les rejeux à 60 % reproduisent les mesures et ne diffèrent que par
l’horodatage du G-code. Les 646 profils relus et l’archive de référence
gardent leurs empreintes. Les avertissements du trancheur sur le plateau
et la dureté de la buse sont identiques.

Le troisième cas montre que l’effet est local. Une densité plus élevée
peut modifier le tracé au point de supprimer le passage dans une boîte
étroite. Le verdict vaut pour la boîte et la géométrie mesurées. La tenue
physique et le calcul volumique des contraintes restent à établir (#83).

### Contrôle du champ Application

Sur la variante surface à 60 %, remplacer uniquement le champ
`Application` de l’entrée par `AnycubicSlicerNext-2.0.0.3` à la
place de `BambuStudio-2.0.0.3` conserve les deux mesures locales
(345,6 et 716,0 mm) dans les sorties du moteur. Le contrôle d’intégrité
refuse toutefois la sortie de cette variante, dont le moteur réécrit
`different_settings_to_system`. La comparaison de la sortie refusée
avec la référence révèle aussi des différences de matrices et de
métadonnées de projet. Ce rejeu ne qualifie pas cette modification
d’archive pour la production et ne change pas le refus (#82).

## Étude volumique des contraintes

```sh
benchab conception contraintes-volumiques -- \
  --dimensions-mm 60 8 6 --force-n 0 0 -4 \
  --young-mpa 1800 --poisson 0.3 --source-matiere "Témoin analytique" \
  --divisions 24 4 5 --sortie prive/rapports/console.json
```

La commande résout l’élasticité linéaire isotrope sur un pavé plein.
Le plateau déclaré est Z=0, l’appui bloque les trois déplacements sur
X=0 et la force résultante déclarée est répartie uniformément sur X=L.
Le solveur vérifie l’équilibre des forces et compare le maillage demandé
au même maillage raffiné deux fois par axe. Il utilise les dépendances
NumPy et SciPy déjà installées par BENCHAB.

Le rapport donne le déplacement moyen de la face chargée, la réaction,
la contrainte de von Mises moyennée par volume dans quatre bandes
axiales, et leur variation entre maillages. Les bandes servent à
localiser des zones à étudier. L’étude ne leur associe aucune densité.
Le rapport distingue les déplacements et contraintes numériques de la
validation physique, qui reste NON VÉRIFIÉE.

En traction avec Poisson nul, un coupon de 30 × 8 × 6 mm sous 48 N et
avec un module de 1 500 MPa retrouve 0,020 mm de déplacement et 1 MPa.
En flexion, la commande ci-dessus donne −0,835 et −1,022 mm sur
2 880 et 23 040 tétraèdres. La référence de poutre élancée donne −1,111 mm.
Le maillage fin se rapproche de la référence, mais les deux maillages
diffèrent encore de 0,187 mm en Z. Sur le maillage fin, la moyenne de von
Mises passe de 2,10 MPa côté appui à 0,37 MPa dans le dernier quart.
Le rapport présente cette variation sans en faire une borne d’erreur.
Les STL de forme libre, d’autres conditions d’appui, l’anisotropie FDM
et la tenue de pièces imprimées sont hors du domaine du coupon (#83, #84).

### Raffinement de la console à paramètres inchangés

La série ci-dessous utilise le même solveur et multiplie les divisions
initiales (24, 4, 5) par un facteur entier sur chaque axe. Les trois
derniers maillages ont été lancés hors du budget de la commande standard,
dans une étude locale dédiée. Le budget de la commande reste inchangé.

| Divisions X × Y × Z | Tétraèdres | Flèche en Z (valeur absolue) | Variation depuis le niveau précédent |
| --- | ---: | ---: | ---: |
| 24 × 4 × 5 | 2 880 | 0,835141 mm | — |
| 48 × 8 × 10 | 23 040 | 1,021842 mm | 0,186701 mm |
| 72 × 12 × 15 | 77 760 | 1,068153 mm | 0,046311 mm |
| 96 × 16 × 20 | 184 320 | 1,085738 mm | 0,017585 mm |
| 120 × 20 × 25 | 360 000 | 1,094195 mm | 0,008457 mm |

Le dernier incrément représente 0,77 % de la flèche finale. Le maillage
le plus fin donne une réaction verticale de 4 N et une contrainte moyenne
de 2,154 MPa dans le quart encastré, contre 2,147 MPa au niveau précédent.
Le résultat se stabilise numériquement sur cette série ; l’incrément
n’est pas une borne d’erreur. La référence de poutre (1,111111 mm) ne
résout pas le même problème tridimensionnel. Aucun seuil de densité ni
validation physique n’est déduit de ce résultat (#83).

### Des contraintes aux densités : une hypothèse à comparer

`modificateurs_depuis_contraintes(etude, seuils)` relit les quatre
moyennes axiales de von Mises du coupon sur chacun des maillages.
L’expérimentateur déclare les seuils en MPa et les densités en %.
Une bande ne reçoit une densité que si toutes ses valeurs restent
dans la même classe. Si une seule bande franchit un seuil entre
deux maillages, aucune tranche n’est fournie. Les bandes adjacentes
de même densité sont fusionnées. Les modificateurs restent bornés
au coupon et disjoints en volume ; aucun STL arbitraire n’est associé
au champ par ressemblance.

Pour ce coupon, les seuils 0 / 0,7 / 1,2 / 1,8 MPa associés à
20 / 35 / 45 / 60 % donnent les paliers ci-dessous. Un seuil alternatif
à 0,95 MPa est franchi dans la troisième bande (0,862 à 0,965 MPa) :
cette variante est refusée.

| Bande depuis l’appui | Moyennes de von Mises sur les cinq maillages | Densité d’essai | Axe du remplissage à 20 % partout | Avec paliers |
| --- | ---: | ---: | ---: | ---: |
| 0–15 mm | 1,921–2,154 MPa | 60 % | 220,8 mm | 667,1 mm |
| 15–30 mm | 1,426–1,579 MPa | 45 % | 233,2 mm | 527,1 mm |
| 30–45 mm | 0,862–0,965 MPa | 35 % | 237,4 mm | 405,1 mm |
| 45–60 mm | 0,333–0,380 MPa | 20 % | 227,3 mm | 227,3 mm |

Anycubic Slicer Next 2.0.0.3 a produit les mêmes mesures sur deux
tranchages hors ligne de la variante. Les mesures portent sur des
boîtes intérieures aux bandes, pour le rôle `Sparse infill`.
Les maillages et placements concordent entre variantes, et seuls
les trois réglages de densité attendus diffèrent avant et après
tranchage. Les 646 profils relus et la source sont intacts.
La densité nominale pondérée par le volume monte à 40 % : ce n’est
pas une masse mesurée ni une comparaison à quantité de matière égale.
Les seuils sont des valeurs d’essai. La rigidité avec ces paliers,
les contraintes locales dans le remplissage et le gain mécanique
restent NON VÉRIFIÉS (#83, #84).

Un second témoin reçoit 40 % dans chacune des quatre bandes, soit la
même densité nominale moyenne que la variante. En passant aux paliers,
les quatre boîtes gagnent respectivement 200,2 et 63,3 mm d’axe de dépôt
près de l’appui, puis perdent 60,3 et 232,0 mm vers le bout.
Le trancheur estime 2,50 g et 402 s pour le témoin uniforme, contre
2,55 g et 411 s avec les paliers. Les quatre différences de densité
sont les seules observées dans les réglages avant et après tranchage.
Cette comparaison établit la redistribution dans le G-code pour un
budget nominal égal ; les masses estimées diffèrent de 2 %. Elle ne
mesure pas un gain de rigidité à masse égale.
