# Relier un incident à son test de régression

`benchab connaissances incidents prive/rapports/declaration.json --depot . --sortie prive/rapports/fiche.json`
relie une déclaration privée à un commit local exact et aux octets de fichiers
de test réguliers de son arbre. La déclaration et la sortie sont privées ;
le chemin de sortie est neuf. Sans sortie, la commande affiche la fiche.

Une observation CI GitHub est facultative et ne démarre aucun accès réseau sans
option explicite : `--ci-github ID/TENTATIVE`. Le dépôt est dérivé de l’`origin`
du checkout fourni avec `--depot` ; seuls les formats HTTPS et SSH de GitHub
sans identifiants sont acceptés. La commande fait des GET publics sans jeton,
sans redirection, avec un délai de 5 secondes et une limite de 256 Kio par
réponse, sans conserver les corps bruts. Pour la tentative, les chemins API sont
`/repos/{OWNER}/{REPO}/actions/runs/{ID}/attempts/{TENTATIVE}` et son suffixe
`/jobs?per_page=20`.
La vérification demande un dépôt source identique à l’origin, la même empreinte
de tête que `revision`, un événement `push` sur `main`, le workflow
`.github/workflows/tests.yml`, un run et une tentative concordants, un unique
job `tests` réussi et son étape `Run uv run pytest -q` réussie.

Le rapport conserve uniquement les champs normalisés sous `ci_github`, avec le
statut `OBSERVATION CI PUBLIQUEMENT VÉRIFIÉE` ou `NON VÉRIFIÉ`. Une réponse
indisponible ou une divergence reste `NON VÉRIFIÉ`. Cette observation ne
modifie jamais `execution.statut`, n’authentifie ni le cas JUnit ni l’identité
de l’exécuteur, et ne prouve pas l’exécution locale de la fiche.

## Relier les cas à l’artefact de la tentative

La CI publie un fichier `benchab-junit-TENTATIVE.xml` après un passage réussi
du workflow sur `main`. Ce fichier est conservé quatorze jours et exclut
les sorties capturées des tests. Récupérer le XML depuis les artefacts de
la tentative, le conserver sous l’atelier privé et le déclarer comme
`preuve_execution` avec son SHA et les cas attendus. Ajouter
`--ci-github ID/TENTATIVE --artefact-github ID_ARTEFACT` à la commande.

La fiche vérifie aussi le succès de l’étape de publication. Elle lit
uniquement les métadonnées publiques de l’artefact et rapproche son
identifiant, son nom, son run, sa révision, sa taille et son SHA-256.
La requête vise `/repos/{OWNER}/{REPO}/actions/artifacts/{ID_ARTEFACT}`.
Si les octets JUnit concordent, `ci_github.preuve_cas` rapporte le résultat
des cas de cette tentative avec le statut `ARTEFACT JUNIT CI VÉRIFIÉ`.
Un artefact absent, expiré ou différent reste NON VÉRIFIÉ. Cette option
ne télécharge aucun fichier et ne modifie pas le statut de l’exécution locale.

## Déclaration de l’incident

La déclaration JSON de schéma 1 contient :

- `issue` : numéro positif de l’issue BENCHAB ;
- `revision` : empreinte Git complète du commit, sans nom de branche ;
- `symptome`, `conditions` et `limites` : un texte et deux listes non vides ;
- `tests` : de un à huit chemins réguliers `tests/.../test_*.py` ;
- facultativement `preuve_execution` : `chemin` privé relatif et `sha256` attendu
  d’un rapport existant. Le rapport est relu et son empreinte comparée.
  Par défaut son contenu reste opaque. Pour une analyse explicitement opt-in,
  ajouter `format: "junit"` et `cas_attendus`, une liste de 1 à 32 identités
  exactes `{ "chemin": "tests/test_x.py", "classe": "tests.test_x", "nom": "test_cas" }`.
  Chaque chemin doit aussi figurer dans `tests`. Le XML `testsuite`/`testsuites`
  est borné à 8 Mio ; son SHA et son analyse portent sur la même lecture protégée
  contre le remplacement du fichier. DTD, entités, identités dupliquées et champs
  ambigus sont refusés. Le rapport indique les cas absents, en échec ou ignorés.
  Pour obtenir `file` avec pytest, exporter en famille `xunit1`, par exemple
  `python -m pytest tests/test_fiche_incident.py -o junit_family=xunit1 --junitxml=prive/rapports/tests.xml`.
  Les rapports pytest par défaut peuvent omettre `file` ; ces cas ne sont pas
  rapprochés par `classname` seul et sont rapportés comme manquants.

Le statut PASS du lien indique que les références sont démontrées. Il ne dit
pas que les tests ont été lancés, que l’issue est rattachée au commit sur
GitHub ou qu’une impression a été validée. L’exécution demeure NON VÉRIFIÉE et attribuée au rapport déclaré.
Même lorsque le contenu JUnit rapporte tous les cas déclarés sans échec, ce
résultat décrit uniquement les octets du rapport : l’exécution demeure
NON VÉRIFIÉE et aucune identité d’exécuteur, révision de lancement ou validité
de la production du rapport n’est authentifiée. Les rapports sans `format`
restent opaques et conservent le traitement historique.
La commande ne télécharge pas d’objet Git, ne modifie ni code, ni réglage, ni
projet et n’ajoute pas automatiquement de connaissance à SQLite.
