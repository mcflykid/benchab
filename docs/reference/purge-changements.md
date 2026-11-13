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

`--resume --sortie RAPPORT.json` affiche le statut, les compteurs et le chemin
du rapport détaillé. `--sortie` seule conserve aussi l’affichage détaillé.
Le JSON est neuf et privé ; aucun rapport existant n’est remplacé. Un total
indéterminé conserve son code 2 et ses détails (#35).

`--observer-tour` ajoute, par plaque, l’observation des mouvements déposés
de rôle `Prime tower`. Le
[code source ASN épinglé](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/ExtrusionEntity.cpp#L518-L539)
associe ce rôle à la tour. BENCHAB ne généralise pas à `Custom`, au démarrage,
au remplissage ou à l’objet. Le volume nominal en mm³ utilise le diamètre
déclaré pour l’outil exact, sans repli ; le remboursement de rétraction et
l’extrusion immobile sont exclus. Sans mesure exploitable ou si la lecture est
incomplète, le total de cette observation reste NON VÉRIFIÉ ; les segments
couverts restent détaillés par outil. Le code du bilan reste celui de la
matrice. Cette observation ne mesure ni purge physique, ni dépôt par paire,
ni effet d’un changement de réglage (#72).

L’observation de la tour indique aussi le temps nominal de dépôt à l’avance
programmée, en secondes. La masse nominale en grammes utilise la densité
embarquée de l’outil exact. Une densité manquante laisse la masse totale
NON VÉRIFIÉE et conserve le temps et le volume couverts. Le temps de changement
complet, les déplacements hors dépôt et le temps réel restent séparés.
