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

Le statut PASS du lien indique que les références sont démontrées. Il ne dit
pas que les tests ont été lancés, que l’issue est rattachée au commit sur
GitHub ou qu’une impression a été validée. L’exécution demeure NON VÉRIFIÉE et attribuée au rapport déclaré.
La commande ne télécharge pas d’objet Git, ne modifie ni code, ni réglage, ni
projet et n’ajoute pas automatiquement de connaissance à SQLite.
