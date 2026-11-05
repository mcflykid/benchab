# Installation locale

Prérequis : Git et uv, un système disposant des roues Python des bibliothèques
CAO/maillage. La version de Python est épinglée dans `.python-version` ; ne pas
installer un second environnement pour contourner une dépendance.

Après récupération du dépôt :

```sh
uv sync --locked
uv run benchab init
uv run benchab configuration
uv run benchab --help
```

`uv sync --locked` installe les dépendances dans l’environnement du dépôt.
Les groupes de développement et de recette sont installés par défaut.
Pour exécuter les scénarios de recette qui ouvrent Chromium, installer le
navigateur avec `uv run playwright install chromium`.
L’entrée du skill est le lien `benchab` vers ce checkout : ses docs et son code
suivent les changements de cette même racine sans copie. Cette opération ne
synchronise pas les dossiers de skills copiés d’autres projets.

Le lien local du skill est une opération distincte et reste en plan sec par défaut.
Les dossiers de destination doivent déjà exister ; la commande ne crée aucun dossier
ni fichier et ne crée que le lien `benchab`. Elle ne retire que des liens dont la
cible résolue appartient à l’ancien atelier explicitement indiqué.

```sh
uv run benchab skill --racine "$PWD" --destination "$HOME/.agents/skills"
uv run benchab skill --racine "$PWD" --destination "$HOME/.agents/skills" --appliquer
```

Ajouter une option `--destination` par dossier skills existant supplémentaire.
Vérifier ces dossiers avant d’exécuter la commande : le plan refuse les dossiers absents.
N’ajouter `--ancien-atelier /chemin/explicite/Impression-3D` que si le lien historique
doit être retiré. Le plan vérifie les collisions et refuse toute modification des
destinations intervenue entre planification et application. Cette commande ne
modifie pas l’installation Python : celle-ci reste `uv sync --locked`.

`init` crée `prive/` et la base sans remplacer un atelier existant. Pour un atelier
séparé : `uv run benchab --prive /tmp/benchab-essai/prive init`.
Le chemin se place avant la commande, ou dans `BENCHAB_PRIVE`.
Le moteur CAO cadgen est épinglé à une révision Git ; son installation nécessite
l’accès à cette source. Sa télémétrie et son daemon sont désactivés par BENCHAB.

Installer séparément Anycubic Slicer Next 2.0.0.3 pour le tranchage natif.
Le chemin est déclaré dans le [profil machine](../../config/kobra-s1.toml).
Blender est facultatif pour les échanges et rendus qui le demandent ; son absence
ne valide pas ces opérations. `uv run benchab conception capacites-locales -- --help` décrit
les capacités locales. Aucun installateur n’est lancé automatiquement.

Voir [fabrication](../reference/fabrication.md) et [sauvegarde des données](../reference/donnees.md).

Le diagnostic `python -m benchab.mesure.environnement` compare passivement les
versions et les révisions Git des paquets géométriques de l’environnement local
à `uv.lock`, sans lancer un autre interpréteur ni installer de paquet. Une
version incompatible donne FAIL ; des métadonnées absentes, malformées ou
doublonnées restent NON VÉRIFIÉES. Ce contrôle ne prouve pas le fonctionnement
du paquet. Sans le verrou du dépôt (par exemple une wheel isolée), il reste
NON VÉRIFIÉ. L’entrée `.venv/bin/benchab` est générée par l’installation uv ;
BENCHAB ne crée pas de lien de commande global dans le dossier personnel.

Pour une source Git, le diagnostic exige l’URL, le type VCS, le commit exact
et le sous-dossier déclarés par les [métadonnées d’origine Python](https://packaging.python.org/en/latest/specifications/direct-url-data-structure/).
Un commit seul ne suffit pas. Un écart de version établi reste FAIL même si
une autre partie de la provenance est inconnue.
