# Préparer une pièce CAO depuis une fiche JSON

Ce gabarit couvre un bloc rectangulaire et jusqu’à huit perçages cylindriques
traversants, avec un lamage supérieur facultatif par perçage. Jusqu’à
quatre poches rectangulaires peuvent aussi être déclarées au-dessus.
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

Une poche commence sur la face supérieure, a un fond plat et reste alignée
sur les axes XY. Les deux largeurs définissent le rectangle centré en
`x_mm`/`y_mm`. Le gabarit refuse les fonds débouchants et les poches
tangentes ou recouvrantes, entre elles ou avec le diamètre d’entrée d’un
perçage. Le volume retiré vaut largeur X × largeur Y × profondeur. Les
rayons d’angle, orientations, chanfreins et parois physiques ne sont pas
déduits (#32).

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
ni le comportement de la pièce imprimée.

La recette du lamage a construit deux fois cinq fiches : lamage central,
excentré, mixte avec un perçage simple, deux lamages, puis quatre trous
dont trois lamés. Avec cadgen 0.8.4 et build123d 0.13.0, les dix STEP
forment chacun un seul solide valide ; volumes et dimensions concordent
avec le calcul nominal. Plans et scripts sont identiques à chaque rejeu.
Sur cette exécution locale, la préparation a pris 2,7 ms en médiane et la
construction 26 ms (premier démarrage : 2,14 s). Les chemins normalisés
pour la mesure, le résumé occupe 74 octets et le détail 848 à 1 427 octets.
