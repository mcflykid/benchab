# Astuces à comparer à une préparation de référence

Les valeurs proposées dans les captures sont des hypothèses d’essai. Pour isoler
leur effet, garder le même modèle, la même pose, le même filament et le même
trancheur, puis changer un réglage à la fois. Comparer les réglages embarqués et
les trajets. Garder le rendu, le retrait des supports et la tenue pour les
retours de projets imprimés.

| Astuce | Usage dans BENCHAB |
| --- | --- |
| Couche de 0,12 mm | Comparer à la hauteur de la référence sur une zone détaillée ou inclinée. Une variante à couches variables peut réserver les couches fines aux zones utiles. Relever les hauteurs réellement tranchées, le temps et la matière. [Hauteur variable dans OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/wiki/prepare_variable_layer_height) · [Témoin A01](../explications/surplombs-ponts-supports.md) |
| Vitesse par rôle | Distinguer paroi visible, remplissage et première couche. Les plages 50–60, 120–150 et 15–20 mm/s des captures restent des valeurs à comparer au profil d’origine. Le débit dépend aussi de la hauteur, de la largeur et du filament. Un plafond estimé avant tranchage se contrôle ensuite sur les trajets. [Contrôle BENCHAB U25](debit.md) · [Limites de vitesse dans OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/wiki/speed_settings_other_layers_speed) |
| Supports et interface | Comparer organique et normal sur le même débord. Séparer l’écart Z supérieur demandé de l’écart nominal lu dans le G-code. Faire varier séparément le nombre de couches d’interface (0 les désactive) et leur espacement en mm, selon le motif. Les valeurs 10–12 %, 2–3 couches et 0,13–0,16 mm des captures décrivent des pistes d’essai. [Mesures BENCHAB avec Anycubic Slicer Next](ecart-z-supports.md) · [Réglages des supports dans OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_advanced) |
| Couture en rampe (scarf) | Piste pour une paroi extérieure courbe avec une couture ordinaire. Comparer une référence à une rampe sur un contour assez long. Contour, départ à 10 %, jeu à 2 % et longueur 8 mm sont les valeurs proposées par la capture. Vérifier leur portée (procédé ou filament) et le trajet produit par la version locale du trancheur. OrcaSlicer exclut le mode vase et la première couche. [Documentation OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_seam) |
| Températures | Identifier la fiche exacte du filament et le type de plateau. Garder séparées les recommandations fabricant, les consignes de première couche et du reste de l’impression, les commandes du G-code et les observations d’usage. [Fiche Anycubic PLA Basic](https://store.anycubic.com/collections/pla/products/pla-filament) · [Références BENCHAB sur les filaments](filaments.md) |
| Orientation | Comparer la pose de référence à une pose tournée, en relevant la surface d’assise, les supports, les faces de finition et le temps. Sur une pièce chargée, prendre en compte le sens de l’effort par rapport aux couches. [Parcours BENCHAB](../guides/cycle-projet.md) · [Orientation automatique dans OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer/wiki/prepare_auto_orient) |

Les essais de supports couvrent déjà plusieurs variantes indépendantes. Sur un
coupon, une consigne d’écart Z de 0,18 mm a donné un écart médian nominal de
0,20 mm. Sur un autre, passer de deux couches supérieures d’interface à zéro a
supprimé les trajets de ce rôle. Ces résultats dépendent du modèle, du style de
support et du binaire. Une qualité de dessous ou un retrait plus facile reste
une observation à recueillir sur les projets concernés.
