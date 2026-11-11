# Avertissements du slicer dans un 3MF

`benchab.controles.avertissements_slicer.controler(chemin)` lit uniquement
`Metadata/slice_info.config` dans l’archive, au travers de `Archive3MF` et de
son plafond XML. Le lecteur refuse notamment les liens ZIP dangereux, les
budgets dépassés, les DTD et les entités XML.

## Format qualifié

Le schéma est limité aux éléments émis par la source officielle
AnycubicSlicerNext au commit
[`6103ed8b511609658d00d0538cc7f0609cdb57da`](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp) :

- la racine `<config>` contient un `<header>` et des `<plate>` ;
- l’en-tête identifie l’origine par `<header_item key="X-BBL-Client-Type" value="slicer"/>` ;
- chaque `<plate>` porte l’index dans `<metadata key="index" value="1"/>` ;
- chaque `<warning>` écrit par plaque porte `msg`, `level` et `error_code`.

Le code source Anycubic déclare le membre `Metadata/slice_info.config`, les noms `warning` et
`msg`, écrit le code source d’avertissement sous `error_code`, et produit les
index de plaque sous forme de métadonnée `key`/`value`. L’API conserve chaque
`error_code` comme texte, sans table de compatibilité ; le champ `level` est
requis dans la fixture et n’est pas interprété dans ce premier contrôle. Les codes
`the_actual_nozzle_hrc_smaller_than_the_required_nozzle_hrc`,
`bed_temperature_too_high_than_filament` et tout code inconnu restent donc
visibles.

## Statuts et portée

- `PASS` : le membre, l’en-tête, les plaques et leurs index sont lisibles ;
  aucun avertissement n’a été trouvé.
- `WARN` : la structure est qualifiée et au moins un avertissement a été
  extrait. Ce statut signale sa présence sans en évaluer la gravité.
- `NON VÉRIFIÉ` : membre absent, lecture ou XML invalide, format incomplet ou
  structure inconnue. Le détail est dans `raison`. Les avertissements déjà
  extraits restent dans le rapport ; leur origine est marquée `NON VÉRIFIÉ` si
  l’en-tête ne permet pas de la confirmer.

Chaque avertissement rapporte `plaque`, `code`, `message` et `origine`.
Une alerte lisible garde son code et son message même si une autre plaque est
malformée. L'index absent ou dupliqué est représenté par `null` et rend la
couverture NON VÉRIFIÉE. L’origine associe le type `slicer` déclaré dans
l’en-tête au membre lu ; elle n’authentifie pas le binaire. La restitution
seule ne démontre ni compatibilité physique, ni application des réglages, ni
autorisation d’envoyer une commande à une machine. Ce contrôle ne lit aucun
réglage slicer.

Les fixtures sont synthétiques et dérivées du schéma écrit par AnycubicSlicerNext.
Les témoins S/M/L locaux contiennent deux avertissements par projet, mais
n’identifient pas le client slicer dans leur en-tête : le contrôle garde leurs
messages et codes avec statut `NON VÉRIFIÉ`. Aucune validation sur machine n’a
été réalisée.
