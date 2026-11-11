# Relier un incident à son test de régression

`benchab connaissances incidents prive/rapports/declaration.json --depot . --sortie prive/rapports/fiche.json`
relie une déclaration privée à un commit local exact et aux octets de fichiers
de test réguliers de son arbre. La déclaration et la sortie sont privées ;
le chemin de sortie est neuf. Sans sortie, la commande affiche la fiche.

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
  Pour obtenir `file` avec pytest, exporter en famille xunit1, par exemple
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
