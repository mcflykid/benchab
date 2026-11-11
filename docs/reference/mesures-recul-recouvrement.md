# Mesures locales de recul et recouvrement

Les identifiants et regroupements de zone existants sont conservés afin de
rattacher les anciens avis humains au même G-code. Le contexte d'objet est
ajouté après le calcul de l'identifiant ; un groupe mêlant plusieurs identités
ne qualifie pas une nouvelle mesure de recul ou de recouvrement.

Une déclaration peut être ajoutée à la revue locale sous
`mesures_recul_recouvrement`, sous forme de liste d'objets. Chaque objet porte
`zone_id`, `plaque`, `gcode_sha256`, `ligne_source`, `couche_source`,
`ligne_cible`, `couche_cible`, `axe_local`, `valeur_declaree_mm` et
`tolerance_mm`. Les numéros de ligne sont les lignes physiques du membre
`Metadata/plate_<plaque>.gcode` (base 1). L'empreinte est le SHA-256 des octets
originaux de ce membre.

`axe_local` est un vecteur XY orienté de la source vers la cible. Le calcul le
normalise et projette les deux segments d'extrusion linéaires dans le repère XY
du plateau décrit par le G-code. Les enveloppes unidimensionnelles sont
élargies de la moitié de la largeur `;WIDTH:` de chaque cordon. La séparation
entre enveloppes est signée : valeur positive = recul, valeur négative =
recouvrement. Le centre de la ligne cible doit rester dans la `bbox_xy_mm` de
la zone, avec prise en compte de son arrondi au 0,001 mm. Si le G-code utilise
des identifiants d'objet, les deux cordons doivent appartenir au même objet que
la zone. Les projections des cordons sur l'axe perpendiculaire doivent se
recouvrir afin de rester localement associés; le recul le long de l'axe déclaré
peut être positif. La largeur et les coordonnées viennent du G-code; elles ne
sont pas une observation de la pièce imprimée. La déclaration doit fournir une
tolérance finie en mm; aucune tolérance de mesure par défaut n'est appliquée.

Une incohérence supérieure à cette tolérance ou une empreinte source erronée
est FAIL et bloque l'acquittement. Une ligne absente, une largeur inconnue, un
axe nul/invalide, une couche incohérente ou une couverture de mouvement non
fiable rend la mesure NON VÉRIFIÉE et laisse la zone à revoir. Les arcs ne sont
pas convertis en cordes. Les décisions humaines déjà liées sont conservées,
mais ne neutralisent pas une contradiction mesurée.

Cette mesure est une comparaison numérique de projections sur un axe choisi;
elle ne mesure ni contact tridimensionnel, ni adhérence, ni support, ni tenue
physique. Ces projections vérifient seulement une association numérique locale,
pas un contact physique. Les rapports historiques de portée (diagonale de boîte XY) et de
support dans `revue_locale.py` gardent leur sens historique et ne sont pas
réinterprétés comme des mesures de recul ou de recouvrement.
