# Diagnostic d'installation

Le module `benchab.qualite.installation.diagnostiquer(racine, ...)` rassemble des contrôles locaux et renvoie un rapport court avec un statut global, un code de sortie conforme à `benchab.qualite.statuts` et une ligne par contrôle. L'option `detail=True` ajoute les chemins, empreintes de profils et éléments de diagnostic. La commande `benchab controle installation` affiche ce rapport et renvoie son code sans ouvrir la base en écriture. Utiliser `--detail` pour afficher les preuves, `--sortie RAPPORT.json` pour conserver le détail dans un nouveau fichier privé, `--profil` pour un fichier à empreinter et `--espace-minimum-mio` pour le seuil d’espace. La racine peut être fournie avec `--racine`.

## Contrôles couverts

- Python courant dans `.venv/bin/python` et conforme à `requires-python` ; lecture TOML de `pyproject.toml` et `uv.lock`, comparaison du projet et de la contrainte Python. La version dynamique Hatch est lue comme littéral sans exécuter le module ; uv peut omettre normalement le numéro du paquet local éditable. Une autre source dynamique reste `NON VÉRIFIÉ`.
- Version du slicer lue par la sonde passive `benchab.fabrication.slicer_runtime.informations`, puis par deux `Info.plist` connus si la configuration ne répond pas. La version locale ne devient PASS que si une liste de versions effectivement testées est fournie à l’appel. Cette preuve documentaire reste séparée de la qualification du binaire, qui demeure `NON VÉRIFIÉ` sans comparaison de SHA bornée. Ces lectures ne lancent pas le slicer.
- Empreinte SHA-256 bornée à 64 Mio et date de modification des profils explicitement fournis ; les liens symboliques sont refusés. Sans empreinte attendue, la capture est `NON VÉRIFIÉ`, pas une conformité. Aucun profil n’est découvert ou modifié par défaut.
- Espace disque libre réel, ou valeur fournie par l'appelant pour un contrôle reproductible. Le seuil est fourni par l'appelant.
- Résultat effectif de `git check-ignore --no-index` sur un fichier privé, avec `GIT_OPTIONAL_LOCKS=0`. Hors checkout ou si Git ne peut conclure, statut `NON VÉRIFIÉ`.
- Identité `APPLICATION_ID`, `user_version` et tables attendues BENCHAB, puis `PRAGMA integrity_check` et `PRAGMA foreign_key_check` sur la base SQLite ouverte en lecture seule.
- Séance active à verrou valide (PASS), séance ouverte orpheline (WARN), ou aucune séance ouverte.

Une absence d'information reste `NON VÉRIFIÉ`. Une version inconnue ou hors de la version documentée ne prouve pas une incompatibilité ; elle reste à examiner. Le contrôle de version dépend des emplacements d'application et d'une référence locale unique, il ne valide pas une version par un lancement. Le contrôle des profils ne porte que sur les chemins transmis. L'espace est seulement comparé au seuil choisi, sans estimer les besoins d'une opération.

## Invariants

Les sondes ouvrent SQLite en lecture seule. Les modes d’affichage conservent ce comportement. Seule l’option `--sortie` publie un rapport de diagnostic neuf. Les valeurs de seuil peuvent être simulées pour tester le rapport.
