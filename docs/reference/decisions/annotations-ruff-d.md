# Décision : contrôle progressif des annotations publiques

## Besoin

Activer Ruff D10x sans bloquer les 701 lacunes historiques recensées dans le code existant. Dès cette livraison, les nouveaux modules Python sous `benchab/` et les portées publiques effectivement modifiées doivent satisfaire le contrat de docstrings en français. Les défauts historiques hors de ces portées restent suivis par lots.

## Décision

Un contrôleur dédié reçoit deux révisions Git explicites, vérifie qu'elles sont présentes et comparables, puis calcule les fichiers Python ajoutés, modifiés, renommés ou supprimés sous `benchab/`. Il utilise `git diff` avec séparateur NUL et arguments fixes, et Ruff en JSON avec D10x sur les fichiers présents concernés. Les diagnostics ne bloquent que pour un module ajouté ou une portée publique dont la définition ou le corps comprend des lignes ajoutées/modifiées. L'AST relie lignes modifiées et portée englobante, y compris lorsque la ligne `def` n'a pas bougé. Les diagnostics au niveau module concernent les modules nouveaux. Les fichiers supprimés ne sont pas analysés ; un renommage est évalué sous son nouveau chemin.

Les anciens diagnostics Ruff D hors du périmètre différentiel ne bloquent pas. Une base absente, illisible, identique/incohérente, un diff Git inexploitable ou une erreur Ruff produit un échec explicite, jamais une réussite silencieuse. Aucun seuil numérique de dette n'est introduit.

## Contrat d'annotation

Ruff D10x vérifie la présence et la forme structurelle des docstrings publiques. Les revues vérifient aussi le français, le rôle, les entrées/sorties et leurs unités le cas échéant, les statuts et les invariants pertinents. Les commentaires décrivent la raison et référencent une règle U ou documentation lorsqu'applicable ; ils ne reformulent pas l'instruction voisine. Le contrôleur n'affirme pas pouvoir valider seul la qualité sémantique d'une annotation.

## Portée et suites

La CI compare le commit de base réel de la PR/push au HEAD. Une exécution manuelle doit fournir une base explicite ou échoue clairement. Le rattrapage des portées publiques existantes reste par lots suivis séparément ; aucune exception globale ni suppression de règles D n'est ajoutée.

## Exécution

La CI fournit la base de la PR ou le commit précédent du push, récupère l'historique complet, puis exécute :

```sh
python -m benchab.qualite.annotations --base <révision-base> --tete HEAD --racine .
```

Le contrôleur exige que `HEAD` corresponde à la révision demandée. Il ne modifie aucun fichier. Ruff D10x reste activé uniquement dans ce contrôle progressif ; le rattrapage global se fera ultérieurement par lots.
