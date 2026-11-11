# Comparaison préparatoire de projets 3MF

`benchab controle comparer-projets-3mf SOURCE.3mf CANDIDAT.3mf` produit en
stdout un diagnostic JSON en lecture seule. Chaque archive est ouverte une
fois par `Archive3MF` ; le rapport contient son SHA-256 exact. Les réglages
projet disponibles sont décodés par le dictionnaire BENCHAB et comparés dans
leur forme native. Les clés absentes sont signalées, les valeurs non typées
restent `NON VÉRIFIÉES`, et aucune conversion ou écriture n’a lieu.

La comparaison littérale de `filament_colour` est toujours rendue par index de
liste, y compris si l’association n’est pas qualifiée. Cet index de sérialisation
ne devient jamais un slot actif par déduction ; les couleurs au-delà des profils
déclarés sont listées comme résiduelles et `NON VÉRIFIÉES`.

L’association `filament_colour`↔`filament_settings_id` n’est qualifiée que si
les deux projets ont les mêmes identifiants de profil non vides et uniques dans
le même ordre, si le contexte typé confirme exactement une couleur par profil,
et si chaque couleur est une chaîne `#RRGGBB`. Même alors, le PASS porte
uniquement sur la référence de profil déclarée et l’index de palette. Un nom de
profil ne prouve ni une bobine réellement chargée, ni l’identité physique d’un
filament, ni l’attribution d’une pièce ou d’une facette. Les couleurs restent
des chaînes littérales, sans normalisation ni réindexation. L’ordre des autres
listes typées est comparé exactement. Un écart reconnu produit `WARN` pour les
réglages/palette ; une ambiguïté produit `NON VÉRIFIÉ`.

La peinture par facette reste partielle. Pour un sous-cas direct et non ambigu,
le lecteur suit un StartPart OPC unique vers un seul objet/maillage et un item
de build direct. Il compare les coordonnées de sommets, leurs unités, l’ordre
des indices de facettes et l’identité d’objet exactement avant de regarder les
attributs. Composants, `p:path`, transformations, objets multiples, relations
OPC ambiguës ou attributs d’extension inconnus restent `NON VÉRIFIÉ`.

Dans ce sous-cas, les extensions ASN `paint_color` et Prusa
`slic3rpe:mmu_segmentation` sont comparées comme attributs XML littéraux
uniquement si les deux archives utilisent le même QName. Une propriété absente
sur une facette est signalée `WARN`. Si le modèle et le dialecte unique sont
établis d’un seul côté et que l’autre n’a aucun attribut de ce dialecte, toutes
les absences ou tous les ajouts littéraux sont rapportés en `WARN`. Les
facettes permutées, un dialecte/QName différent, un modèle ambigu, des
relations OPC mal formées, un chemin URI non interne ou une facette dégénérée
donnent `NON VÉRIFIÉ`. Aucune valeur n’est décodée ou transcodée.
Les références Core `pid`, `p1`–`p3` et leurs ressources restent
`NON VÉRIFIÉES`, car ce lot n’en résout pas la sémantique. Leur présence est
signalée sans verdict sémantique, avec prise en compte de `pid`/`pindex` de
l’objet et de `pid`/`p1`–`p3` des facettes. Aucun de ces constats ne prouve la
compatibilité du slicer ou la conservation après conversion.

Le statut général reste `NON VÉRIFIÉ`, car la comparaison littérale n’établit
ni la sémantique des propriétés Core ni la compatibilité du slicer, même si les
réglages projet, la palette ou les attributs littéraux sont identiques.
Cette première étape ne satisfait donc qu’une partie de #43. Elle ne teste
pas la réouverture par un slicer, les réglages au niveau objet/plaque, les
propriétés standard de matériau ou les encodages de peinture ASN/Prusa.
Fixtures synthétiques seulement ; aucune archive tierce, aucun profil
installé et aucun moteur ne sont utilisés.

Références de format et d’implémentation :

- [3MF Core Specification 1.3.0, triangle properties et basematerials](https://3mf.io/wp-content/uploads/sites/106/2025/02/3MF_Core_Specification_v1.3.0.pdf)
- [Anycubic Slicer Next, `bbs_3mf.cpp` au commit 6103ed8](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp)
- [PrusaSlicer 2.9.3, `3mf.cpp`](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.3/src/libslic3r/Format/3mf.cpp)
