# Décision : contrôle progressif des annotations publiques

## Besoin

Activer Ruff D10x sans bloquer les 701 lacunes historiques recensées dans le code existant. Dès cette livraison, les nouveaux modules Python sous `benchab/` et les portées publiques effectivement modifiées doivent satisfaire le contrat de docstrings en français. Les défauts historiques hors de ces portées restent suivis par lots.

## Décision

Un contrôleur dédié reçoit deux révisions Git explicites, exige une base
antérieure et le code exact de HEAD, puis lit le diff NUL des fichiers Python
sous `benchab/`. Il compare les AST par nom qualifié, indépendamment de leurs
numéros de ligne. Une modification du corps, d'un décorateur, d'une signature
ou d'une docstring qualifie la portée même si la ligne `def` ne bouge pas.
Les commentaires et déplacements de lignes seuls ne changent pas l'AST.
Supprimer une portée n'impute pas sa dette à une voisine intacte.

Ruff D10x décide de la visibilité, y compris paquets, méthodes spéciales et
constructeurs. Les nouveaux modules, les renommages et les suppressions de
docstrings de module sont contrôlés ; les fichiers supprimés ne sont pas lus.

Les anciens diagnostics Ruff D hors du périmètre différentiel ne bloquent pas. Une base absente, illisible, identique/incohérente, un diff Git inexploitable ou une erreur Ruff produit un échec explicite, jamais une réussite silencieuse. Aucun seuil numérique de dette n'est introduit.

## Contrat d'annotation

Ruff D10x vérifie la présence et la forme structurelle des docstrings publiques. Les revues vérifient aussi le français, le rôle, les entrées/sorties et leurs unités le cas échéant, les statuts et les invariants pertinents. Les commentaires décrivent la raison et référencent une règle U ou documentation lorsqu'applicable ; ils ne reformulent pas l'instruction voisine. Le contrôleur n'affirme pas pouvoir valider seul la qualité sémantique d'une annotation.

## Portée et suites

La CI compare le commit de base réel de la PR/push au HEAD. Un déclenchement
manuel GitHub compare au premier parent ; la commande locale prend une base
explicite. Une base introuvable fait échouer le contrôle. Le rattrapage des
portées publiques existantes reste suivi par lots ; aucune exception globale
ni suppression de règles D n'est ajoutée.

## Exécution

La CI fournit la base de la PR ou le commit précédent du push, récupère l'historique complet, puis exécute :

```sh
python -m benchab.qualite.annotations --base <révision-base> --tete HEAD --racine .
```

Le contrôleur exige que `HEAD` corresponde à la révision demandée. Il ne modifie aucun fichier. Ruff D10x reste activé uniquement dans ce contrôle progressif ; le rattrapage global se fera ultérieurement par lots.
