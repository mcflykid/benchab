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
besoin physique de support.

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
