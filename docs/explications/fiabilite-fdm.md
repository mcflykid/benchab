# Fiabilité FDM : jeux, trous et mécanismes

Les dimensions CAO ne sont pas les dimensions imprimées. Distinguer contrôle
géométrique, trajectoire tranchée et ajustement réel. Les observations suivantes
proviennent de l’auteur sur sa Kobra S1 Combo, en octobre 2026 ; elles n’imposent
pas une tolérance universelle.

## U32 : jeux mesurés

Un essai du 26/10/2026 en eSUN PLA+ blanc os à 220 °C, trous verticaux proches
de 10 mm : 0,2 mm par côté serré, 0,3 mm glissant, au moins 0,35 mm avec jeu
visible. Une seule éprouvette, pas de mesure métrologique de tous les diamètres.
Un jeu par côté de 0,3 mm correspond à une différence de diamètre de 0,6 mm.
Toujours préciser cette convention pour éviter de doubler ou diviser la valeur.

Fentes de 0,8 mm refermées et emboîtements à 0,2–0,3 mm trop justes ont été
observés sur des mécanismes. Les valeurs dépendent de l’orientation, de la
première couche, du matériau et de la géométrie. Selon U30, une valeur non
mesurée reste marquée comme telle ; une marge prudente n’est pas une mesure.

## Trous, filetages et ordre de montage

Relire la géométrie exportée et le G-code : une ouverture présente dans la CAO
peut être refermée par le tranchage. Examiner diamètre, épaisseur de paroi,
chanfrein d’entrée, pied d’éléphant et accès à l’outil de montage. Un filetage
vertical limite certains surplombs mais ne garantit ni vissage ni résistance.

Simuler insertion pièce par pièce dans l’ordre réel, et en hélice pour un
vissage. Vérifier le passage des têtes de vis durant le mouvement, pas seulement
à la position fermée. L’écrou doit serrer les pièces fixes sans brider la pièce
mobile ; un méplat ou une forme en D peut empêcher l’axe de tourner.

## U24 : le mouvement doit être entraîné

Aligner deux évidements autorise un déplacement sans le provoquer. Nommer
l’élément moteur et vérifier le contact dans les deux sens. Une animation
solidaire de toute la chaîne ne démontre pas l’entraînement : placer chaque
élément au retard permis par son jeu, puis tester le cycle complet.

Un flanc presque radial à environ 79° a bloqué un rotor dans un retour réel.
Le critère théorique d’arc-boutement dépend du frottement : avec μ = 0,4,
90° − arctan(μ) donne environ 68°. Ce coefficient n’a pas été mesuré ici.
Les contacts proches de 45° sont une piste de conception, pas une garantie.
Un mécanisme dépendant d’un engagement de quelques dixièmes est fragile en FDM ;
préférer un entraînement positif et des engagements de plusieurs millimètres.

## Géométrie et résistance

Vérifier continuité de contact et course sur toute la longueur, pas une coupe
unique. Additionner les jeux et leur effet de levier sur les pièces éloignées.
Réassemblage numérique sans collision ne prouve ni frottement acceptable ni
solidité. Les connecteurs du [module de découpe](../guides/decoupe.md)
restent à valider physiquement pour chaque usage et matière.

## Sources

Éprouvette du 26/10/2026 et retours de mécanismes des 21–22/10 ; distinction
entre théorie, essais physiques et vérifications numériques dans
[le corpus](../../benchab/reference/connaissances.jsonl) et
[ses sources anonymisées](../../benchab/reference/sources-savoir.jsonl).
