# Continuité de la paroi extérieure

Ce contrôle mesure les trajectoires d’extrusion marquées `Outer wall` par le slicer. Il ne prédit pas la qualité physique.

Pour chaque hauteur de couche, le rapport compte les morceaux continus, les segments extrudés et les segments de moins
de 0,3 mm. Un déplacement sans dépôt, une rétraction ou un changement de rôle rompt la continuité. Les positions,
l’extrusion absolue ou relative et les remises à zéro de l’extrudeur sont interprétées avant le comptage.
Le lecteur commun suit l’axe E et sa dette de rétraction ; un changement d’outil coupe la continuité sans effacer
cette dette. Tn est une identité numérique, sans limitation artificielle à T0–T3 et sans qualification du routage matériel.
Les mouvements linéaires compacts valides et les alias G00/G01 sont mesurés. Un format invalide reste `NON VÉRIFIÉ`.

Le rapport distingue l’accélération extérieure configurée de l’accélération `M204` observée pendant les dépôts.
Une commande de mouvement non prise en charge rend la couverture incomplète : le résultat devient `NON VÉRIFIÉ`.
Les arcs G2/G3 restent explicitement non mesurables dans ce parcours. Une remise à zéro G92 XYZ ne qualifie pas
le nouveau repère de mesure des couches : elle conserve une couverture incomplète.

Les seuils absolus par défaut déclenchent un `WARN` au-delà de 10 morceaux par couche, de 30 % de microsegments ou de
4 000 mm/s². Ce sont des heuristiques de triage configurables, sans valeur de norme constructeur. Un dépassement
appelle une inspection des couches et des réglages ; une impression réussie peut aussi dépasser ces seuils dans
des zones détaillées ou très fragmentées.

L’analyse couvre toute la hauteur par défaut. Une fenêtre Z est possible, mais ses bornes figurent dans le rapport :
un résultat dans cette fenêtre ne vaut pas pour le reste de la pièce. Un 3MF à plusieurs plaques exige une sélection
explicite de plaque pour une analyse isolée.

Une référence doit déclarer sa relation : même source de modèle, seulement même machine et même filament, ou relation
inconnue. Seule la première autorise une comparaison de géométrie. Une différence de trajectoires entre deux modèles
distincts ne démontre pas une cause de défaut ; leur maillage peut contribuer à cette différence.

Le lecteur travaille en flux. Les rapports sont mis en cache par empreinte SHA-256 du 3MF complet, membre G-code,
options et version du lecteur. Modifier les seuils, la fenêtre ou le fichier invalide ce résultat. Le cache reste
borné et les réponses retournées ne permettent pas de modifier les résultats conservés.
Les mutations et comptes d’octets sont protégés contre les appels concurrents. Une même clé insérée deux fois
ne réserve sa taille qu’une fois ; les lectures et analyses s’exécutent hors du verrou de cache.
La continuité conserve au plus 128 diagnostics détaillés du lecteur, puis un message de dépassement explicite.
