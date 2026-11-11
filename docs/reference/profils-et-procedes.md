# Profils et procédés : choisir à partir d’une réussite

Un profil définit un candidat de fabrication, pas une calibration universelle.
Distinguer machine, procédé, filament, surcharges d’objet et changements faits
pendant l’impression. Un profil présent sur disque n’est pas nécessairement
sélectionné dans l’interface ni affecté à la bobine physique.

## Deux réussites SUNLU HS à plafond 32 mm³/s

Observé par l’auteur sur sa Kobra S1 Combo : deux impressions déclarées parfaites
le 17/10/2026, l’une pour une fixation de mobilier, l’autre pour une bride de
câbles. SUNLU High Speed PLA Marble vert forêt ; 220 °C première couche puis
240 °C, PEI 55 °C, flow 0,98 et plafond volumique 32 mm³/s.

La première préparation : couche 0,24 mm, trois parois de 0,50 mm, remplissage
15 %, sans supports générés. La seconde : bride en 0,24 mm avec supports,
vis en 0,20 mm, quatre parois, 25 % et contours ralentis. Les temps de 1 h 26
et 1 h 20 sont des estimations, pas des chronométrages.

Les fichiers exécutés n’ont pas été récupérés pour ces deux impressions ; les
relevés décrivent les candidates retenues. Statut : deux observations d’usage,
pas mesure de débit maximal, vitesse soutenue ni résistance sous charge.

## Réussites à plafond 12 mm³/s

Des fichiers exécutés retrouvés pour d’autres pièces SUNLU utilisent 220 °C,
flow 0,98 et plafond 12 mm³/s. En couche 0,08 mm et ligne 0,42 mm, ce plafond
ne bride pas comme en couche 0,24 mm : vitesse et section doivent être lues
ensemble. Certains retours mentionnent supports difficiles et défauts locaux ;
« satisfait » ne veut pas dire sans défaut.

Ne pas revenir automatiquement au profil prudent à 12, ni copier 32 avec une
température différente. Les réussites ne dispensent pas des contrôles.
Voir les [fiches matière](filaments.md).
Les signalements du slicer vus en ligne sont conservés comme hypothèses
contextualisées dans le [registre communautaire](retours-communautaires.md) ;
ils ne remplacent pas les contrôles locaux.

## Réussite contextuelle avec vitesses relevées et mode Sport

Une plaque de trois petites pièces en PLA Basic noir Anycubic a été déclarée
parfaite le 08/11/2026, en 170 min, avec le mode Sport de la couche 6 à la fin.
Il s’agit d’une impression de plaque, pas de trois essais indépendants.

Les préparations conservées ont été relues le 10/11 : couche 0,20 mm, buse
0,4 mm déclarée, températures 215 °C au démarrage puis 205 °C. Par rapport à la
préparation initiale, les vitesses déclarées ont été multipliées par 1,3 et le
plafond de débit est passé de 12 à 15,6 mm³/s. Le rapport Sport/Normal du profil
est de 780/600 ; l’hypothèse d’un multiplicateur uniforme de 1,3 donnerait un
scénario de 20,28 mm³/s. Ce chiffre n’est ni un débit physique mesuré ni une
vitesse effectivement atteinte : le G-code exécuté n’a pas été retrouvé.

L’apprentissage porte sur la réussite visuelle déclarée dans cette combinaison
de pièce, matière, préparation et intervention en cours d’impression. La tenue,
l’endurance, la masse consommée et le débit maximal restent non mesurés.
Une autre préparation portant directement 20,28 mm³/s n’est pas validée par
ce seul essai.

Conserver ce retour comme piste de comparaison pour des conditions proches,
avec ses fichiers et observations rattachés dans l’atelier privé. Ne pas revenir
automatiquement à 12 mm³/s, ni transférer automatiquement 20,28 à une nouvelle
pièce. Le plafond d’une nouvelle préparation reste explicite et distinct de
l’observation ; U25 compare le G-code à ce plafond déclaré.

## Préserver les réglages utiles

Les surcharges par objet, couches variables, pauses, parois utiles et filetages
font partie du projet. Le suffixe `_HS` de certains champs de température
signifie buse acier trempé dans ce contexte, pas « High Speed » du filament.
Identifier la buse réelle et contrôler les commandes effectivement générées.

Reprendre un profil sauvegardé explicite plutôt que modifier le profil installé.
Déclarer chaque changement, comparer après tranchage durée estimée, matière,
trajets et zones critiques. Conserver la candidate exacte et sa provenance.
Une variation bénéfique de masse ou de temps ne prouve pas sa résistance.

## Optimisation interne et refroidissement

Une cavité à toit de 45° dans un bloc 80 × 40 × 24 mm, trois parois, couche
0,2 mm et gyroïde a coûté 11,3 % de masse à 15 % de remplissage mais économisé
5,0 % à 40 % dans une comparaison numérique du 25/10/2026. Un seul protocole,
aucune validation de rigidité ; ses seuils interpolés ne sont pas universels.

Un plancher de vitesse de 2 mm/s et une cible de 15 s ont donné des résultats
différents selon matériau et sommet : réussite Glow à 190 °C, écailles sur une
petite surface arrondie en eSUN à 220 °C. Cause non isolée. Examiner la chaleur
accumulée et les trajectoires plutôt que généraliser le réglage.

## Sources

Relevés historiques SUNLU du 17/10, fiches filaments et étude numérique du
25/10/2026 : [faits et limites](../../benchab/reference/connaissances.jsonl),
[provenance par empreinte](../../benchab/reference/sources-savoir.jsonl).
Le [cycle de projet](../guides/cycle-projet.md) décrit leur réutilisation.
