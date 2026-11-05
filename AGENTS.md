# BENCHAB

- Répondre en français, vérifier avant d'affirmer et citer la commande utilisée.
- Rester dans le périmètre demandé ; noter séparément les idées pour plus tard.
- Utiliser `benchab/` pour les projets et ajouter un module commun avec son test pour tout besoin partagé.
- Lire l'état du projet concerné et limiter les lectures au besoin présent.
- Contrôler ce qui change ; réserver l'audit complet à la candidate figée avant envoi et après retouche.
- Démarrer chaque séance par `benchab seance debut` pour prendre le verrou de session.
- Un contrôle numérique ne prouve pas la tenue physique d'une pièce.
- Distinguer faits fabricant, réglages embarqués, contrôles numériques, observations utilisateur et validations physiques.
- Utiliser un seul Python du dépôt et réutiliser les caches communs indexés par empreinte.
- Ne pas imprimer, chauffer, déplacer ou calibrer sans demande explicite.
- Ne jamais modifier les profils installés ; conserver originaux, preuves et préparations figées.
- Toutes les données projet et préférences personnelles restent dans `prive/`.
- Les règles vérifiables sont reliées à leurs contrôles dans [la référence des règles](docs/reference/regles.md).
- U20 : `benchab controle plaques-interface` ; U22 : `benchab controle regles`.
- U25 : `benchab controle debit-gcode` ; les plafonds proviennent des réglages déclarés.
- U36 : `fabrication plaques-mixtes` filtre les pièces protégées par nom, chemin et SHA ; sans contexte projet, laisser NON VÉRIFIÉ.
- Opportunité de découpe : `benchab controle opportunite-decoupe` vérifie des indicateurs sourcés et une comparaison ; il ne détecte ni ne génère une coupe.
- U21 ne fixe aucune vitesse universelle ; ne pas transformer une valeur observée en plafond.
- Avant livraison d'un outil commun, exécuter sa suite et les témoins S/M/L concernés.
- Au plus trois candidates ; au-delà, retenir la meilleure et chiffrer les risques restants.
- Avant chaque vote de préparation : relecture courte par un sous-agent GROS sans historique ; si unanimité ou contrôles tous PASS, prémortem à trois scénarios et surveillance dans le dossier d'envoi. Voir `docs/reference/vote.md`.
- Après chaque étape importante, ajouter une note courte dans le journal de séance de BENCHAB.
- Regrouper la paperasse, l’état et les documents générés en fin de séance.

## Annotations du code

- Tout code nouveau ou modifié porte des annotations courtes en français.
- Chaque fonction et classe publique a une docstring : rôle, entrées et sorties
  avec leurs unités (mm, s, %), statuts renvoyés (PASS, WARN, FAIL, NON VÉRIFIÉ)
  lorsqu’ils s’appliquent, et invariants, par exemple « ne modifie aucun réglage
  d’impression ».
- Les commentaires expliquent pourquoi : règle U, fait physique, piège du slicer
  ou décision de l’auteur, avec le lien vers la documentation concernée.
  Ils ne paraphrasent jamais le code.
- Modifier du code implique de mettre ses annotations à jour dans le même changement.
- La relecture sans historique de fin de lot vérifie les annotations du nouveau
  code. Le code antérieur ne fait pas l’objet d’un rattrapage dans la 1.0 ; les
  règles D de Ruff restent désactivées jusqu’au chantier de contrôle prévu en 1.1.

- Aucun code ne lit, ne normalise ni n’écrit un réglage du slicer sans passer par
  le dictionnaire typé des réglages. Toute transformation d’un 3MF déclare ses
  différences permises et passe le contrôle d’intégrité avant/après.
