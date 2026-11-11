# Couches courtes et îlots issus des sections

`benchab controle couches-courtes` prélève des sections XY du maillage STL à
l’intérieur d’intervalles Z de hauteur `lh` (mm). Les rapports existants
gardent leurs champs `centre_xy_mm`, `de_z`, `a_z`, `hauteur_mm`, les aires,
`motifs` et `sommet_terminal`.

Le champ `qualification_sections` ajoute des observations numériques des
sections, sans modifier ni préparer le modèle :

- `premiere_relation_appui_xy = premier_echantillon_du_maillage` signifie que
  la piste est présente au premier prélèvement près du Z minimal du STL.
  `candidat_pose_au_z_min_du_maillage` est seulement un candidat : une pièce
  flottante dans le premier intervalle Z peut donner le même échantillon.
  `contact_plateau_prouve` reste toujours faux.
- `premiere_relation_appui_xy` et `relations_appui_xy_observees` distinguent
  `sans_recouvrement_xy_echantillonne`, `recouvrement_partiel_xy_echantillonne`
  et `recouvrement_complet_xy_echantillonne`. La fraction est l’aire de la
  section courante intersectée par l’union des polygones du prélèvement Z
  précédent, divisée par son aire.
- `branche_projection_xy` signale qu’un polygone précédent recouvre plus d’un
  polygone courant ; `fusion_projection_xy` signale qu’un polygone courant
  recouvre plusieurs polygones précédents. Ce sont des relations de coupes,
  pas une reconstruction de l’arbre solide ni une preuve de branche physique.
  `association_pistes_ambigue` reste vrai lorsque la relation ne permet pas une
  correspondance un-à-un ; `ambiguites` en expose la raison sans forcer une
  identité de piste. Une intersection sous le seuil numérique y est également
  signalée au lieu d’être rangée comme absence d’appui.
- `cavite_section_detectee` et `cavites_section_max` comptent les anneaux
  intérieurs des polygones 2D. Un anneau peut correspondre à un trou traversant ;
  il ne prouve pas une cavité fermée en 3D.
- `sommet_terminal_echantillonne` reprend le critère existant : aucune section
  ultérieure ne recouvre la dernière section suivie. Cela ne prouve pas une
  pointe fragile ni un défaut physique.
- `pas_echantillonnage_z_mm`, `seuils_heuristiques` et
  `aire_intersection_numerique_min_mm2` gardent le pas, l’origine des options et
  le seuil de calcul utilisés. `aire_max_mm2` et `hauteur_min_mm` sont les
  paramètres de commande, sans validation indépendante ni signification
  physique universelle.

L’option CLI `--qualifier-position-enveloppes` ajoute, sans changer le rapport
historique par défaut, `qualification_sections.position_enveloppe_xy` :
`interieur_d_un_trou` exige que toute la section de l’îlot soit strictement
incluse dans l’anneau intérieur d’une autre section de la même coupe ;
`exterieur_aux_enveloppes` signifie qu’elle est strictement disjointe de toutes
les enveloppes de cette coupe. Une frontière touchée, un chevauchement matériel,
une géométrie invalide ou une classe qui change au fil des couches donne
`NON VÉRIFIÉ`. La valeur décrit une position XY échantillonnée par couche, pas
une cavité fermée en 3D, un appui, ni une propriété du tranchage.

Les intersections sont retenues au-delà de `1e-9 mm²`, uniquement pour écarter
les résidus numériques de géométrie. Ce nombre n’est ni une taille minimale
d’appui ni un seuil de fabrication. Chaque qualification garde le statut
`NON VÉRIFIÉ`, car les coupes espacées peuvent manquer une surface fine et ne
décrivent ni les parois, ni les trajectoires, ni les supports générés par le
trancheur.

`colonnes-isolees` examine des déplacements et des supports déclarés dans le
G-code ; `appuis_gcode` estime le voisinage XY de trajectoires échantillonnées.
Ces observations portent sur des fichiers différents et ne se fusionnent pas
automatiquement avec une piste STL. Aucune catégorie ne déduit une cause de
picots ni une validation physique.

Le mode `benchab controle appuis-gcode FICHIER --arcs-planaires` est opt-in.
Il évalue des points calculés sur les arcs G17 analytiques et planaires du
G-code, en gardant leur longueur de courbe pour la portée nominale ; il ne
remplace pas l’arc par une corde. Une portion est comptée couverte uniquement
si sa marge de recouvrement aux deux extrémités atteint au moins la moitié de
sa longueur ; sinon sa longueur exacte est entièrement comptée sans appui.
Le pas maximal et le budget global sont respectivement le `--pas-mm` fourni
et 1 000 000 d’échantillons d’arc.
Hélices, largeur manquante, géométrie absente et extrémité discordante au-delà
des arrondis binaires restent `NON VÉRIFIÉ`. Les résultats portent sur les
trajectoires et largeurs nominales ; ils ne qualifient pas le comportement du
trancheur ou une tenue physique. Sans le drapeau, les arcs gardent le refus
historique.

## Comparaison synthétique reproductible

Le test `test_comparaison_synthetique_sections_et_appui_gcode_supporte` place
un îlot de maillage séparé à X = 15 mm, Z = 4 à 6 mm, au-dessus d’un solide distinct. Les
sections du STL donnent `sans_recouvrement_xy_echantillonne`, fraction minimale
0.0 et statut `NON VÉRIFIÉ`. Deux G-code synthétiques au même repère placent
un parcours cible à Z = 4.2 mm : le premier n’a aucun cordon antérieur sous
l’îlot (0 % des points appuyés), le second ajoute un support coïncident (100 %,
recouvrement nominal minimal 0.4 mm, portée libre maximale 0 mm). Le parcours
est mesuré par `appuis_gcode.analyser_lignes`.

Ces témoins ne sont pas issus d’un trancheur : ils vérifient seulement que des supports
ajoutés dans un G-code peuvent fournir une géométrie d’appui absente des coupes
du modèle avant tranchage. Il ne qualifie ni le placement réel de supports, ni
leur adhérence ou tenue.

La qualification structurée des positions intérieures/extérieures reste hors
de ce lot : les rapports n’attribuent pas ces catégories. Il faudra une preuve
géométrique distincte avant de les ajouter.

Les relations de branche/fusion sont calculées par couche avec un `STRtree` des
polygones courants. Seuls les candidats qui intersectent selon l’index reçoivent
le calcul d’aire exact ; le même seuil strict de `1e-9 mm²` décide ensuite si
une relation compte. L’association historique des pistes et ses règles
d’égalité restent inchangées. Une coupe dense où presque tous les polygones se
recouvrent peut toujours produire un grand nombre de candidats.

Sur une mesure locale reproductible avec 400 carrés disjoints dans chacune de
deux coupes (5 répétitions, médiane), le balayage complet de référence a examiné
160 000 paires et trouvé 400 recouvrements en 62,915 ms ; l’index a renvoyé ces
400 relations en 3,172 ms. Ce micro-témoin synthétique ne constitue pas une
garantie de temps pour des maillages réels ; il vérifie surtout que les îlots
disjoints ne provoquent plus une intersection exacte pour chaque paire.
