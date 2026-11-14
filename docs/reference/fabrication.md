# Tranchage natif

```sh
uv run benchab fabrication trancher prive/projets/ma-piece/candidats/projet.3mf --sortie prive/projets/ma-piece/candidats/tranche-v01
```

Préparer la fiche `projet.3mf.apercu.json` à côté de l’archive avant cette
commande. Le [cycle de projet](../guides/cycle-projet.md#valider-laperçu-dune-candidate)
décrit la décision et les empreintes attendues.

Pour compléter des identifiants de filament permanents, passer un catalogue JSON
privé avec `--identifiants CHEMIN` (objet racine `filaments`) ou définir
`BENCHAB_IDENTIFIANTS_SLICER`. L’option explicite prime sur la variable
d’environnement. Les clés sont les noms exacts de `filament_settings_id` ; chaque
entrée indique un `id` existant et ses `sources`. Une correspondance absente ou
ambiguë est refusée. Les identifiants déjà présents dans le 3MF sont conservés.

Tous les appels natifs passent par `benchab/fabrication/trancher.py` ; aucun autre
lanceur et aucun `sandbox-exec`. Un verrou interdit deux slicers simultanés.
Chaque exécution crée ses parents, son `--datadir` neuf et son `--outputdir`.
HOME, TMPDIR et XDG_CACHE_HOME sont temporaires dans `/tmp`. Les profils installés
ne sont jamais utilisés comme dossier de travail.

Le lanceur refuse plus de 30 plaques. La source doit fournir les listes
`filament_type` et `filament_colour`. Les identifiants `filament_ids` et
`filament_settings_id` doivent être permanents pour une impression ; le catalogue
peut compléter un identifiant permanent à partir du nom exact du profil, avec sa
provenance. Le champ scalaire natif `filament_id` reprend le premier identifiant.
Les cardinalités d’héritages entièrement vides peuvent être corrigées, sans
supprimer de parent nommé. Ces modifications de métadonnées sont toutes tracées ;
aucun paramètre d’impression n’est inventé. Des identifiants
`BENCHAB-temporaire-N` sont réservés aux témoins (`--usage temoin`) ; dans un
fichier destiné à l’impression, le contrôle d’envoi les signale FAIL.

Une copie reçoit uniquement la suppression temporaire des noms de plaques,
nécessaire à un plantage observé. Les noms originaux sont remis dans le 3MF
tranché, rouvert et vérifié. `top_shell_layers` reste inchangé au niveau projet
comme au niveau objet, même si les valeurs diffèrent. Aucun réglage de secours
n’est appliqué automatiquement. Le rapport liste les modifications de la copie.

En cas d’échec, conserver le dossier d’incident, la sortie du processus et la pile
du dernier rapport de plantage disponible. Les noms de fonctions sont des indices,
non des causes établies. `bridge_over_infill` n’a été observé qu’une fois et ne
justifie pas de modifier `top_shell_layers`. Au plus trois essais par cause.

Après tranchage, lancer les [contrôles d’envoi](controles.md), inspecter l’état
sauvé dans le slicer et suivre le [vote](vote.md). Un fichier tranché n’est pas
une preuve d’envoi ni d’impression.

Pour reprendre une archive déjà tranchée sans reconstruire ni relancer le
slicer, utiliser `benchab fabrication revue-archive ARCHIVE.3mf --declaration
DECLARATION.json --sortie prive/rapports/envoi-final.json`. La commande crée
des rapports initiaux et finaux, une revue et ses faits dans l’atelier privé.
Une revue source peut être fournie avec `--revue-source`; elle doit porter les
empreintes actuelles de l’archive et de la géométrie, et se trouver dans le
même dossier que la sortie afin de préserver les chemins relatifs de ses
preuves. Une revue périmée est refusée sans la réécrire. Les décisions humaines
et leurs preuves sont conservées quand ces empreintes concordent. Les mesures
déclarées de recul et de recouvrement sont recopiées à l’identique puis recalculées
par le contrôle final ; une contradiction reste bloquante. Les décisions
hors des zones courantes restent dans la revue originale et sont signalées
dans le rapport. Les zones restantes non revues gardent un statut NON VÉRIFIÉ. Ce parcours ne valide pas
physiquement la pièce et n’envoie aucune commande à la machine.
# Chaîne commune des projets

`benchab fabrication chaine CHEMIN_PROJET` ou `make tout` dans un gabarit
configuré exécute la cohérence native, l’environnement, la construction, les
contrôles géométriques et d’interfaces, les références, la préparation, le
décollement, le tranchage par le lanceur unique, la lecture du G-code, la routine,
la revue automatique des zones et le contrôle d’envoi complet, continuité de
paroi comprise. Un FAIL arrête les étapes suivantes ; une exception de script
n’est jamais convertie en WARN.

Les contrôles propres au niveau L sont aussi exécutés. `--optimiser` et
`--objectif` conservent le choix explicite d’une étude d’optimisation ; ils ne
constituent pas un accord d’impression. Les rapports et erreurs complets sont
dans `preuves/chaine/`. Ce dossier doit être neuf, comme les candidates ; la
chaîne ne remplace ni ancienne preuve ni préparation figée.

Cette commande exige les profils et filaments renseignés dans le projet privé.
Elle ne chauffe pas, ne contacte pas la machine et ne réalise aucun vote.
La revue automatique ne couvre que ses critères connus ; les risques restants
exigent la [relecture avant vote](vote.md). Voir le [cycle de projet](../guides/cycle-projet.md).

Les commandes `benchab fabrication optimisation proposer` et `comparer`
acceptent `--profil CHEMIN`. Le profil explicite fourni par l’API ou la CLI prime
sur `FABRICATION.json` (`machine.profil`), qui prime sur la configuration générale
(`machine.profil_maitre`). Le rapport indique l’origine choisie et conserve les
snapshots et empreintes du profil. La résolution reste confinée à l’espace privé ;
les profils installés ne sont jamais modifiés.
