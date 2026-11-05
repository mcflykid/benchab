# Filaments : fiches techniques et retours bornés

Les références ci-dessous décrivent des types de filament, jamais le stock de
l’auteur. Une couleur ou une gamme identique ne garantit pas le même lot ou la
même formulation. Vérifier l’étiquette et la buse réellement montée. Les retours
sont observés par l’auteur sur sa Kobra S1 Combo ; les paramètres de fichiers
sont distingués des mesures physiques. Aucun débit maximal n’a été calibré.

## eSUN PLA+ Bone White

Un retour d’éprouvette du 26/10/2026 : 220 °C, trous verticaux proches de 10 mm,
jeu par côté 0,2 mm serré, 0,3 mm glissant, au moins 0,35 mm visiblement libre.
Un essai, pas une loi pour tous diamètres et toutes orientations. Détails dans
[fiabilité FDM](../explications/fiabilite-fdm.md).

Référence bicolore en couche 0,08 mm : eSUN à 220 °C, flow 0,98 et plafond
16 mm³/s, réussite déclarée une fois. L’autre matériau empêche d’en faire un
essai isolé. Mécanismes : petites pièces hautes décollées sans bordure et fentes
de 0,8 mm refermées dans certains retours ; voir les
[défauts constatés](../explications/retours-d-experience.md).

Source fabricant : [eSUN PLA+](https://www.esun3d.com/pla-pro-product).
La fiche historique du 22/10 indiquait 210–230 °C et plateau 45–60 °C. Le produit
actuel consulté peut être une formulation mise à jour ; l’étiquette du lot et
la fiche historique ne sont pas interchangeables.

## SUNLU High Speed PLA Marble, vert forêt

Deux réussites déclarées le 17/10/2026 : première couche 220 °C puis 240 °C,
PEI 55 °C, flow 0,98 et plafond enregistré 32 mm³/s, couches majoritairement
0,24 mm. D’autres réussites utilisent 220 °C et plafond 12 mm³/s en couches plus
fines. Ni retour automatique à 12 ni passage automatique à 32 : lire
[profils et procédés](profils-et-procedes.md).

Étiquette transcrite historiquement : 190–210 °C pour 50–150 mm/s, 210–230 °C
pour 150–300 mm/s, 230–260 °C pour 300–600 mm/s ; plateau 50–60 °C.
Source fabricant : étiquette relue le 22/10/2026, documentée dans le corpus.
Le marquage de résistance thermique de la bobine ne constitue pas une consigne
de séchage. PA 0,035 et flow 0,98 sont des valeurs utilisées, non calibrées.

## Anycubic PLA Luminous blanc, lueur bleue

Un lithophane sphérique personnel réussi : 210 °C puis 190 °C, PEI 55 °C,
flow 0,96 et plafond 12 mm³/s ; auxiliaire modifié manuellement pendant
l’impression. Un autre lithophane tiers présente des picots malgré une bobine
déclarée sèche. La différence de modèle interdit une comparaison géométrique
directe. Voir [lithophanes](../explications/lithophanes.md).

Source fabricant : [Anycubic PLA lumineux](https://fr.anycubic3d.com/products/pla-lumineux),
fiche historique relue le 22/10/2026 : 200–230 °C, plateau 60–70 °C. Les
190/55 °C du retour sont une expérience locale, distincte des recommandations.
Ancienne et nouvelle formulation ne garantissent pas la même compatibilité ACE.

## Anycubic PLA Marble blanc

Une impression du 24/10/2026 a repris avec ce matériau après changement de
filament. Réglages envoyés 220 puis 240 °C hérités de l’autre matière ; hauteur
de transition et modifications à l’écran inconnues. Réussite d’usage déclarée,
défaut local et fils non attribuables à un matériau précis. Aucune calibration
de débit ni validation séparée à 240 °C. Source : retour anonymisé dans le corpus.

## Anycubic PLA Basic

Un support en noir déclaré bien imprimé le 02/11/2026 : la qualité de surface
ne démontrait pas l’adaptation à tous les bracelets. Un socle noir du 04/11
est fonctionnel, mais dessous de pattes imparfaits et couture visible. Ces
observations ne valident pas toute la gamme de couleurs. Les autres variantes
historiques n’ont pas de retour isolé suffisant pour leur attribuer une réussite.

## Autres matières et recherche d’une référence

PETG, ABS/ASA et TPU ne reçoivent ici aucun jeu ou profil prétendument validé
sur cette machine. Demander une référence fabricant et un usage précis ; sans
mesure locale, indiquer « non mesuré ». Les jeux par matière et les connecteurs
à clip de la feuille de route future ne sont pas des fonctions 1.0.

## Sources et consultation

Fiches filaments historiques, étiquettes et retours datés :
[corpus structuré](../../benchab/reference/connaissances.jsonl), avec conditions,
statut, occurrences connues ou inconnues et
[empreintes de provenance](../../benchab/reference/sources-savoir.jsonl).
Les prix, quantités restantes et affectations personnelles ne sont pas publiés.
