# Découpe intelligente en version 1.0

La décision du 4 novembre ajoute un lot 6B entre les outils avancés et la
documentation. Le module part de la découpe existante et conserve ses API dans
un paquet `benchab.conception.decoupe`.

Les fonctions retenues s'inspirent d'idées évoquées à propos de l'addon Cleave.
L'implémentation et les textes sont indépendants. Les calculs utilisent les
bibliothèques ouvertes de l'atelier ; aucun code ni texte de cet addon n'est repris.

La forme et les difficultés d'impression déclenchent une comparaison entre
pièce entière et variante découpée. Les propositions, coutures éditées,
connecteurs, dimensions, jeux et contrôles sont enregistrés dans le projet privé.
Le choix final appartient à l'utilisateur. Une géométrie ambiguë est signalée.

Le catalogue comporte cheville (rectangle, rond, D, queue d'aronde), rotule
simple ou double, douille avec goupille séparée, canal pour tige et coupe sans
connecteur. Jeux et parois sont contrôlés numériquement. Les valeurs non mesurées
restent identifiées ; une rotule ne reçoit aucune validation physique implicite.

Les tests publics emploient des pièces générées par l'atelier. Les essais sur
projets réels restent privés. Le parcours d'édition, de réassemblage et d'export
est testé, puis le lot 8 rejoue une découpe complète sur une pièce réelle.
Les critères antérieurs restent en place.

La validation physique des connecteurs et la mesure des jeux sont réservées à
une vraie pièce ultérieure ; aucune impression n'est lancée pendant le chantier.
