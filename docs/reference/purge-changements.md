# Changements d’outil et purge conditionnelle

`benchab controle purge-changements FICHIER.gcode.3mf` compte les transitions
`Tn` explicites plaque par plaque. Une première sélection initialise la lecture ;
une sélection répétée du même outil ne crée pas de changement. Un indice hors
des slots déclarés reste visible et rend le total indéterminé.

La matrice projet est interprétée uniquement si les listes de profils et de
couleurs donnent le même nombre de slots et si la matrice a exactement cette
taille au carré. Les valeurs doivent être finies, positives ou nulles, et
la diagonale nulle. La lecture ne change pas le type historique dans le
dictionnaire et n’autorise aucune réécriture du réglage.

La convention conditionnelle vient du
[code source épinglé d’Anycubic Slicer Next](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/OrcaSlicer.cpp#L2607-L2660).
Les lignes correspondent à l’outil source, les colonnes à l’outil destination.
Cette convention documentaire ne prouve pas la lecture par un binaire
particulier. Un témoin à un seul outil avec neuf valeurs ne qualifie aucune
paire, même si le tableau rappelle une matrice de trois outils.

Le rapport distingue le comptage, les valeurs par paire et leur somme
conditionnelle en mm³. Le statut PASS couvre seulement ce calcul conditionnel.
Le volume attribuable aux commandes de purge, le temps réel, la densité,
la masse et les effets des commandes du firmware restent NON VÉRIFIÉS.
Aucun plafond, multiplicateur ou réglage machine n’est ajouté.
