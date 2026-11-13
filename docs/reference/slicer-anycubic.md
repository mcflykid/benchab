# Anycubic Slicer Next : préparation et incidents

Périmètre : version 2.0.0.3, Kobra S1, lancements en ligne de commande sur macOS.
Les incidents ci-dessous sont des constats locaux, pas un diagnostic universel.
Le lanceur commun est `benchab fabrication trancher` ; consulter son `--help`
pour les chemins et options. Les profils installés restent intacts.

## Contrat du lanceur

Chaque lancement crée un datadir neuf, ses parents et un répertoire de sortie.
HOME, TMPDIR et XDG_CACHE_HOME pointent vers le temporaire. Le verrou empêche
deux slicers simultanés. Une préparation contient au plus 30 plaques par 3MF :
c’est une borne prudente du parcours qualifié, pas une limite universelle prouvée.

Les listes `filament_type`, `filament_colour`, `filament_ids` et
`filament_settings_id` doivent être complètes et cohérentes. Le champ
`filament_id` au singulier est une métadonnée scalaire : il ne remplace pas
la liste `filament_ids`. Les valeurs
`BENCHAB-temporaire-N` sont réservées aux témoins et mesures ; une valeur restante
dans un fichier à imprimer fait échouer le contrôle d’envoi. Ne pas inventer un
identifiant permanent pour contourner ce contrôle.

Les noms de plaques d’origine sont conservés. Ils sont retirés seulement dans
la copie reçue par le moteur puis remis dans le 3MF tranché. Le fichier final
est rouvert et les noms comparés. Nommer une plaque avant le moteur a déclenché
un incident local lié à la génération de sa texture.

## Aucun réglage d’impression modifié en silence

`top_shell_layers` par objet est légitime. Un objet à 8 couches et un projet à 5
doivent rester à 8 et 5 dans le résultat. Ni suppression de l’override ni refus
pour ce motif. L’hypothèse historique d’un lien avec `bridge_over_infill` repose
sur un incident unique ; elle n’autorise aucune correction de réglage.

Le rapport de tranchage liste les modifications de la copie. Toute modification
qui change le G-code est interdite ; le nom documentaire des plaques est restauré
ensuite. Une correction demandée de réglages produit une nouvelle préparation
explicite, pas une réparation cachée du lanceur.

## Portée du mode vase

Dans Anycubic Slicer Next 2.0.0.3, `spiral_mode` s’applique à toute la plaque.
Le [code public de la configuration de plaque](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.hpp#L1270-L1357)
le déclare dans `PrintConfig`. Une surcharge d’objet ou de plage de hauteur ne
permet donc pas de passer en vase au-dessus d’une base pleine dans la même
pièce. [La normalisation du moteur](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L7062-L7075)
force une seule ligne de paroi, zéro couche supérieure, zéro remplissage et
désactive la rétraction aux changements de couche.

- Imprimer séparément la base pleine et la coque en vase sur des plaques
  distinctes permet de garder les deux procédés. L’assemblage ajoute un joint
  dont l’ajustement reste à valider sur la pièce.
- Imprimer la pièce entière en mode classique conserve la liberté de choisir
  l’épaisseur. Une couture arrière près d’un biseau rend l’emplacement
  prévisible. Une couture aléatoire disperse les marques.
- Sur un relief, une couture alignée dans un creux marqué peut réduire sa
  visibilité. Vérifier sa trajectoire dans l’aperçu avant de trancher la
  comparaison sur une pièce.

Avec une seule ligne de paroi, la largeur de ligne fixe l’épaisseur nominale.
Elle influe sur la rigidité et la transmission de lumière. La matière, la
géométrie et la qualité du dépôt influent aussi sur le résultat physique.

## Avertissements pour un filament lumineux

`the_actual_nozzle_hrc_smaller_than_the_required_nozzle_hrc` invite à comparer
la dureté renseignée dans le profil machine à l’exigence renseignée par le
profil filament. La déclaration de la buse montée et les valeurs embarquées
restent visibles séparément dans le rapport d’envoi.

`bed_temperature_too_high_than_filament` signale un risque selon la combinaison
profil filament et température de plateau lue par le moteur. Pour le PLA Glow,
comparer la surface choisie et les températures embarquées à la
[fiche filament](filaments.md). Conserver le code natif et sa provenance dans le
[rapport](avertissements-slicer.md).

## Lire le rapport de plantage

| Fonction en tête de pile | Piste à examiner | Réponse du parcours |
| --- | --- | --- |
| `generate_plate_name_texture` | Nom de plaque transmis au moteur | Vérifier la copie moteur et la restauration finale. |
| `expand_plate_extruders`, `filaments_cnt` | Configuration multi-filament | Examiner cohérence et longueur des listes. |
| `ConfigOptionVector` | Liste de filaments vide | Compléter explicitement le fichier source. |
| `load_from_3mf_structure` | Trop de plaques parmi les causes possibles | Fractionner explicitement la préparation à 30 plaques maximum. |
| `bridge_over_infill` | Cause non établie après un seul incident | Archiver pile et échec ; aucune relance avec réglage modifié. |

Le dernier rapport AnycubicSlicerNext `.ips` dans DiagnosticReports est lu après
un plantage ; la pile et la copie fautive vont au dossier d’échec privé. Au plus
trois essais par cause identifiée. Ces noms de fonctions orientent une enquête,
ils ne prouvent pas à eux seuls la cause. `sandbox-exec` ne fait pas partie du
parcours. Aucun accès aux profils installés n’est nécessaire au lanceur.

Une cause non identifiée est plafonnée uniquement pour la même opération et le
même contexte vérifiable : SHA-256 source pour le tranchage et la visualisation,
mode explicite (`aide` ou `defauts`) pour l’inspection. Les anciennes inconnues
sans ce contexte restent dans l’historique, mais ne sont pas agrégées à une
nouvelle demande ; elles ne permettent pas de déterminer si la cause est commune.

## Relire le résultat

Rouvrir le 3MF, contrôler toutes les plaques et les paramètres réellement
embarqués, puis analyser le G-code : températures, matériaux, vitesses,
continuité de paroi, supports, interfaces et démarrage. Un fichier G-code.3MF
peut ne plus contenir de maillage ; il ne remplace donc pas une source CAO.
Une ouverture réussie ne prouve ni l’impression ni l’ajustement physique.
Voir [le cycle de projet](../guides/cycle-projet.md).

## Sources

Incidents locaux du lanceur et corrections déclarées les 04–05/11/2026 ; un seul
incident rapporté pour l’hypothèse `bridge_over_infill`. Contrat exécuté et tests
du [lanceur](../../benchab/fabrication/trancher.py), provenance historique et
statuts dans [le corpus](../../benchab/reference/connaissances.jsonl) et
[les empreintes des sources](../../benchab/reference/sources-savoir.jsonl).
