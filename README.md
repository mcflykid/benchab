# BENCHAB

![Logo BENCHAB 3D](docs/identite/benchab3d-logo.png)

BENCHAB est un atelier local en français pour concevoir des pièces et préparer
des impressions 3D. Il réunit les fichiers d’une pièce, ses réglages, les
contrôles du 3MF et du G-code, puis les observations faites sur l’impression.
Les commandes s’utilisent depuis le terminal. Une visionneuse permet
d’inspecter les modèles et les couches dans le navigateur.

## Démarrer

Depuis une copie du dépôt, avec Git et [uv](https://docs.astral.sh/uv/getting-started/installation/) :

```sh
uv sync --locked
uv run benchab --version
uv run benchab init
uv run benchab etat
```

`init` crée l’atelier privé et sa base SQLite. La version de Python est
définie dans `.python-version` et uv utilise l’environnement `.venv` du
dépôt. Pour le tranchage natif, installez Anycubic Slicer Next séparément.
Le [guide d’installation](docs/guides/installation.md) détaille les
prérequis et les outils facultatifs.

Créez ensuite un dossier de travail pour votre pièce :

```sh
uv run benchab projet ma-piece --niveau S
```

Le [cycle de préparation d’une pièce](docs/guides/preparer-une-piece.md)
indique où placer les sources, comment préparer une plaque et quels
rapports relire avant de retenir un fichier. Pour inspecter les projets
dans la visionneuse locale, lancez `uv run benchab visualisation serveur`.

## Que faire dans l’atelier ?

| Si vous voulez… | Consultez… |
| --- | --- |
| Partir de cotes pour créer un modèle | [Fiches CAO](docs/reference/fiche-cao.md) |
| Préparer une fixation ou un filetage nominal | [Fixations et filetages](docs/reference/fixations-nominales.md) |
| Découper un modèle et préparer son assemblage | [Guide de découpe](docs/guides/decoupe.md) |
| Examiner les pièces, les supports et les couches | [Visionneuse et aperçu G-code](docs/guides/visionneuse.md) |
| Comprendre un verdict et retrouver sa preuve | [Lecture des rapports](docs/guides/lire-les-rapports.md) |
| Consigner un défaut, une mesure ou un ajustement | [Retour d’impression](docs/guides/retour-impression.md) |

Toutes les commandes et références sont accessibles depuis
[l’index de la documentation](docs/README.md).

## Machine, vérifications et limites

Le parcours numérique qualifié couvre la Kobra S1 sur macOS avec Anycubic
Slicer Next 2.0.0.3. La préparation se fait localement et aucune commande de
préparation ne lance une impression. La lecture Cloud est testée sur des
réponses simulées. Pour une autre imprimante, le [guide
d’adaptation](docs/guides/autre-imprimante.md) décrit le profil privé et les
témoins à vérifier.

Les contrôles numériques portent sur les fichiers et les réglages déclarés.
La tenue, les jeux réels et les observations qui demandent une mesure
restent **NON VÉRIFIÉS** sans preuve physique. Les retours documentés
précisent leurs conditions d’application dans les références
[matières](docs/reference/filaments.md), [réglages](docs/reference/profils-et-procedes.md)
et [défauts observés](docs/explications/retours-d-experience.md).

## Données privées et sauvegarde

Les projets, photos, profils personnels et la base SQLite se trouvent dans
`prive/`, ignoré par Git. Sauvegardez ce dossier séparément du dépôt.
Seuls les témoins S/M/L et les fixtures générées sont autorisés comme fichiers
3D publics. Voir le [guide des données et sauvegardes](docs/reference/donnees.md)
et la [frontière public/privé](docs/decisions/2026-11-04-frontiere-public-prive.md).

## Versions et licence

<!-- BENCHAB:BASE:DEBUT -->
Les versions livrées sont décrites dans le [CHANGELOG](CHANGELOG.md) et associées
à leurs tags Git (`vX.Y.Z`).
<!-- BENCHAB:BASE:FIN -->

La [feuille de route](ROADMAP.md) détaille les travaux en cours. BENCHAB
est sous [GPL-3.0-or-later](LICENSE). Les dépendances et leurs licences
sont recensées dans [TIERS.md](TIERS.md).
