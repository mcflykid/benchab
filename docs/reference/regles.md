# Règles et contrôles

Cette page relie les règles opératoires aux contrôles réellement disponibles. Un résultat s'applique uniquement aux fichiers et paramètres vérifiés ; aucun contrôle numérique ne vaut validation physique.

| Règle | Contrôle | Limite |
| --- | --- | --- |
| U20 — pas d'interface entre plaques | `benchab controle plaques-interface` (`benchab.controles.plaques_interface`) | Lit les pas d'interface et de commande dans `config/kobra-s1.toml`. |
| U21 — vitesse choisie selon le procédé | `benchab controle debit-gcode` mesure le débit nominal par objet et outil | Il n'existe pas de plafond universel de vitesse ; la dynamique réelle n'est pas simulée. |
| U22 — noms lisibles et uniques | `benchab controle regles` inspecte le 3MF enregistré | La réouverture dans l'interface graphique reste à vérifier par l'opérateur. |
| U24 — mouvement causé par un contact mécanique | Aucun contrôle actuel ne prouve cette causalité ; sans preuve mécanique, statut `NON VÉRIFIÉ` | `contact_support` ne mesure que la projection géométrique des trajets FDM sous les supports. Le port du gabarit est prévu au lot 6. |
| U25 — filament et débit | `benchab controle debit-gcode` et `benchab controle envoi` | Le débit est comparé au plafond porté par le 3MF ou à une déclaration explicite. |
| U36 — pièces protégées | `plaques_mixtes.pieces_projet` filtre par nom, chemin et SHA via `benchab.projets.protections` ; voir `tests/test_plaques_mixtes.py` | L'agrégateur sans contexte projet laisse U36 `NON VÉRIFIÉ`. Le helper seul ne prouve pas qu'un slicer externe écartera une pièce. |
| U49–U51 — vues README et catalogue | Vues générées depuis Base prévues au lot 5 | `benchab.qualite.tout` n'est pas le générateur de ces vues ; aucune génération n'est revendiquée au lot 4. |
| U61 — témoins avant livraison d'un outil commun | Suite de tests et témoins S/M/L concernés avant livraison | Le gabarit est prévu au lot 6 ; la chaîne native finale est prévue au lot 8. Aucun PASS de ces jalons n'est affirmé au lot 4. |
| U60 — sources et empreintes | Qualifier les preuves, modifier la source structurée et relire avant renouvellement d'empreinte ; vérifier la cohérence avant génération | Valeurs issues de sections générées ou de renvois, couverture des fichiers et dépendances explicite. Les vues depuis Base sont prévues au lot 5 ; une source inaccessible reste NON VÉRIFIÉE. |
| U64 — réutilisation des résultats | `benchab.gcode.lecture` et caches des contrôles réutilisent des résultats par empreinte | Garder un seul interpréteur Python ; étendre les caches communs plutôt que créer un second système. |

Le contrôle agrégé `benchab controle regles` lance U20, U22 et U25 sur le 3MF fourni. Si le G-code ou un plafond mesurable manque, U25 reste `NON VÉRIFIÉ`. U21 ne produit aucun seuil implicite. U24 reste `NON VÉRIFIÉ` sans preuve mécanique de la causalité du mouvement. U36 reste `NON VÉRIFIÉ` dans cet agrégateur sans projet ; le filtrage nom/chemin/SHA est prouvé seulement pour le flux de plaques mixtes.

Le contrôle `benchab controle opportunite-decoupe` consomme un manifeste privé qui référence, par chemin relatif et SHA-256, la pièce candidate et deux rapports structurés `PASS` liés à son empreinte (`source_sha256`) : forme (`retrecissement_naturel`, `volumes_fusionnes`) et imprimabilité (`supports`, `surplombs`, `couture_visible_ou_fonctionnelle`, `depasse_plateau`). Si un indicateur est vrai, il exige une comparaison entre le monobloc et au moins une variante découpée sur les sept axes, avec au moins deux fichiers de pièce présents, empreintés et déclarés comme issus de la même source. L'estimation de réussite reste qualitative et non mesurée ; le choix final reste humain. Ces contrôles valident la déclaration et les empreintes, pas la géométrie ni la dérivation des fichiers. Ils ne détectent aucune forme ou couture, et ne génèrent ni ne découpent de variante ; la génération et sa validation géométrique relèvent du lot 6B.

La grille complète U01–U68, son sort, sa destination et sa justification sont conservés dans le registre privé de reprise. Les règles et faits personnels sont gardés dans `prive/config/regles-personnelles.json`.

U20 lit les éléments XML des modèles et les liaisons de plaque, quel que soit
l’ordre des attributs. Un `transform` absent représente l’identité. Un XML
illisible laisse U20 NON VÉRIFIÉ avec le motif de lecture.

U36 accepte dans `qualite.json` un objet `protegees` indexé par nom ou chemin.
Une valeur SHA-256 de 64 caractères hexadécimaux protège aussi la copie
renommée. Le format structuré avec `fichier` et `sha256` reste lisible.
