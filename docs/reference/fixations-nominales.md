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

### Profil nominal à méplats déclarés

Ajouter ensemble `--meplat-crete-mm` et `--meplat-fond-mm` pour obtenir
un profil trapézoïdal dans une coupe axiale. Les deux plateaux et chaque
flanc doivent occuper au moins un intervalle axial nominal, soit un
seizième du pas. Le rapport indique leur largeur et l’angle théorique
entre flancs. Le jeu radial d’un outil intérieur déplace les deux rayons
sans changer le pas ni l’angle. Sans ces options, le profil triangulaire
garde son contrat et ses octets.

Les références [ISO 68-1](https://www.iso.org/standard/85107.html)
pour le profil, [ISO 724](https://www.iso.org/standard/85104.html) pour
les dimensions de base et [ISO 965-1](https://www.iso.org/standard/87889.html)
pour les tolérances couvrent des propriétés différentes. Ce profil à
cotes déclarées ne reproduit ni un fond arrondi normatif ni une classe
de tolérance ; aucun ajustement avec une vis ou un écrou réel n’est
certifié (#27).

### Profil métrique ISO nominal

```sh
benchab conception filetage-nominal -- --profil-metrique-iso \
  --diametre-mm 6 --pas-mm 1 --longueur-mm 4 \
  --source-cotes "plan nominal M6 × 1" --sortie prive/filetage-m6
```

Le diamètre et le pas sont fournis ensemble. Les dimensions nominales
suivent les profils à 60° d’ISO 68-1:2023 et les diamètres d’ISO 724:2023.
La vis a un fond circulaire tangent aux flancs. L’outil à soustraire
(`--role outil-interieur`) emploie le profil intérieur à plateaux et
demande un `--jeu-radial-mm` explicite, appliqué aux rayons du profil.
La source des cotes couvre les valeurs déclarées, y compris le jeu.
Le rapport sépare les rayons et diamètres nominaux des rayons de l’outil
après ajout du jeu. L’ajustement réel reste NON VÉRIFIÉ.

Les options de profondeur et de méplats du profil sur mesure sont
incompatibles avec ce mode. Le rapport donne les diamètres mineur et sur
flancs avant jeu. Le modèle maillé garde des bouts plats. Le choix de la
série de pas, la classe de tolérance, les entrées de filet et les jeux
validés par matière restent à établir pour la pièce (#27).

### Greffer un filet femelle existant

```sh
benchab conception greffer-interface -- prive/col.stl prive/support.stl \
  --sha256 EMPREINTE_DU_COL --zone-mm 0.75 4.5 6 --sortie prive/col-greffe
```

Les deux STL utilisent le même repère en mm et l’axe Z passe au centre du
filet. Les trois cotes délimitent la zone à conserver (Z bas, Z haut,
rayon). Prévoir une marge radiale au-delà du dernier contact du filet et
du recouvrement de matière avec le support. Le col peut provenir d’une
pièce existante ou d’un solide creusé avec l’outil intérieur nominal (#27).

Le rapport de greffe (#263) relève le recouvrement, le nombre de
composantes du STL rouvert, le premier contact sur 256 angles et 150
hauteurs, ainsi que l’écart de volume dans la zone déclarée. Un PASS
publie le STL et son rapport dans le dossier privé neuf. FAIL ou NON
VÉRIFIÉ affiche le motif sans publier de STL. La tenue de la jonction
et l’ajustement imprimé restent à qualifier séparément.
