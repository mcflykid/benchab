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
attributs. Une translation pure du build est admissible si sa déclaration
est strictement identique des deux côtés. Les neuf coefficients linéaires
doivent former exactement l’identité et les trois translations être bornées
à 10⁹ unités du modèle. Un placement différent, une rotation, une échelle,
un composant, `p:path`, des objets multiples, des relations OPC ambiguës ou
des attributs d’extension inconnus restent `NON VÉRIFIÉS`.

Dans ce sous-cas, les extensions ASN `paint_color` et Prusa
`slic3rpe:mmu_segmentation` sont comparées comme attributs XML littéraux
uniquement si les deux archives utilisent le même QName. Une propriété absente
sur une facette est signalée `WARN`. Si le modèle et le dialecte unique sont
établis d’un seul côté et que l’autre n’a aucun attribut de ce dialecte, toutes
les absences ou tous les ajouts littéraux sont rapportés en `WARN`. Les
facettes permutées, un dialecte/QName différent, un modèle ambigu, des
relations OPC mal formées, un chemin URI non interne ou une facette dégénérée
donnent `NON VÉRIFIÉ`. Aucune valeur n’est décodée ou transcodée.
Dans le même sous-cas direct, les groupes de matériaux de base Core
(`basematerials`) résolvent le matériau déclaré sur l’objet et chaque facette.
Le rapport compare le nom littéral et la couleur sRGB d’affichage, avec
alpha `FF` implicite quand il n’est pas écrit. Un changement d’identifiant
de groupe ou d’index sans changement de matériau ne compte pas comme une
perte. Un matériau déclaré qui change ou disparaît est signalé en `WARN`.
Le contrôle refuse les indices hors groupe, les identifiants ambigus, les
gradients entre matériaux de base et les extensions requises hors de ce
sous-cas. Deux modèles sans matériau attribué restent `NON VÉRIFIÉS`.

La couleur d’affichage Core ne définit pas la couleur imprimée ni la
correspondance d’un filament. Le statut général reste `NON VÉRIFIÉ` :
le diagnostic ne convertit pas la peinture entre slicers et ne démontre
pas qu’un slicer conserve les déclarations à la réouverture. Les composants,
les extensions de matériau, les réglages au niveau objet/plaque et les
encodages de peinture ASN/Prusa restent à qualifier (#43).

Références de format et d’implémentation :

- [3MF Core Specification 1.3.0, triangle properties et basematerials](https://3mf.io/wp-content/uploads/sites/106/2025/02/3MF_Core_Specification_v1.3.0.pdf)
- [Anycubic Slicer Next, `bbs_3mf.cpp` au commit 6103ed8](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp)
- [PrusaSlicer 2.9.3, `3mf.cpp`](https://github.com/prusa3d/PrusaSlicer/blob/version_2.9.3/src/libslic3r/Format/3mf.cpp)

Pour un retour court, demander `--resume --sortie prive/comparaison.json`.
Le résumé indique les statuts, les compteurs et le chemin du rapport complet ;
aucune ligne détaillée n’est retirée du fichier. `--sortie` seule enregistre
aussi le JSON complet. Une sortie existante est refusée, et les erreurs de
lecture gardent leur message sur stderr. Les archives restent intactes (#35).

Sur les deux archives de la sonde native multi-volumes #48, le résumé a
occupé environ 560 octets sur stdout, contre 97 141 octets pour le détail
compact. Le JSON privé conserve le même contenu que le détail compact.
Les archives ont gardé leurs empreintes ; la peinture des composants
multiples est restée NON VÉRIFIÉE.
