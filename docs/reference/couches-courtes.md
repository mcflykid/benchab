# Couches courtes et îlots issus des sections

`benchab controle couches-courtes` prélève des sections XY du maillage STL à
l’intérieur d’intervalles Z de hauteur `lh` (mm). Les rapports existants
gardent leurs champs `centre_xy_mm`, `de_z`, `a_z`, `hauteur_mm`, les aires,
`motifs` et `sommet_terminal`.

Le champ `qualification_sections` ajoute des observations numériques des
sections, sans modifier ni préparer le modèle :

- `position_initiale = premier_echantillon_du_maillage` signifie que la piste
  est présente au premier prélèvement près du Z minimal du STL.
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
