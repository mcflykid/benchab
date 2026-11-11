# Comparaison préparatoire de projets 3MF

`benchab controle comparer-projets-3mf SOURCE.3mf CANDIDAT.3mf` produit en
stdout un diagnostic JSON en lecture seule. Chaque archive est ouverte une
fois par `Archive3MF` ; le rapport contient son SHA-256 exact. Les réglages
projet disponibles sont décodés par le dictionnaire BENCHAB et comparés dans
leur forme native. Les clés absentes sont signalées, les valeurs non typées
restent `NON VÉRIFIÉES`, et aucune conversion ou écriture n’a lieu.

La palette `filament_colour` n’est rapprochée par index que si
`filament_settings_id` contient les mêmes identités non vides dans le même
ordre dans les deux projets, avec une couleur par identité. Les couleurs
restent des chaînes littérales : elles ne sont ni normalisées ni réindexées.
L’ordre des autres listes typées est comparé exactement. Un écart reconnu
produit `WARN` pour les réglages/palette ; une ambiguïté produit
`NON VÉRIFIÉ`.

La peinture par facette demeure `NON VÉRIFIÉE` dans ce premier lot. Le
3MF Core décrit les propriétés d’objet et de triangle au moyen de références
de groupe (`pid`), d’indices (`pindex`, `p1`–`p3`) et de ressources de
matériau. Les attributs de peinture des slicers sont des représentations
distinctes : Anycubic Slicer Next identifie `paint_color` dans sa source
épinglée; PrusaSlicer 2.9.3 identifie `slic3rpe:mmu_segmentation`. Cette
présence documentaire ne prouve ni une équivalence entre ces familles, ni
leur conservation par une conversion. Le diagnostic ne lit pas les facettes,
ne vérifie pas leur identité/ordre et ne compare aucune peinture.

Le statut général reste `NON VÉRIFIÉ` tant que la peinture par facette n’est
pas qualifiée, même si les réglages projet et la palette sont identiques.
Cette première étape ne satisfait donc qu’une partie de #43. Elle ne teste
pas la réouverture par un slicer, les réglages au niveau objet/plaque, les
propriétés standard de matériau ou les encodages de peinture ASN/Prusa.
Fixtures synthétiques seulement ; aucune archive tierce, aucun profil
installé et aucun moteur ne sont utilisés.

Références de format et d’implémentation :

- [3MF Core Specification 1.3.0, triangle properties et basematerials](https://3mf.io/wp-content/uploads/sites/106/2025/02/3MF_Core_Specification_v1.3.0.pdf)
- [Anycubic Slicer Next, `bbs_3mf.cpp` au commit 6103ed8](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp)
- [PrusaSlicer 2.9.3, `3mf.cpp`](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.3/src/libslic3r/Format/3mf.cpp)
