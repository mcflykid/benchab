# Préparer une pièce CAO depuis une fiche JSON

Ce gabarit couvre un bloc rectangulaire ou une base cylindrique, avec jusqu’à
huit perçages traversants et un lamage supérieur facultatif par perçage. Jusqu’à
quatre poches rectangulaires, avec angles arrondis en option, peuvent aussi être déclarées au-dessus.
Les cotes sont en millimètres. L’origine est au centre en XY et le dessous
est à Z = 0 mm.

```json
{
  "schema": 1,
  "id": "plaque-deux-trous",
  "bloc_mm": [40, 20, 4],
  "percages": [
    {"x_mm": -10, "y_mm": 0, "diametre_mm": 4},
    {"x_mm": 10, "y_mm": 0, "diametre_mm": 4,
     "lamage": {"diametre_mm": 7, "profondeur_mm": 1.5, "source_cotes": "plan nominal de la pièce"}}
  ],
  "poches_rectangulaires": [
    {"x_mm": 0, "y_mm": 0, "largeur_x_mm": 6, "largeur_y_mm": 4,
     "profondeur_mm": 1, "source_cotes": "plan nominal de la pièce"}
  ],
  "hors_perimetre": ["Tenue sous charge"]
}
```

Pour une base cylindrique, remplacer `bloc_mm` par
`"base_cylindrique": {"diametre_mm": 30, "epaisseur_mm": 6}`. Une seule base
est permise. Les perçages, lamages et poches utilisent le même repère. La
marge d’un trou se mesure depuis son bord le plus éloigné du centre et
celle d’une poche depuis le point le plus éloigné de son contour. Cela évite d’accepter
une opération située dans la boîte englobante mais hors du disque.

Les dimensions et diamètres sont compris entre 0,1 et 1 000 mm. Les centres
sont déclarés dans le même repère XY. Le gabarit refuse les trous tangents ou
recouvrants et ceux qui atteignent le bord du bloc ; la marge de calcul de
0,000001 mm écarte la tangence numérique. Elle ne représente pas une épaisseur
admissible pour l’impression. Les cotes sont explicites et aucun jeu ou
compensation n’est ajouté. Les formes hors gabarit, les assemblages et les
exigences physiques peuvent être signalés dans `hors_perimetre`.

Le lamage part du dessus du bloc. Son diamètre dépasse celui du passage,
sa profondeur laisse un fond autour du passage et les contrôles de collision
utilisent son diamètre d’entrée. La fiche doit citer la source des cotes ;
elle ne déduit aucune cote de visserie. Le volume analytique retire le
passage traversant, puis seulement l’anneau du lamage (#27, #32).

Une poche borgne commence sur la face supérieure, a un fond plat et reste
alignée sur les axes XY. Les deux largeurs définissent le rectangle centré en
`x_mm`/`y_mm`. Le gabarit refuse une profondeur borgne qui atteint
le dessous. Pour une ouverture traversante, remplacer `profondeur_mm`
par `"traversante": true` ; le plan indique alors l’épaisseur retirée.
Les poches et ouvertures tangentes ou recouvrantes sont refusées, entre
elles et avec le diamètre d’entrée d’un perçage. Pour des coins droits, le volume retiré vaut largeur X × largeur Y
× profondeur. Une poche peut déclarer `rayon_angle_mm`, compris entre
0,1 mm et strictement moins que chacune de ses demi-largeurs. Son contour
est celui du noyau rectangulaire agrandi d’un disque : les distances aux
autres opérations et au bord d’une base ronde tiennent compte des coins
retirés. Le volume diminue de `(4 − π) × rayon² × profondeur` par rapport
à la poche à coins droits de mêmes largeurs. Le moteur construit l’outil
avec [le rectangle arrondi de build123d](https://build123d.readthedocs.io/en/stable/build_sketch.html)
et l’extrude jusqu’au fond déclaré. Orientations, chanfreins et parois
physiques ne sont pas déduits (#32).

Une poche peut aussi déclarer `"oblongue": true` à la place de
`rayon_angle_mm`. Les deux largeurs doivent être distinctes ; la plus
longue donne l’axe et la longueur totale, la plus courte le diamètre des
deux extrémités semicirculaires. Le plan indique leur rayon dérivé.
La forme est construite par `SlotOverall` et peut être borgne ou
traversante. Les marges et collisions sont calculées sur le contour
oblong, y compris pour un trou proche d’une extrémité ; aucun jeu
d’impression n’est ajouté.

```sh
benchab conception fiche-cao -- prive/fiche.json --sortie prive/plan-neuf
benchab conception cad-engine -- build prive/plan-neuf/modele.py --output-dir prive/construction-neuve
```

La première commande publie ensemble `plan.json` et `modele.py` dans un
dossier neuf. À fiche équivalente, la mise en forme du JSON et l’ordre de ses
clés ne changent pas leurs octets. Le plan contient le SHA-256 du script,
les dimensions et le volume analytique attendus. Seules les cotes numériques
validées entrent dans le gabarit Python. La génération ne lance pas le moteur.

La sortie courte fournit les chemins ; ajouter `--detail` pour afficher le
plan. Le code 0 indique une fiche couverte par le gabarit, le code 2 garde
la génération et signale les exigences non vérifiées, le code 1 signale un
refus. Une nouvelle destination est requise pour chaque génération.

La deuxième commande construit le STEP avec le moteur verrouillé. Son
manifeste permet de comparer nombre de solides, dimensions et volume au plan.
Cette comparaison ne tranche rien et ne qualifie ni les jeux, ni les matériaux,
ni le comportement de la pièce imprimée. Le gabarit vérifie les cotes de la
fiche ; il ne détecte pas une demande libre omise. Inscrire les exigences
non encodées dans `hors_perimetre` pour les garder NON VÉRIFIÉES.

La recette du lamage a construit deux fois cinq fiches : lamage central,
excentré, mixte avec un perçage simple, deux lamages, puis quatre trous
dont trois lamés. Avec cadgen 0.8.4 et build123d 0.13.0, les dix STEP
forment chacun un seul solide valide ; volumes et dimensions concordent
avec le calcul nominal. Plans et scripts sont identiques à chaque rejeu.
Sur cette exécution locale, la préparation a pris 2,7 ms en médiane et la
construction 26 ms (premier démarrage : 2,14 s). Les chemins normalisés
pour la mesure, le résumé occupe 74 octets et le détail 848 à 1 427 octets.

La recette des poches couvre cinq autres fiches, de la poche seule à quatre
poches et deux lamages, en passant par le coin dégagé près d’un perçage.
Les dix constructions ont donné un seul solide valide par STEP, aux
dimensions et volumes attendus. Plans et scripts concordent exactement
entre les deux répétitions. La préparation a pris 3,0 ms en médiane et la
construction 33 ms sur cette exécution (premier démarrage : 1,59 s). Le
résumé normalisé occupe 74 octets et le détail 824 à 1 908 octets.

La recette de la base cylindrique couvre cinq autres fiches : disque plein,
alésage central, lamage excentré, poche et combinaison de deux lamages et deux
poches. Les dix STEP relus ont chacun un solide valide, avec dimensions et
volumes analytiques conformes et plans/scripts identiques au rejeu. Les cinq
fiches rectangulaires avec poches conservent leurs plans et scripts historiques.
Préparation : 3,0 ms en médiane ; construction : 23 ms (premier démarrage :
2,08 s). Le résumé normalisé occupe 74 octets et le détail 640 à 1 532 octets.

### Fraisage conique d’un perçage

Une entrée `fraisage` peut remplacer `lamage` sur un perçage :

```json
{"diametre_mm": 9, "angle_inclus_deg": 90, "source_cotes": "plan coté"}
```

Le diamètre est celui de l’ouverture supérieure. L’angle inclus est mesuré
entre les deux génératrices, de 30 à 150 degrés. La profondeur est calculée
depuis la différence des rayons et le demi-angle ; le plan la conserve en
mm. Le diamètre supérieur sert aux contrôles de tangence avec les bords,
les autres perçages et les poches. Un passage débouchant reste cylindrique
sous le fraisage ; un fraisage qui atteint le dessous est refusé. Aucune
cote de visserie, tolérance ou conformité ISO n’est déduite.

Cinq fiches synthétiques ont été construites deux fois avec cadgen 0.8.4
et build123d 0.13.0 : trois angles sur bases rectangulaire ou circulaire,
un cas mixte avec lamage et poche, puis deux entrées distinctes. Les dix
STEP ont un solide valide, avec dimensions et volume conformes au calcul
indépendant. Les plans et scripts sont identiques entre répétitions ; les
cinq fiches cylindriques historiques gardent aussi leurs octets. Il s’agit
d’une vérification numérique, sans ajustement imprimé établi (#27, #32).

La recette des angles arrondis a construit deux fois trois fiches :
un coin dégagé près d’un perçage, une poche au bord d’une base ronde et
deux poches dont les encombrements se chevauchent. Avec cadgen 0.8.4 et
build123d 0.13.0, chacun des six STEP relus contient un solide valide
aux dimensions et au volume attendus. Plans et scripts se reproduisent
octet pour octet. Cinq fiches antérieures sans arrondi gardent les mêmes
plans et scripts. Sur cette exécution locale, la préparation a pris
2,0 ms en médiane et la construction 42,6 ms.

### Recette des ouvertures traversantes

Cinq fiches ont été construites deux fois : rectangle, coins arrondis,
base ronde, ouverture avec poche borgne et ouverture avec perçage.
Les dix STEP relus contiennent chacun un solide valide aux cotes et au
volume attendus. Des points de contrôle au centre de l’ouverture, près
des deux faces et à mi-hauteur, confirment l’absence de matière. Dans le
cas mixte, la poche borgne garde son fond. Huit anciennes fiches gardent
leurs plans et scripts à l’octet près. Préparation médiane : 2,1 ms ;
construction médiane : 35,9 ms sur le poste de recette. Aucun ajustement
ni comportement de la pièce imprimée n’est vérifié (#32).

### Rejeu du gabarit et périmètre validé

Cinq fiches de référence, du bloc plein au bloc à quatre perçages, ont
été reconstruites deux fois. Les dix STEP relus contiennent chacun un
solide valide ; les dimensions et volumes concordent avec le calcul
analytique. Pour chaque paire, le plan et le script gardent les mêmes
empreintes malgré le changement de présentation du JSON. Sur le poste
de rejeu, la préparation médiane a pris 3,8 ms et la construction
26,7 ms. À chemins normalisés, le résumé occupe 74 octets et les
rapports détaillés 591 à 973 octets.

Ce périmètre couvre les bases, perçages, lamages, fraisages et poches
décrits par le gabarit. Les exigences déclarées dans `hors_perimetre`
restent NON VÉRIFIÉES et les opérations inconnues sont refusées (#32).
