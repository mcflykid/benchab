# Architecture de BENCHAB 1.0

Décision du 4 novembre 2026, lot 1.

Le paquet `benchab` contient les outils communs. SQLite contient les données de
l'utilisateur. Un export JSONL transactionnel permet de reconstruire ces données.
Les projets ne portent pas de copie locale des outils communs.

## Schéma des données

Le schéma exécutable est `benchab/donnees/schema.sql`. Les tests l'appliquent dans
une base temporaire et vérifient ses contraintes, y compris les clés étrangères.
Les identifiants sont des chaînes stables ; les chemins personnels et les fichiers
importés restent dans la base privée. Les dates enregistrées portent leur fuseau.

| Table | Rôle et rattachement |
| --- | --- |
| projets | Une fiche par projet, y compris les projets historiques. |
| preparations | Réglages et résultat figés d'un projet ; nom unique dans ce projet. |
| impressions | Exécution déclarée par l'utilisateur, liée à une préparation. |
| retours | Observation sourcée liée à une impression ; aucune validation physique implicite. |
| filaments | Matière et recommandations du fabricant, séparées des réglages embarqués. |
| bobines | Inventaire personnel, lié à un filament ; les masses inconnues restent inconnues. |
| profils | Profil et origine, avec empreinte ; aucun profil installé n'est modifié. |
| connaissances | Fait, source datée, catégorie de preuve et choix explicite de visibilité. |
| decisions | Décision sourcée, éventuellement propre à un projet. |
| votes | Avis et preuves d'une préparation, conservés avec la version de la grille. |
| mesures | Durée, CPU, mémoire, commande et résultat ; rattachement facultatif à une séance. |
| seances | Début, fin, lot, candidates, temps de vote et de contrôle. |
| versions | Évolution datée, utilisée pour générer le journal des versions. |

Les documents historiques sont importés avec leurs sources et leur contenu
structuré dans `donnees_json`. Ces champs préservent les informations dont le
modèle fin n'est pas encore arrêté. Ils ne remplacent pas les relations utiles :
projet → préparation → impression → retour, et filament → bobine.

La suppression d'un parent référencé est refusée. Une donnée absente ne devient
jamais zéro. Les preuves numériques et les observations physiques sont distinctes.
Les changements de schéma porteront une version via `PRAGMA user_version`.

## Découpage du paquet

| Sous-paquet | Responsabilité |
| --- | --- |
| conception | Géométrie paramétrique, assemblages et transformations. |
| controles | Contrôles numériques et règles mécaniques vérifiables. |
| fabrication | Production et tranchage des 3MF avec configuration temporaire. |
| gcode | Lecture partagée, cache par empreinte du 3MF tranché. |
| `imports3d` | Modèles importés, provenance et protection des originaux. |
| machine | Configuration ; lecture Cloud testée uniquement avec des réponses simulées. |
| visionneuse | Visualisation locale, port configurable 8771. |
| connaissances | Recherche de faits sourcés et distinction des niveaux de preuve. |
| donnees | SQLite, migrations, exports JSONL et reconstruction. |
| vote | Grille, règles de blocage et avis automatiques déterministes. |
| mesure | Collecte centralisée de durée, CPU et mémoire. |
| qualite | Publication, licences, liens, typographie et frontière privée. |

`cli.py` est l'entrée commune. Les dépendances vont des commandes vers les outils
et vers les données ; un outil ne lance pas une autre commande de la CLI pour
réutiliser son calcul. Les hooks d'intégration invoquent l'entrée mesurée dans un
processus séparé. Le nom historique `visualisation-3d` devient `benchab.visualisation`.

Les associations de fichiers aux préparations sont des événements techniques
typés dans `decisions.donnees_json`, liés au projet et à l'identifiant de préparation.
La vue de préparation joint ces événements au snapshot initial, qui reste intact.
Une association n'ajoute pas d'impression ; démarrage et résultat physique restent
inconnus tant qu'une observation explicite n'est pas enregistrée.

## Périmètre du lot

Ce lot fixe le schéma et les responsabilités. Les migrations de contenu, l'export
automatique et les commandes métier sont réalisés dans leurs lots respectifs.
