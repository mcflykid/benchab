# Contribuer

## Versions et environnement

BENCHAB déclare Python 3.13 comme version minimale (`pyproject.toml`) et épingle
Python 3.13 dans `.python-version`. La CI exécute actuellement les contrôles sur
macOS 15 avec cette version ; d’autres versions ou systèmes ne sont pas déclarés
validés. La série 1.1 reçoit les changements par pull request après revue et CI verte.

Installez l’environnement verrouillé avec `uv sync --locked`. Utilisez le Python
unique du dépôt, `.venv/bin/python`, pour lancer les outils et les tests.

## Changements

1. Vérifiez les issues existantes et gardez chaque pull request dans un périmètre
   cohérent.
2. Créez une branche de travail et ouvrez une pull request vers `main`.
3. Ajoutez ou adaptez des tests pour les comportements concernés. Les données de
   test doivent être synthétiques ; ne copiez pas de projets, profils ou secrets
   personnels.
4. Avant la demande de revue, exécutez les commandes utilisées par la CI :

   ```sh
   uv run python -m benchab.qualite.frontiere --racine .
   uv run ruff check .
   uv run pytest -q
   ```

5. Vérifiez les changements et gardez la CI verte. Les intégrations passent par
   pull request squash ; incluez `Fixes #N` dans la description pour chaque issue
   résolue.

Le contrôle de frontière s’exécute sans initialiser l’atelier privé. Il inspecte
les fichiers publics, l’index Git et les contenus de modèles pris en charge ; il
ne remplace pas une vérification humaine. Pour un problème de sécurité, suivez
[la procédure de signalement](SECURITY.md) et ne joignez aucun secret à une
contribution publique.
