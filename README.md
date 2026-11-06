![BENCHAB](docs/identite/benchab3d-logo.png)

# BENCHAB

Atelier local de conception, contrôle et préparation d’impression 3D, en français.
Une CLI, une base SQLite privée et une visionneuse relient modèles, préparations,
réglages, preuves et retours physiques. Les scripts déterministes précèdent l’IA.

## Installer et commencer

Depuis une copie du dépôt, avec [uv](https://docs.astral.sh/uv/getting-started/installation/) installé :

```sh
uv sync --locked
uv run benchab init
uv run benchab etat
uv run benchab projet ma-piece --niveau S
```

Python est sélectionné par `.python-version`. L’environnement `.venv` est le seul
Python utilisé par l’atelier. Le slicer est un programme externe, installé séparément.
Le [guide d’installation](docs/guides/installation.md) décrit les prérequis.

## Usage courant

Suivre le [cycle d’une pièce](docs/guides/preparer-une-piece.md), ou ouvrir le
[guide de découpe](docs/guides/decoupe.md) pour séparer et assembler un modèle.
La [documentation](docs/README.md) donne accès aux commandes et aux explications.
Aucune commande de préparation ne lance une impression.

## Limites

Seule la Kobra S1 est qualifiée dans la 1.0, sur macOS avec Anycubic Slicer Next 2.0.0.3.
La qualification concerne la chaîne numérique ; aucun contrôle numérique ne
prouve le résultat physique. Les jeux non mesurés, la tenue des connecteurs et
les zones sans observation restent explicitement NON VÉRIFIÉS.
La lecture Cloud est testée sur réponses simulées ; leur présence ne
vaut pas autorisation de contacter une imprimante.

## Adapter à une autre imprimante

Suivre le [guide d’adaptation](docs/guides/autre-imprimante.md) : profil TOML privé,
profils du slicer, limites matière et témoins mesurés. Une configuration chargée
ne constitue pas une qualification.

## Données et licence

Tous les projets, photos, profils personnels et bases restent dans `prive/`,
ignoré par Git. Sauvegarder ce dossier séparément. Les seuls fichiers 3D publics
autorisés sont les témoins S/M/L et les fixtures générées par l’atelier.
Voir la [frontière public/privé](docs/decisions/2026-11-04-frontiere-public-prive.md).
Le savoir technique est public, y compris les retours de l’auteur sur sa machine,
avec conditions, occurrences et limites. Voir les [matières](docs/reference/filaments.md),
les [réglages réussis](docs/reference/profils-et-procedes.md) et les
[défauts constatés](docs/explications/retours-d-experience.md).

BENCHAB est sous [GPL-3.0-or-later](LICENSE). Les composants et leurs licences
sont recensés dans [TIERS.md](TIERS.md).

<!-- BENCHAB:BASE:DEBUT -->
Versions enregistrées : aucune version livrée.
<!-- BENCHAB:BASE:FIN -->

[Feuille de route et suivi des versions](ROADMAP.md).
