# Témoin de gyroïde modélisé

`benchab conception gyroide-temoin` construit un solide d’étude dans une
enveloppe rectangulaire. Il ne transforme aucune pièce existante. La commande
écrit le STL et le rapport dans un dossier privé neuf dont le parent existe ;
elle ne lit pas la base et ne lance ni tranchage ni impression.

```sh
benchab conception gyroide-temoin \
  --dimensions-mm 24 16 16 --paroi-mm 2 --cellule-mm 8 \
  --seuil 0.55 --pas-mm 0.35 --protection-mm 10 6 4 14 10 12 \
  --sortie prive/rapports/coupon-gyroide
```

Chaque protection, facultative et répétable, donne les coins opposés d’une
boîte pleine à conserver dans le repère XYZ du coupon. Le champ continu
emploie la somme `sin(x)cos(y) + sin(y)cos(z) + sin(z)cos(x)` dans des
coordonnées ramenées à la période demandée. Le seuil sans unité délimite
la bande de part et d’autre de zéro. Le [noyau Manifold](https://manifoldcad.org/docs/html/)
maille la bande ; une intersection avec des cotes exactes la confine
et lui donne un recouvrement avec la peau.

Le rapport enregistre les paramètres, les volumes en mm³, les protections,
le nombre de triangles et le SHA-256 du STL. PASS signifie que le moteur
trouve un seul corps positif au-dessus de la tolérance volumique,
sans perte de zone protégée ni débord au-dessus de cette tolérance, et
que le STL relu est fermé avec un volume cohérent. La tolérance vaut le
maximum entre 0,000001 mm³ et un milliardième du volume du bloc plein.
Le diamètre analytique s’applique à une boule centrée sur la surface
médiane du champ continu infini. Il ne mesure ni l’épaisseur du STL ni
celle des trajectoires extrudées. Le pas demandé est nominal ; il ne
remplace pas une mesure de résolution locale.

Un essai comparatif peut partir de trois variantes de même enveloppe :
bloc à remplissage natif, peau et protections seules, puis géométrie
modélisée avec un remplissage clairsemé à 0 %. Conserver le binaire du
slicer, les réglages embarqués, les avertissements et deux rejeux des mêmes
octets. La comparaison porte sur des géométries et réglages déclarés. Les
durées et grammes du slicer sont des estimations. La tenue physique reste
NON VÉRIFIÉE (#87, #84).
