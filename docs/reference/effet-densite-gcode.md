# Mesurer l’effet local d’une hausse de densité

Dans l’éprouvette de remplissage localisé, BENCHAB compare B à A : même forme,
mêmes régions mesurées, et hausse de densité déclarée pour le modificateur.
La troisième variante change aussi les parois et reste hors de ce verdict.

Le dépôt clairsemé est mesuré en mm à 0,1 mm près. Deux arrondis
indépendants donnent un intervalle de ±0,1 mm autour de chaque écart affiché.
La comparaison exige que tout l’intervalle du contrôle hors zone soit contenu
entre −0,1 et +0,1 mm.

- **PASS** : le bas de l’intervalle de hausse dans la zone dépasse +0,1 mm.
- **FAIL** : l’intervalle de l’effet reste entièrement entre −0,1 et +0,1 mm
  (effet nul à cette précision) ou entièrement sous −0,1 mm (effet inversé).
- **NON VÉRIFIÉ** : mesures manquantes, dépôt insuffisant, stabilité hors
  zone non établie ou intervalle de l’effet à cheval sur le seuil.

Sur la sonde synthétique de la [qualification multi-volumes](integrite-3mf.md#qualification-native-dun-modificateur-48),
le dépôt dans une boîte intérieure de 8 × 8 × 4 mm passe de 628,2 à
1 833,8 mm entre 20 et 60 %. Deux boîtes témoins extérieures restent à 225,4
et 244,4 mm dans les deux variantes. La hausse de 1 205,6 mm est donc
mesurée dans ces conditions. Les archives et rapports de recette restent
privés.

Cette comparaison n’attribue pas d’effet à la peau, au squelette ou à un
changement du champ Application. Ces variantes restent NON VÉRIFIÉES
tant que leur consommation et leur effet n’ont pas été établis (#82).

L’API `evaluer_effet_reglage_gcode` compare deux G-codes et exige que son appelant
fournisse les réglages et le champ Application relus dans leurs 3MF, ainsi que le
SHA du même maillage, le rôle `modifier_part`, l’identité et le SHA du même
modificateur, et une paire commune de régions zone/hors-zone disjointes. Elle
refuse une mesure si ces déclarations diffèrent ou si le binaire ne correspond
pas à ASN R1. Le seul changement admis dans cette portée est
`sparse_infill_density` de 20 à 60 %, avec un motif clairsemé recensé par R1 et
le rôle `Sparse infill`. Les
champs peau/squelette verrouillés, les surfaces internes pleines et tout autre
réglage restent NON VÉRIFIÉS ; aucune absence de dépôt ne vaut FAIL tant que
cette comparaison n’est pas qualifiée.
Les longueurs sont calculées avec la tolérance déjà documentée de ±0,1 mm sur
chaque écart affiché. La fonction ne lance pas le trancheur et ne modifie pas
les 3MF, les profils ou le G-code.
