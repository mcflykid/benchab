# Chemins et atelier privé

Les commandes utilisent un seul atelier : `--prive CHEMIN` lorsqu'il est fourni,
sinon `BENCHAB_PRIVE`, sinon `prive/` à la racine du checkout BENCHAB. Le défaut
ne dépend jamais du dossier courant. Un chemin relatif fourni explicitement
par l'utilisateur est interprété depuis son dossier courant. Hors checkout,
indiquer un atelier explicite.

`benchab --help` et `benchab --version` analysent les arguments sans ouvrir de
base, créer de fichier, écrire de mesure ou prendre un verrou. L'aide déléguée
est descriptive : elle lit les déclarations d'options, sans appeler l'outil.
Pour un atelier courant absent, seule `benchab init` l'initialise ; les autres
commandes refusent et indiquent cette initialisation. Une destination de
restauration ou de reconstruction reste un résultat explicitement demandé,
pas l'atelier courant. Un argument invalide ne crée rien.

La garde commune `benchab.chemins.sans_lien` contrôle chaque composant situé
sous la racine de confiance, avant de réduire les `..`. Un lien interne reste
refusé, même si le chemin revient ensuite par `..`. La racine elle-même ne
peut pas être un lien. Les alias système attestés de macOS (`/tmp` et `/var`)
sont normalisés vers leurs cibles système ; ils n'autorisent aucun lien placé
dans le projet ou dans sa destination. Avec une racine explicite, ses parents
sont résolus sans imposer de politique aux dossiers système qui la précèdent.

Les sorties temporaires sont reconnues par le même module, y compris le
temporaire natif macOS. La CI ne force pas `TMPDIR` pour masquer un refus
incorrect. Chaque outil garde son contrôle de périmètre : une garde anti-lien
réussie ne donne pas le droit d'écrire ailleurs ni de modifier un réglage
ou une préparation figée.

Sources : [résolution et garde communes](../../benchab/chemins.py),
[entrée CLI](../../benchab/cli.py), [base canonique](../../benchab/donnees/base.py),
[tests de non-régression](../../tests/test_chemins_atelier.py).
