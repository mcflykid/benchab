# Réglages et intégrité des préparations

Le dictionnaire `benchab.reglages` distingue les listes par mode machine des
listes par filament. Une longueur observée ne suffit jamais à déterminer leur
sens. Les définitions proviennent des profils système installés, lus sans
modification, et des métadonnées des préparations privées.

Une clé inconnue reste intacte et NON VÉRIFIÉE. Sa transformation est refusée.
Une ancienne liste de filaments sans correspondance certaine est conservée ;
elle n'est ni raccourcie ni diffusée implicitement.

Chaque transformation déclare avant écriture les différences exactes permises,
avec leur portée projet, objet, partie ou plaque. `benchab.integrite_3mf` relit
l'archive temporaire et compare le résultat au plan avant livraison. Une
différence supplémentaire, une valeur inattendue ou une modification prévue
absente donne FAIL.

Les références d'or sont privées et accompagnées d'empreintes fixes. Le test
`tests/test_references_or.py` les relit dans la suite complète et les tests
d'impact. Leur absence en CI produit un saut explicite, jamais un succès.
Une référence sans G-code ne permet pas de valider des commandes machine.

Sources : profils système du fabricant recensés dans
`benchab/reference/reglages-types.json` ; limites relevées et datées dans
`config/kobra-s1.toml` ; décision de protection contre les réindexations de modes
machine. Ces vérifications sont numériques, sans validation physique implicite.

Quand une référence initiale contient seulement de la géométrie, elle reste
intacte. Un calcul numérique daté peut lui être associé séparément, avec le SHA
du modèle d’origine et du profil explicitement choisi. Le test compare aussi
les réglages et commandes de ce calcul. Il ne devient jamais une impression
historique réussie. Sans références privées, le test est non exécuté ; avec un
manifeste privé présent, un résultat NON VÉRIFIÉ ne suffit pas à le réussir.

Le comparateur de commentaires embarqués distingue les indices de filament et
de mode machine. Une attente pour une liste doit être explicite ; un scalaire
n’est pas diffusé sur plusieurs valeurs. Une clé inconnue reste NON VÉRIFIÉE.
Ce comparateur ne modifie jamais l’archive et ne remplace pas le contrôle
des commandes machine effectivement exécutées.

Le lecteur utilisé détermine N pour `filament_ids` et `inherits_group` (N et N+2).
Le [chargeur PresetBundle](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PresetBundle.cpp)
part de la palette `filament_colour` ; le
[lecteur CLI](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/OrcaSlicer.cpp)
compte les profils effectivement présents. La préparation BENCHAB exige aussi
assez de profils pour les affectations déclarées, sans prétendre que c’est
la formule du lecteur. Le producteur et le lanceur
déclarent donc leur consommateur CLI au dictionnaire unique. Sans consommateur
déclaré, une palette de trois entrées et un seul profil restent ambiguës :
aucune longueur n’est déduite et la correspondance est NON VÉRIFIÉE.
Les couleurs résiduelles ne créent jamais de profils actifs.

La longueur complète des vecteurs machine provient du profil résolu. En
**lecture historique**, un vecteur non vide plus court donne WARN : « repli du
slicer sur la première valeur ». Le rapport donne les valeurs effectivement
lues pour chaque indice attendu. Une liste plus longue donne WARN et liste
les valeurs en trop. Une liste vide lorsqu'une valeur est requise donne FAIL.
Aucune archive n'est corrigée ou normalisée par ce contrôle.

Cette règle cible
[`ConfigOptionVector::get_at`, `src/libslic3r/Config.hpp`, lignes 434–440,
OrcaSlicer v2.3.1](https://github.com/OrcaSlicer/OrcaSlicer/blob/f2d10a0b5760c142e532e09f26fabd485579234c/src/libslic3r/Config.hpp#L434-L440) :
indice demandé s'il existe, sinon première valeur ; le vecteur doit être non
vide. Le commit public Anycubic `6103ed8b511609658d00d0538cc7f0609cdb57da`
descend de ce tag (neuf commits supplémentaires) et son `Config.hpp` est
identique. Son [`version.inc`, ligne 13](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/version.inc#L13)
déclare 2.3.1. La correspondance exacte avec le **binaire Anycubic 2.0.0.3**
reste NON VÉRIFIÉE : les métadonnées du binaire ne donnent pas le commit de
construction. Les archives historiques conservent cette réserve explicite,
même si leur lecture selon `get_at` est déterminée.

Il existe des consommateurs distincts :
[`GCodeProcessor::get_option_value`, lignes 119–123](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/GCode/GCodeProcessor.cpp#L119-L123)
utilise la dernière valeur hors bornes. Ce repli spécialisé d'estimation ne
remplace pas `get_at`. Le contrôle des commandes machine utilise l'indice du
mode déclaré et la lecture `get_at` demandée, puis compare aux limites
configurées ; un M203 X780 attendu à 600 reste FAIL.

En **écriture par BENCHAB**, la longueur exacte reste obligatoire. Un WARN de
lecture n'autorise pas une écriture hors cardinalité. Une origine dont le
vecteur machine est court ou non résolu ne peut pas être modifiée ou complétée
en place, même si la cible a la longueur canonique. La sérialisation contrôle
l'origine et la cible, y compris les mutations imbriquées. Elle ne change
jamais une valeur d'impression pour rendre un fichier acceptable.
