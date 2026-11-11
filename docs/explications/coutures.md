# Coutures visibles : comprendre et préparer

Une couture matérialise les départs et arrêts de boucle. Le 04/11/2026, une
couture alignée le long d’une patte d’un socle noir donnait l’impression d’une
pièce abîmée ; l’assemblage et le dessus des pattes étaient réussis. Une seule
observation de l’auteur sur sa Kobra S1 Combo. La position alignée sur une face
visible est la cause probable de cet aspect ; une mauvaise résistance n’a pas
été démontrée.

## Règle U31

Avant tranchage, distinguer faces visibles, dessous, intérieur et arrière selon
l’usage réel. Prévoir une couture sur une arête ou une face cachée lorsque le
slicer le permet. Rouvrir le résultat et examiner couche par couche les points
de début et fin de paroi extérieure. Une orientation de couture n’est pas une
peinture locale et ne garantit pas le placement sur toute la hauteur.

Le placement des coutures sur des faces visibles reste une revue manuelle (#26). Cette limite doit apparaître dans
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
n’est activé automatiquement par ce parcours de contrôle.

## Sources

Observation du socle du 04/11/2026, retour FLSUN et ressources fabricant
Slicer Next 2.0.0.3 : faits `savoir-asn-2003-couture-candidats` et
`savoir-couture-essuyage-retour-autre-machine` dans
[la référence publique](../../benchab/reference/connaissances.jsonl).
Voir aussi [les retours d’expérience](retours-d-experience.md).

## Contrôle sur des régions déclarées

```sh
benchab --prive /chemin/atelier controle coutures /chemin/tranche.3mf \
  --declaration /chemin/atelier/regions-coutures.json \
  --sortie /chemin/atelier/rapports/coutures-candidate
```

La déclaration relie le SHA-256 exact du 3MF tranché à une plaque et à
l’identifiant de l’objet dans le G-code. Elle indique des polygones XY, leurs
plages Z et leur visibilité déclarée. Toutes les coordonnées et tolérances
sont en millimètres dans le repère spatial de l’interpréteur. Une région
déclarée cachée ne constitue pas une mesure de visibilité réelle.

Le contrôle compare les débuts des boucles de paroi extérieure fermées
dans son périmètre. Des débuts alignés sur plusieurs couches dans une même
région visible donnent WARN. Une attribution ambiguë ou une trajectoire
hors contrat garde NON VÉRIFIÉ. Le rapport conserve les inconnues, y compris
lorsqu’une autre région donne WARN. Le SVG montre les régions en XY et les
points en XY et XZ ; aucun point n’est inventé pour une boucle sans position.

La sortie est facultative et doit être neuve. Elle réunit le rapport
et l’aperçu ; `--detail` affiche également le rapport intégral. Les
propositions de placement ou d’essuyage restent des candidates à comparer.
Aucun réglage n’est écrit, et aucun résultat esthétique ou physique n’est
déduit de l’aperçu.
