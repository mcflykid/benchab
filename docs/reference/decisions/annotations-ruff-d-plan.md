# Plan : ratchet Ruff D progressif

**But :** contrôler Ruff D10x sur le code nouveau et les portées publiques modifiées, en ignorant les lacunes historiques intactes.

**Architecture :** contrôleur Python dans `benchab/qualite/annotations.py`, appelé en CI avec les deux révisions Git explicites. Le diff NUL détermine les chemins sûrs ; l'AST associe lignes modifiées aux portées publiques, et Ruff JSON fournit les violations D10x.

**Technologies :** Python standard, AST, subprocess Git/Ruff, pytest, GitHub Actions.

**Décision :** `docs/reference/decisions/annotations-ruff-d.md`.

## Contraintes globales

- Code nouveau et modifié annoté en français selon `AGENTS.md`.
- Pas d'installation ni de modification des profils ou projets privés.
- Les bases Git invalides et les erreurs d'outil échouent explicitement.
- Seules les violations dans le périmètre différentiel bloquent ; aucun seuil global.

## Points de revue

- Base inconnue ou objet manquant : échec explicite.
- Chemins contenant espaces, tabulations, retours de ligne ou préfixes ambigus : transport NUL et arguments séparés.
- Renommages, suppressions et fichiers hors `benchab/` : portée documentée et testée.
- Corps modifié avec ligne `def` inchangée : la portée reste contrôlée.
- Ruff absent ou erreur d'analyse : échec explicite.

### Étapes

1. Ajouter les fixtures de diff synthétiques et vérifier leur échec attendu.
2. Implémenter le calcul différentiel et l'association AST/Ruff.
3. Ajouter la commande de CI avec fetch complet et base PR/push explicite.
4. Documenter la commande et les limites, exécuter les tests ciblés et Ruff D sur le périmètre.
