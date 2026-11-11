# Continuité de la paroi extérieure

Le plan modal G17/G18/G19 est conservé entre les lignes. Seuls les arcs en
G17 XY sont mesurés ; un arc dans un autre plan est refusé sans modifier la
position, l’avance ou la dette de rétraction. Un retour explicite à G17 est
reconnu. Le champ `deposition_start` vaut `None` pour un arc : sa position
géométrique de début de dépôt n’est pas encore exposée, même si sa longueur
déposée et son débit sont calculés. Les consommateurs linéaires le refusent
avant de lire ce champ (suite #123).

La commande accepte `--sortie prive/rapports/continuite.json` pour un export
JSON neuf, identique au rapport affiché, avec chemin source, plaque et SHA-256.
Une destination existante, une entrée ou une préparation figée ne sont jamais
écrasées. `--bandes-z 5` ajoute une synthèse par bandes de 5 mm sans retirer
les mesures par couche ni modifier le verdict. Les bandes couvrent la fenêtre Z
explicite, sinon les hauteurs mesurées : `[min, max)`, dernière borne haute
incluse. La dernière bande partielle et les bandes vides sont conservées ;
une bande vide reste NON VÉRIFIÉE. Le budget est limité à 10 000 bandes.

Ce contrôle mesure les trajectoires d’extrusion marquées `Outer wall` par le slicer. Il ne prédit pas la qualité physique.

Pour chaque hauteur de couche, le rapport compte les morceaux continus, les segments extrudés et les segments de moins
de 0,3 mm. Un déplacement sans dépôt, une rétraction ou un changement de rôle rompt la continuité. Les positions,
l’extrusion absolue ou relative et les remises à zéro de l’extrudeur sont interprétées avant le comptage.
Le lecteur commun suit l’axe E et sa dette de rétraction ; un changement d’outil coupe la continuité sans effacer
cette dette. Tn est une identité numérique, sans limitation artificielle à T0–T3 et sans qualification du routage matériel.
Les mouvements linéaires compacts valides et les alias G00/G01 sont mesurés. Le dialecte d’arc explicitement reconnu
est limité à G17 XY, I/J relatifs au point de départ ou R signé, avec sens horaire G2 et antihoraire G3 ; les cercles
complets exigent I/J. La longueur XY est calculée par rayon et angle, sans segments polygonaux ; une hélice Z ajoute
sa course par la longueur euclidienne exacte. La durée nominale vaut longueur / F lorsque F est connu. Cette mesure
géométrique ne valide ni l’interpolation du micrologiciel ni le trajet physique. G18/G19, I/J absolus, tours P,
paramètres supplémentaires et géométries incohérentes restent `NON VÉRIFIÉ` et ne modifient pas l’état.
Cette grammaire suit les arcs G17 à centres relatifs décrits par la documentation primaire
[LinuxCNC G-code](https://www.linuxcnc.org/docs/html/gcode/g-code.html) ; elle ne revendique pas une compatibilité
avec les autres firmwares.

G92 XYZ redéfinit les coordonnées de commande sans déplacer le repère spatial continu. Dans l’API du lecteur, `xyz`
garde les coordonnées commandées et `spatial_xyz` garde la trajectoire continue ; `start`, `end`, `spatial_start` et
`spatial_end` décrivent cette dernière en mm. Les couches et la continuité sont calculées dans ce repère ; E et sa
dette ne sont pas remis à zéro par G92 XYZ. Un G92 E ne change que le repère de l’extrudeur. Un format invalide
reste `NON VÉRIFIÉ`.

Les mesures analytiques d’arc servent aux calculs de longueur et de débit. La continuité de paroi et le contrôle de
première couche exigent une représentation de trajectoire qui sache suivre une courbe ; tant qu’ils ne consomment
pas ce modèle, tout arc valide y rend le verdict `NON VÉRIFIÉ`. Aucun arc n’est remplacé par sa corde dans ces contrôles.

Le contrôle des capacités-machine calcule aussi l’enveloppe XYZ analytique de la portion déposée d’un arc G17 pour
le verdict local `volume XYZ déposé`. Il inclut les extrema angulaires réellement parcourus et les deux extrémités Z
d’une hélice ; le début est décalé selon la fraction de dépôt après rétraction. Les dépassements du volume déclaré
au-delà de la tolérance donnent FAIL. Une tolérance numérique de 1e-6 mm est appliquée à ces bornes ; les coordonnées
ou l’enveloppe hors de ±1 000 000 mm restent NON VÉRIFIÉES. La corde n’est jamais utilisée et le rayon déclaré par
l’interpréteur n’est pas ajusté. Les vitesses et accélérations par axe d’un arc restent NON VÉRIFIÉES ; cette
enveloppe numérique ne confirme ni la trajectoire firmware, ni la géométrie utile réelle, ni la tenue physique.

La revue des zones locales utilise l’enveloppe du dépôt G17 pour les ponts et
parois en surplomb explicitement déclarés, en repère physique après G92. Elle
ne mesure ni la portée libre ni le contact. Les arcs sans couche ou attribution
d’objet vérifiée restent bloquants, comme les arcs d’autres rôles pour ce
contrôle. Une mesure locale de cordons qui ne traite que G0/G1 ne valide pas
ces arcs (suite de #123).

Le rapport distingue l’accélération extérieure configurée de l’accélération `M204` observée pendant les dépôts.
Une commande de mouvement non prise en charge rend la couverture incomplète : le résultat devient `NON VÉRIFIÉ`.

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


## Mesures de dépôts dans une région 3D

La mesure de remplissage localisé peut utiliser les arcs G17 XY qualifiés
(#123). Elle calcule les intersections avec les six faces d’une boîte en mm
sur la trajectoire analytique : angles pour X/Y et fraction de hauteur pour Z
dans une hélice. La dette de rétraction déplace le début du dépôt le long de la
courbe. Le repère physique reste distinct du repère de commande après G92.
Les longueurs sont arrondies à 0,1 mm ; aucune corde n’est comptée. Les
bornes, extrémités et enveloppes circulaires au-delà de ±1 000 000 mm sont refusées.

Cette extension concerne les mesures par boîte. Les contrôles de couverture
première couche, de continuité et d’appuis qui attendent encore des segments
restent NON VÉRIFIÉS pour les arcs. Les autres plans et centres absolus ne sont
pas qualifiés. La mesure est numérique et ne valide pas une impression.
