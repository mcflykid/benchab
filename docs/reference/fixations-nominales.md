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
La tenue, le frottement et l’ajustement restent NON VÉRIFIÉS.
Les filetages ISO restent à traiter (#27).

Le fraisage traverse le bloc par un passage cylindrique. Le diamètre
d’entrée et le diamètre du passage sont reliés par un tronc de cône sur
la profondeur déclarée. Le rapport indique l’angle inclus calculé et la
marge annulaire à l’entrée, distincte d’un épaulement plat. Les sections
rondes sont maillées en 96 côtés ; les marges sont calculées d’après les
cotes nominales. Aucune norme de vis ni correspondance physique n’est déduite.

## Vis et écrou dans une découpe plane

Le connecteur `vis-ecrou` réutilise le lamage et le logement hexagonal. Saisir
le diamètre nominal de vis, le diamètre de sa tête, la profondeur du lamage,
la largeur de l’écrou sur plats, sa profondeur et la source des cotes. On peut
orienter l’écrou en degrés dans le plan de coupe. Le jeu déclaré de même
connecteur et bobine dimensionne les logements sans qualifier l’ajustement.
Sans mesure applicable, le haut de la plage d’essai reste NON MESURÉ.

L’axe doit traverser exactement deux faces extérieures, de part et d’autre
du joint. La tête entre du côté +normale, l’écrou du côté opposé. Le contrôle
utilise la matière réelle pour vérifier le passage, les marges autour des
deux logements et la paroi qui les sépare du joint. Il rapporte la distance
des faces, sans en déduire une longueur de vis ni certifier l’accès d’un
outil ou la tenue de l’assemblage. Les deux pièces sont exportées ; la vis et
l’écrou sont à fournir séparément. Aucune cote ISO n’est choisie automatiquement.

## Filetage triangulaire à cotes déclarées

La commande suivante prépare un maillage fermé et son rapport, dans un
dossier privé neuf. Toutes les valeurs de cet exemple sont fictives.

```sh
benchab conception filetage-nominal -- --diametre-mm 8 --longueur-mm 9 \
  --pas-mm 1.5 --profondeur-radiale-mm 0.65 --source-cotes "exemple fictif" \
  --sortie prive/filetage-exemple
```

Le diamètre indiqué est celui de la crête, la profondeur est radiale et
le pas est l’avance en Z par tour. `--sens gauche` produit le miroir du
filetage droit. Le rôle `--role outil-interieur` demande aussi
`--jeu-radial-mm` : les rayons nominaux sont augmentés de la valeur
déclarée pour former un volume à soustraire. L’outil n’est pas une pièce
à imprimer et le jeu réel reste NON VÉRIFIÉ.

La surface triangulaire est échantillonnée à 64 secteurs et au moins
16 intervalles axiaux par pas, pour un à 64 tours. Les bouts sont plats ;
aucune entrée de filet, troncature ISO, tolérance de fabrication ou
compensation n’est déduite. Le STL est rouvert pour contrôler son volume
et son empreinte ; il ne constitue pas une validation de montage.
