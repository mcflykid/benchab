# Préparer une pièce CAO depuis une fiche JSON

Ce gabarit couvre un bloc rectangulaire et jusqu’à huit perçages cylindriques
traversants, sans déplacement ni rotation du bloc. Les trois dimensions sont
en millimètres. L’origine est au centre en XY et le dessous est à Z = 0 mm.

```json
{
  "schema": 1,
  "id": "plaque-deux-trous",
  "bloc_mm": [40, 20, 4],
  "percages": [
    {"x_mm": -10, "y_mm": 0, "diametre_mm": 4},
    {"x_mm": 10, "y_mm": 0, "diametre_mm": 4}
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
