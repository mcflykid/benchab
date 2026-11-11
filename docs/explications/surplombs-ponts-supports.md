# Surplombs, ponts et supports

Un pont relie deux appuis ; un surplomb s’étend depuis un seul côté. Le slicer
peut traiter un anneau courbe au-dessus d’une ouverture comme un surplomb plutôt
qu’un pont. Lire les trajectoires réelles et le type de ligne, pas seulement
l’apparence du modèle. Le dessous visible fait partie des critères de réussite.

## Retours physiques

Des poches imprimées sans support ont terminé leur impression avec leurs
plafonds affaissés. Une ouverture était boudinée alors qu’une autre était plus
propre : l’orientation, le refroidissement et le trajet sont des hypothèses,
pas des causes isolées. Le 04/11/2026, un socle fonctionnel a des imperfections
sous des pattes en surplomb sans support, mais leurs dessus sont réussis.

Une paroi propre et un assemblage correct ne valident donc pas tous les dessous.
Classer les zones visibles et les appuis avant de décider de supprimer des
supports. Une modification de profil peut déplacer le défaut vers une autre zone.

`benchab controle surplombs-connexes piece.stl --unite-mm` produit une carte
numérique des faces connexes orientées vers le bas, avec aire, inclinaison et
altitudes en unités explicites. Pour STL, `--unite-mm` confirme l'unité fournie
par l'appelant : le format ne la déclare pas. Le seuil d'angle est un filtre
géométrique configurable, pas un seuil de support. Le rapport ne calcule ni
l'accès réel au plateau, ni la franchissabilité d'un pont, ni les trajectoires
du slicer ; ces champs restent NON VÉRIFIÉS. Il ne justifie aucun réglage ni
besoin physique de support. La connexité repose sur l'égalité exacte des
coordonnées d'arêtes ; aucune tolérance ne rapproche les sommets. Le rapport
compte les arêtes ouvertes, non-manifold et d'orientation locale incohérente,
et conserve le sens des triangles lus. Les normales ne démontrent pas une
orientation physique correcte. Le descripteur et l'identité du chemin sont
contrôlés avant et après lecture ; le SHA-256 est calculé sur les octets fournis
au décodeur, puis conservé dans le rapport.
Pour borner l'usage mémoire, la commande refuse les sources de plus de 64 Mio
et les STL de plus de 50 000 triangles avant de construire le graphe Python.

L'option opt-in `--plateau-z-mm Z` ajoute, pour chaque groupe, des visées
verticales depuis les barycentres de faces sélectionnées vers le plan horizontal
`Z` déclaré en mm. Le rapport conserve l'identifiant de face et le point XYZ
de chaque visée, sous le libellé `visées_échantillonnées`, et exige le SHA-256
du STL exact ; au plus 256 rayons sont calculés au total. Le CLI calcule ce SHA
sur les octets mêmes qu'il décode. Les triangles
projetés, leurs intersections et les contacts sont évalués avec une tolérance
de distance de `0,000001 mm` et de poids barycentrique `0,000000001`. Un plan
non fini ou qui atteint une face candidate rend les visées de ce groupe
ambiguës, sans invalider un groupe distinct plus haut. Pour une projection XY
dégénérée alignée, les intersections de ses arêtes donnent les bornes Z de la
fibre au point visé ; elle ne rend le rayon ambigu que si cette fibre touche
le segment. Une frontière ou des triangles superposés rendent la visée
ambiguë si leur altitude touche le segment. Les contacts avec le plan ou
avec une autre face au départ restent également ambigus. Une visée DÉGAGÉE ne
signifie que l'absence d'intersection détectée sur ce segment échantillonné ;
une visée OBSTRUÉE indique un triangle numérique rencontré entre ses deux
extrémités. Si des faces sont omises par le budget, l'agrégat reste AMBIGUË.
Ces rayons ne couvrent pas l'intérieur des faces ni le reste d'un groupe, et ne
prouvent ni accessibilité d'un support, ni franchissabilité d'un pont, ni appui
ou tenue physique. Le champ historique `acces_plateau` reste NON VÉRIFIÉ.

`--dossier-apercu DOSSIER_NEUF` conserve ensemble `rapport.json` et
`apercu.html` dans un emplacement privé ou temporaire. Ouvrir la page
autonome et choisir une visée dans la liste : le repère situe le barycentre
sur le STL, et indique l’îlot, le statut et l’altitude. Les points omis sont
comptés, aucun marqueur n’est ajouté à la géométrie exportable. L’empreinte
du maillage est celle du rapport. La page ne déduit aucun réglage.

## Choisir le support dans Slicer Next 2.0.0.3

| Famille | Usage envisagé | Point à contrôler après tranchage |
| --- | --- | --- |
| Normal, grille | Surplombs plats et appuis stables | Matière et marques autour de l’appui. |
| Normal ajusté, `snug` | Suivre le contour | Continuité et accès pour retrait. |
| Arbre organique | Branches vers des contacts locaux | Tenue des branches et trace de contact. |
| Arbre fin ou fort | Compromis masse/rigidité | Stabilité contre difficulté de retrait. |
| Hybride | Grands plats et autres régions | Répartition réellement générée. |
| Manuel | Uniquement les zones forcées | Aucun surplomb important oublié. |

Bloqueurs et forceurs désignent localement les zones. « Plateau seulement »
interdit un départ depuis la pièce. Une distance Z plus grande facilite souvent
le retrait au prix d’un dessous moins propre ; sa valeur tranchée dépend des
couches et du mode de support. Davantage d’interface peut lisser le dessous mais
durcir le retrait. Ce sont des mécanismes attendus, pas des résultats physiques
validés sur cette machine pour tous les modes.

## Méthode de décision

Comparer supports générés, interface, distance réellement tranchée, matière et
durée estimées. Le protocole historique du 23/10/2026 a comparé 23 variantes
d’éprouvettes à couche 0,20 mm par tranchage ; il n’a testé ni retrait, ni marques,
ni tenue réelle des branches. Ne pas convertir un gain numérique en qualité
physique. Préférer une autre orientation ou une géométrie autoportante si elle
préserve l’usage, puis vérifier dimensions et montage.

Pour un canal horizontal, un profil en goutte pointe vers le haut et des
flancs inclinés peuvent supprimer un plafond difficile ; vérifier le dégagement
utile sur toute la course. Pour un pont, surveiller l’accrochage initial et
l’affaissement au milieu. Le contrôle d’envoi et la relecture visuelle restent
complémentaires ; une réussite sur un petit pont ne valide pas une portée longue.

## Sources

Étude de supports Slicer Next du 23/10, leçons de mécanismes et observations
locales du 04/11/2026 : [corpus avec portée et preuve](../../benchab/reference/connaissances.jsonl),
[catalogue des sources](../../benchab/reference/sources-savoir.jsonl).

Avec `--sortie RAPPORT.json --resume`, le rapport complet est créé en privé
et la commande affiche le chemin, les cinq groupes de plus grande aire et
le nombre d’omissions. `--detail` affiche aussi le rapport complet. Sans
ces options, la sortie historique est conservée. Les appuis et ponts restent
NON VÉRIFIÉS dans le résumé.

### Recette sur un coupon tranché

Deux archives du même coupon, tranchées hors ligne avec le même binaire ASN
2.0.0.3, conservent le même maillage placé. La carte relève une base à
Z = 0 mm (168 mm², visées non qualifiées au niveau du plan) et un surplomb
horizontal de 224 mm² à Z = 14,8 mm. Les visées échantillonnées du surplomb
sont dégagées. Les deux réglages d’interface donnent respectivement 100 et
64 segments de rôle `Support interface`.

Dans les deux archives, 61 segments de rôle `Bridge` sont déposés à Z = 15 mm.
Leurs axes mesurent 498,446 mm, dont 460,227 mm dans la projection XY du
surplomb ; leur dessous nominal `Z - HEIGHT` coïncide avec Z = 14,8 mm à la
précision de la recette. Le rôle `Internal Bridge` apparaît à Z = 17 mm ;
sa projection XY recoupe aussi le surplomb mais son altitude ne correspond
pas à cette surface. L’altitude évite donc d’attribuer ce second parcours au
même surplomb sur la seule superposition des axes.

Cette mesure ne tient pas compte de la largeur du cordon et ne prouve ni
un appui, ni un pont imprimable, ni un retrait. Elle illustre une comparaison
après tranchage pour un coupon et deux réglages d’interface, sans changer la
classification des cartes avant tranchage (#93).

### Comparer un îlot à une plaque choisie

`benchab controle ponts-surplombs STL TRANCHE.3mf --unite-mm --ilot surplomb-002 --plaque 1
--translation-mm X Y Z --sha-stl SHA_STL --sha-3mf SHA_3MF` relit la carte avec
le même seuil d’angle et sélectionne l’îlot demandé. Il compare les SHA aux
deux fichiers relus avant de mesurer. Les trois composantes de translation
en mm sont une déclaration : le contrôle ne reconstitue pas une rotation,
une échelle ou une correspondance entre objets. `--sortie RAPPORT_NEUF`
conserve le détail en privé et affiche un résumé.

Le sous-cas couvre un îlot horizontal et des axes de dépôt linéaires
des rôles `Bridge` et `Internal Bridge`. Le rapport distingue les longueurs
dans la projection XY et celles dont le dessous nominal `Z − HEIGHT`
coïncide aussi avec le niveau de l’îlot. Une hauteur ne se propage pas à
une nouvelle couche ou à un autre niveau sans déclaration. Les arcs,
les lignes trop longues et les diagnostics du lecteur laissent la
comparaison NON VÉRIFIÉE. PASS signifie que la comparaison nominale est
couverte, même quand aucun axe ne correspond. Il ne valide ni un appui,
ni la largeur d’un cordon, ni l’impression.

### Hypothèse géométrique avant tranchage

`controle surplombs-connexes --unite-mm --portee-pont-mm PORTEE`
peut signaler un plafond horizontal rectangulaire aligné si ses quatre
côtés sont complets et si deux côtés opposés entiers touchent des faces
voisines qui descendent sous lui. La portée est la distance entre ces
côtés. Le maximum est déclaré pour la comparaison ; aucune valeur
universelle n’est choisie. Un seul côté, une portée supérieure, une
topologie ouverte ou une géométrie ambiguë restent NON VÉRIFIÉS (#93).

Le rapport et, avec le plan de visée déclaré, l’aperçu indiquent l’axe
et la portée. L’hypothèse ne décide ni d’un réglage de support, ni d’une
franchissabilité. Les trajets `Bridge` du G-code sont une vérification
distincte ; l’adhésion et l’appui réels exigent encore une observation.

Sur un témoin synthétique fermé, le plafond rectangulaire a une portée de
4 mm suivant X. Deux tranchages hors ligne avec ASN 2.0.0.3, supports
désactivés, donnent chacun neuf axes `Bridge` concordants en projection et
en altitude (36 mm au total). Le rôle `Internal Bridge` recoupe la projection,
mais aucun de ses axes n’est au niveau nominal du plafond. Sur le coupon
de 224 mm² décrit plus haut, l’hypothèse reste NON VÉRIFIÉE : les deux bords
entiers ne sont pas établis par cette géométrie. L’absence d’hypothèse
n’interdit donc pas au slicer de générer des ponts.
