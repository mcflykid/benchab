# Logements de fixation à cotes déclarées

`benchab.conception.fixations.generer_outil` renvoie un volume à soustraire
et ses marges géométriques. Le sous-cas se limite à un bloc rectangulaire
aligné dont la face d’entrée est à `z=0` et le logement suit l’axe Z.
Les dimensions du bloc, le centre XY, la profondeur, la marge minimale
et la source des cotes sont fournis explicitement. Toutes les cotes sont en mm.

- `insert_cylindrique` : diamètre et profondeur d’un logement borgne.
- `lamage` : diamètre et profondeur du renfoncement, diamètre du passage
  traversant distinct.
- `ecrou_hexagonal` : largeur **sur plats**, profondeur, passage traversant
  et orientation XY en degrés. À zéro degré, un sommet pointe vers +X.

Le contrôle rapporte le résidu aux quatre côtés, l’épaisseur sous le logement
hors passage et, pour une forme traversée, l’épaulement minimal. Une marge
insuffisante refuse l’outil. Les formes rondes sont maillées en 96 secteurs ;
les marges utilisent le cercle nominal, de façon conservatrice.

Ce contrôle n’applique ni cote ISO, ni jeu par matière, ni compensation de
trou. Les observations U32 gardent leur matériau, leur orientation et leurs
conditions ; elles ne sont pas transférées à une fixation différente. Le
maillage renvoyé représente le vide à soustraire, pas une pièce à imprimer.
La tenue, le frottement et l’ajustement restent NON VÉRIFIÉS. Les filetages,
fraisages et l’intégration aux connecteurs de découpe restent à traiter (#27).
