# Développer et vérifier le dépôt

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run benchab controle typographie docs
uv run benchab controle publication
```

Les tests construisent leurs propres fixtures et isolent l’atelier privé.
Les hooks résolvent le dossier courant à chaque appel, depuis la charge utile
quand elle le fournit. Leur filtre des commandes shell est une aide heuristique,
pas une barrière de sécurité : une écriture détournée peut lui échapper. Les
contrôles explicites des livrables et des empreintes restent nécessaires (#21).
La CI macOS exécute les tests unitaires sans slicer ; les essais natifs et Blender sont
séparés et demandent leurs programmes externes. L’outil d’impact existant choisit
les tests avec les imports, liens documentaires, appels Make et relations JSON
déclarées (`dependances`). Il revient à la suite complète si l’impact est inconnu.
Il n’existe qu’un système d’impact.

`controle publication` bloque les chemins et identifiants personnels, secrets,
bases publiques, liens vers le privé, modèles non autorisés, licences non recensées
ou incompatibles, documents mal encodés et liens morts. Les liens internes sont
vérifiés localement ; les liens HTTP des documents sont interrogés en lecture.
Un échec réseau laisse le contrôle en échec, sans présumer un lien valide.
Les inventaires de licences et de fixtures sont liés aux versions et empreintes.
Une nouvelle dépendance nécessite la lecture de sa licence puis la mise à jour de
[TIERS.md](../../TIERS.md) et du manifeste associé.

Ne jamais ajouter de projet réel pour faire passer un test public. Les témoins et
fixtures 3D autorisés doivent être générés par le code public et enregistrés avec
leurs empreintes. Conserver les licences des bibliothèques intégrées ; les modèles
d’entrée gardent leur propre licence. Les fonctionnalités futures ne sont pas
ajoutées sous forme de modules vides.

Le contrôle `uv run benchab controle typographie CHEMIN` accepte aussi un rapport
JSON généré : il examine les textes des valeurs, sans traiter les clés comme de
la prose. Il détecte les collages mot/nombre et nombre/unité ; blocs de code,
chemins et identifiants techniques explicites sont distingués des phrases.
