# Plan numérique de filament

Le premier lot #77 fournit l’API Python en lecture seule `benchab.projets.plan_consommation.estimer_retour` et la commande `benchab --prive CHEMIN controle consommation-retour --retour ID`. La commande ouvre uniquement la base BENCHAB existante en lecture seule et produit un document JSON sur stdout, avec le code 2 tant que la consommation physique reste NON VÉRIFIÉE.

L’API et la commande vérifient les liens déjà enregistrés entre un retour, son impression, la préparation identifiée par SHA-256 et un unique fichier 3MF envoyé, lui aussi identifié par SHA-256. Elles lisent ensuite l’estimation de filament du G-code **par plaque et par index G-code**, en grammes. Les plaques doivent être numérotées à partir de 1 ; tout index d’outil hors du vecteur de masses rend l’estimation absente.

Le résultat reste toujours `NON VÉRIFIÉ`. Quand les sources sont complètes, le champ `estimation` indique explicitement `ESTIMATION_SLICER`. Un retour interrompu, une portée différente de l’ensemble, des liens absents ou ambigus, une fiche altérée ou un envoi dont le SHA diverge ne produisent pas d’estimation.

Le champ `indice_repere_dans_envoi` indique seulement que l’index figure parmi les indices `filaments_utilises` du lecteur. Cette liste peut être alimentée par une commande `T` ou par une masse positive, même sans commande `T` explicite ; le champ ne prouve donc pas une sélection physique d’outil.

Ce plan n’écrit pas dans SQLite et ne change aucune masse de bobine. La commande refuse les bases WAL ou non identifiées et garde les lectures dans un snapshot transactionnel à délai borné. Elle ne crée pas d’atelier absent et n’instancie ni Base applicative ni mesure.

Le résultat n’établit pas que le 3MF envoyé est celui exécuté, ne relie pas un index G-code à un outil physique, à un emplacement ACE ou à une bobine, et ne transforme pas les grammes estimés en consommation réelle. Le suivi de ces preuves reste ouvert dans #77.

Une étape ultérieure devra exiger une affectation explicitement confirmée et une mesure physique de la bobine. Toute future imputation devra être idempotente et enregistrer sa preuve dans la même transaction que la mise à jour du stock.
