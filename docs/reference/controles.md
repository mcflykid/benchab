# Contrôles et statuts

PASS : le critère numérique testé est satisfait. WARN : un seuil mérite revue.
FAIL : le critère est violé. NON VÉRIFIÉ : une donnée ou une preuve manque.
Un statut global ne supprime pas les détails et les zones restant à vérifier.

`uv run benchab controle envoi FICHIER` rassemble les contrôles du fichier final.
Les options `--declaration`, `--geometrie`, `--revue-locale` et `--reference`
rattachent leurs preuves. `--relation-reference` distingue même source de modèle,
seulement même machine/filament, et relation inconnue.
Les constantes et critères vérifiables sont reliés dans [les règles](regles.md).
La routine vérifie le [contrat de placement](placement.md) avant le premier
contrôle ; une déclaration invalide donne une erreur localisée sans créer ses rapports.

Les familles couvrent : plaques CLI/interface, réglages, pièces, paquet 3MF,
finition/parois, débit, appuis, première couche, décollement, ponts internes,
colonnes isolées, couches courtes, supports requis, orientations, disposition,
ergots et assemblage. Les modules partagent un lecteur G-code indexé par SHA-256.
Une revue locale ne vaut que pour la zone et l’empreinte qu’elle vise ; un fichier
modifié invalide sa preuve. Le contrôle de [continuité](continuite-paroi.md)
utilise des seuils absolus et précise la portée de toute référence.

Une opportunité de découpe n’est ni une coupe calculée ni une validation de
connecteur. Les [contrôles propres à la découpe](../guides/decoupe.md) sont distincts.
Le contrôle de publication est décrit dans le [guide développeur](../guides/developpement.md).

## Reconstruction et réimpression

```sh
uv run benchab qualite archives prive/projets/ma-piece prive/mesures/inventaire.json
uv run benchab qualite comparer-fabrication -- --attendu prive/references/ma-piece --obtenu prive/projets/ma-piece/candidats --sortie prive/mesures/comparaison.json
uv run benchab controle reimpression -- --projet prive/projets/ma-piece
```

L’inventaire ne suit aucun lien et n’extrait aucune archive. Les budgets et
inspections incomplètes restent explicites et ne produisent pas PASS. Le rapport
doit être neuf, privé et hors de la source. Le comparateur distingue géométrie,
fabrication, identité binaire du conteneur et documentaire ; aucune différence
de G-code ou de réglage n’est ramenée à une simple date. Même un conteneur ZIP
à contenu identique garde son écart d’identité binaire dans le rapport.
Le contrôle de réimpression vérifie le verrou local et les empreintes, sans
transformer leur identité en accord d’envoi ou en observation physique.

## Contrôles privés d’un projet L

Les fichiers `src/controles/*.py` exposent `verifier(projet)` et renvoient un
statut. Le pilote `src.verifier` conserve un rapport individuel JSON sous
`preuves/controles/` avec les sorties complètes ; les erreurs d’import et
d’exécution donnent FAIL avec leur pile. Le rapport agrégé est
`preuves/qualite-projet.json`. Ces rapports neufs remplacent les anciens logs
texte de sous-processus ; un rapport existant n’est jamais écrasé.
Un résultat inconnu reste NON VÉRIFIÉ (code 3), FAIL donne 2 et WARN donne 1.
Un exemple sans mécanisme déclaré reste NON APPLICABLE. Les valeurs et les SHA
d’un partenaire déclaré imprimé sont des contrôles de fichier, sans validation
physique de l’ajustement.

Le contrôle complet borne chaque lancement externe (Ruff et pytest) à 1 800 s,
arrête son groupe de processus en cas de dépassement et conserve les journaux entiers.
Un code de découverte pytest 5 reste FAIL. La RSS par étape est une somme de RSS
du groupe échantillonnée, pas un pic d'allocation exact ; les maxima du processus
appelant et de ses enfants cumulés sont présentés séparément.

`benchab qualite syntaxe-privee PROJET` vérifie les scripts Python actifs de
`prive/projets/PROJET` hors dépendances et archives de projet, sans les exécuter ni créer de `.pyc`. Un script illisible
ou invalide donne FAIL ; l'absence de script reste NON VÉRIFIÉE. Le projet doit
être choisi explicitement et les liens internes restent refusés.

## Codes de sortie des verdicts

Les rapports numériques de l’envoi, des règles, de la continuité, de la routine,
du contrôle complet et de la chaîne portent le code que retourne leur commande.

| Verdict | Code |
| --- | ---: |
| PASS | 0 |
| WARN ou WARNING | 1 |
| FAIL | 2 |
| NON VÉRIFIÉ | 2 |
| INCONNU, BLOQUE ou ATTENTE HUMAINE | 2 |

Lire le statut du rapport pour distinguer les situations qui partagent le code
2. Les codes natifs des processus enfants restent disponibles dans leurs traces.

Les étapes de `qualite tout` sans délai configuré disposent de 1 800 s.
Les lectures de l’index et des blobs Git disposent de 10 s. Un dépassement
échoue avec un motif et arrête le groupe créé pour la commande. Le pilote
Safari de recette est lui aussi arrêté dans son propre groupe.

Les lectures de débit et de continuité suivent une position E et une dette
de rétraction communes à l’axe E. La sélection Tn identifie le filament.
`G92 E` change le repère sans acquitter la rétraction. Un référencement
`G28` remet les axes demandés à l’origine nominale. Un segment de dépôt
immédiat après référencement en cours de trajectoire est exclu et signalé.

Pour `controle regles`, les résultats évalués et `hors_portee` sont séparés.
Les règles hors portée ne comptent pas dans le code global. Une donnée
manquante dans un contrôle exécutable garde le code NON VÉRIFIÉ.
