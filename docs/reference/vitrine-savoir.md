# Vitrine locale du savoir public

Depuis un checkout BENCHAB, générer un fichier HTML neuf dans l’atelier privé
ou dans un répertoire temporaire système hors du dépôt :

```sh
uv run python -m benchab.connaissances.vitrine --racine . --sortie prive/vitrine-savoir.html
```

La vitrine est autonome : aucun JavaScript, service distant ou fichier de
projet n’est nécessaire pour la lire. L’index propose matières, réglages,
défauts, entretien et codes. Les sujets sont des repères pour la lecture :
une fiche peut apparaître dans plusieurs rubriques. Sa source, son statut et
sa portée restent affichés.

La génération accepte uniquement le corpus public canonique du checkout. Elle
compare les octets au blob de `HEAD`, refuse les fiches privées et exécute le
[contrôle de frontière](../decisions/2026-11-04-frontiere-public-prive.md).
Le résultat donne le nombre de fiches et les SHA-256 du corpus et du HTML.
À corpus et code identiques, les octets du HTML sont identiques.

La commande prépare un aperçu. Une mise en ligne, par exemple via GitHub
Pages, reste une opération distincte, après relecture de l’artefact et contrôle
de publication. Aucun déploiement ou workflow de publication n’est activé.

Ajouter `--manifeste prive/savoir.json` pour conserver un index JSON avec
la date de chaque source, la portée, le statut et l’empreinte du corpus.
Le champ `page` désigne un chemin sous `docs/` versionné dans le dépôt. Une
référence documentaire absente de l’arbre Git donne `null` dans l’index.
Deux générations depuis le même arbre produisent les mêmes octets.
