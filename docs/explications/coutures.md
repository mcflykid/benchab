# Coutures visibles : comprendre et préparer

Une couture matérialise les départs et arrêts de boucle. Le 04/11/2026, une
couture alignée le long d’une patte d’un socle noir donnait l’impression d’une
pièce abîmée ; l’assemblage et le dessus des pattes étaient réussis. Une seule
observation de l’auteur sur sa Kobra S1 Combo. La position alignée sur une face
visible est la cause probable de cet aspect ; une mauvaise résistance n’a pas
été démontrée.

## Règle U31 dans la 1.0

Avant tranchage, distinguer faces visibles, dessous, intérieur et arrière selon
l’usage réel. Prévoir une couture sur une arête ou une face cachée lorsque le
slicer le permet. Rouvrir le résultat et examiner couche par couche les points
de début et fin de paroi extérieure. Une orientation de couture n’est pas une
peinture locale et ne garantit pas le placement sur toute la hauteur.

BENCHAB 1.0 ne classe pas automatiquement les faces visibles et ne réalise pas
le futur placement intelligent des coutures. Cette limite doit apparaître dans
les risques de la relecture avant vote, avec le statut NON VÉRIFIÉ tant que
l’aperçu n’a pas permis de trancher. Une couture aléatoire répartit les marques,
elle ne les supprime pas et peut dégrader une autre surface.

## Réglages candidats de Slicer Next 2.0.0.3

| Option | Intention à examiner | Statut local |
| --- | --- | --- |
| `wipe_on_loops` | Essuyage sur les boucles | Candidat, désactivé dans les profils relus. |
| `wipe_before_external_loop` | Essuyage avant boucle extérieure | Candidat, désactivé dans les profils relus. |
| `seam_slope_type` et mode conditionnel | Couture en biseau, notamment sur courbes | Candidat, non validé physiquement Kobra S1. |
| `staggered_inner_seams` | Décaler les coutures intérieures | Candidat, désactivé dans les profils relus. |

Les noms et valeurs existent dans les ressources fabricant vérifiées ; leur
présence ne démontre ni efficacité ni sélection active. Un retour de l’auteur
sur une FLSUN et un autre logiciel rapporte une fin et reprise de paroi vers
l’intérieur : c’est une piste, pas une validation transférable à la Kobra S1.

L’aperçu du G-code doit montrer le mouvement réellement dirigé vers l’intérieur
et le raccord de la surface visible. Un nom d’option ou une case activée ne
remplace pas cette lecture. Comparer ensuite sur une vraie pièce autorisée,
en distinguant bavure, trou au raccord et surépaisseur. Aucun de ces réglages
n’est activé automatiquement par la 1.0.

## Sources

Observation du socle du 04/11/2026, retour FLSUN et ressources fabricant
Slicer Next 2.0.0.3 : faits `savoir-asn-2003-couture-candidats` et
`savoir-couture-essuyage-retour-autre-machine` dans
[la référence publique](../../benchab/reference/connaissances.jsonl).
Voir aussi [les retours d’expérience](retours-d-experience.md).
