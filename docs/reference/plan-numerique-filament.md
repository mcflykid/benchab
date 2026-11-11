# Plan numérique de filament

Le premier lot #77 fournit l’API Python en lecture seule `benchab.projets.plan_consommation.estimer_retour` et la commande `benchab --prive CHEMIN controle consommation-retour --retour ID`. La commande ouvre uniquement la base BENCHAB existante en lecture seule et produit un document JSON sur stdout, avec le code 2 tant que la consommation physique reste NON VÉRIFIÉE.

L’API et la commande vérifient les liens déjà enregistrés entre un retour, son impression, la préparation identifiée par SHA-256 et un unique fichier 3MF envoyé, lui aussi identifié par SHA-256. Elles lisent ensuite l’estimation de filament du G-code **par plaque et par index G-code**, en grammes. Les plaques doivent être numérotées à partir de 1 ; tout index d’outil hors du vecteur de masses rend l’estimation absente.

Le résultat reste toujours `NON VÉRIFIÉ`. Quand les sources sont complètes, le champ `estimation` indique explicitement `ESTIMATION_SLICER`. Un retour interrompu, une portée différente de l’ensemble, des liens absents ou ambigus, une fiche altérée ou un envoi dont le SHA diverge ne produisent pas d’estimation.

Le champ `indice_repere_dans_envoi` indique seulement que l’index figure parmi les indices `filaments_utilises` du lecteur. Cette liste peut être alimentée par une commande `T` ou par une masse positive, même sans commande `T` explicite ; le champ ne prouve donc pas une sélection physique d’outil.

Ce plan n’écrit pas dans SQLite et ne change aucune masse de bobine. La
commande refuse les bases WAL ou non identifiées et lit les relations dans
une transaction de lecture. L’attente d’un verrou SQLite est limitée à une
seconde ; le calcul des empreintes et la lecture de l’archive suivent leurs
propres budgets de lecture. La commande ne crée pas d’atelier absent et ne
lance aucune mesure d’usage.

Le résultat n’établit pas que le 3MF envoyé est celui exécuté, ne relie pas un index G-code à un outil physique, à un emplacement ACE ou à une bobine, et ne transforme pas les grammes estimés en consommation réelle. Le suivi de ces preuves reste ouvert dans #77.

Une imputation ultérieure devra exiger une affectation confirmée et une mesure physique de la bobine. Toute future imputation devra être idempotente et enregistrer sa preuve dans la même transaction que la mise à jour du stock.

## Rapprocher une déclaration de pesée

Ajouter `--preuve-pesee FICHIER --sha256-preuve SHA256` pour relire un JSON
privé de 64 Kio au plus, au schéma 1. Il contient exactement :

- `schema`, `retour_id`, `envoi_sha256`, `plaque`, `membre_gcode`,
  `gcode_sha256` et `index_gcode` ;
- `emplacement_declare`, `bobine_id`, `masse_avant_g` et `masse_apres_g`.

L’envoi doit être celui du plan pour un retour complet réussi. Le membre
relu doit correspondre au numéro de plaque, à son SHA exact et comporter
une sélection `Tn` explicite de l’index déclaré. Une masse estimée positive
ou un commentaire ne remplace pas cette sélection. La bobine doit exister
dans l’atelier ; sa présence n’atteste pas son emplacement physique. Les
masses sont déclarées en grammes, avec une valeur après qui ne dépasse pas
celle d’avant.

`DÉCLARATION LIÉE` signifie que les fichiers et identifiants concordent.
La différence entre les masses déclarées est présentée séparément de
l’estimation du slicer. Aucune pesée, affectation physique ou exécution
n’est authentifiée, et le stock ne change pas. Le statut global et le code
restent NON VÉRIFIÉ et 2. Sans les deux options, le rapport historique est
conservé (#77).


Pour un affichage court, ajouter `--resume --sortie prive/rapports/consommation.json`
avec un nom JSON neuf dans l’atelier privé. Le résumé garde le statut, le code,
les motifs de refus, le nombre de plaques estimées et le chemin du rapport.
La commande écrit le JSON intégral avant d’afficher le résumé. Sans `--resume`,
elle affiche le JSON intégral, avec ou sans copie demandée via `--sortie`.
Un rapport existant est refusé ; le statut NON VÉRIFIÉ et le code 2 ne changent pas.
