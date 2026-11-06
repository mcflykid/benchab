# Feuille de route

Les issues détaillent le périmètre, les tests et les dépendances.

## 1.0.1

Stabilisation du contrôle et de la préparation.

- #1 — Codes de sortie incohérents pour un même statut (WARN, FAIL, NON VÉRIFIÉ, INCONNU).
- #2 — Intégrité 3MF : affectation des objets aux plaques et instances non comparée.
- #3 — U25 : `debit_gcode` plante (StopIteration) sur une plaque sans métadonnée index.
- #4 — Contrôle d'envoi : IndexError sur extrudeur hors vecteur et lecture hors dictionnaire typé.
- #5 — Séance : verrou sans propriétaire ni expiration.
- #6 — Écritures non atomiques : votes, bobines et mesures divergent de SQLite après un échec.
- #7 — Sous-processus sans délai : `controle complet`, `qualite tout`, appels git.
- #8 — G-code : état de l'interpréteur faux après G28 et après changement d'outil (E par outil).
- #9 — U20 : lecture du XML 3MF par expressions régulières trop stricte.
- #10 — U36 : empreintes de `qualite.json["protegees"]` non prises en compte.
- #11 — Frontière : contrôle Git limité au préfixe `prive/`.
- #15 — Tranchage : temporaire orphelin et 3MF partiel laissé sur disque.
- #16 — Décider : `controle regles` jamais à 0 (U24) et écritures privées acceptées sous /tmp.
- #22 — Petits défauts : durée « 1 h 60 », message SystemExit perdu, encodage implicite.
- #25 — 1.0.1 : priorités, dépendances et lots de réalisation.

## 1.1.0

Évolutions de l’atelier et de ses outils de développement.

### P2 — À engager

**Premier lot — fiabilité G-code.** Commencer par #13 pour unifier l’interpréteur,
puis traiter #12 pour unifier le contrôle de débit U25. Rejouer les mêmes témoins
S/M/L aux deux étapes.

- #13 — Chantier : un seul interpréteur G-code.
- #12 — Chantier : un seul contrat de débit U25.
- #17 — Chantier : module commun de chemins et d'écritures atomiques.
- #21 — Hooks : dossier figé à l'import et filtre de commandes contournable.
- #14 — Tests manquants : collecte des mesures.
- #36 — Provenance et licence de chaque modèle.
- #38 — Comparaison des réglages entre 3MF et profil système.
- #48 — Qualifier le tranchage natif des 3MF multi-volumes.
- #49 — Qualifier les 32 archives historiques aux réglages ambigus.
- #51 — Consigner les validations physiques des témoins et assemblages.

### P3 — Ensuite

- #18 — Chantier : répartiteur CLI unique et table des statuts.
- #19 — Chantier : coût de `Base.transaction` proportionnel à la base entière.
- #37 — Annotations du code et contrôle Ruff D.
- #35 — Sorties courtes et rapports complets pour les agents.
- #39 — Savoir communautaire Kobra S1 et cohérence des réglages.
- #40 — Sources et provenance des connaissances communautaires.
- #41 — Avertissements de cohérence pour les réglages 3MF.
- #42 — Traçabilité des 3MF.
- #56 — Hygiène du dépôt et contrôle des secrets en CI.
- #26 — Coutures visibles et règle U31.
- #27 — Bibliothèque de filetages, inserts et écrous.
- #28 — Retour photo assisté et observations par zone.
- #29 — Connecteurs à clip et jeux par matière.
- #30 — Géométrie des goupilles et tenons à clip.
- #31 — Placement des clips dans la visionneuse.
- #32 — Conception déterministe avec fiches JSON.
- #50 — Recetter la visionneuse dans Safari et au pavé tactile.
- #52 — Recetter la lecture Cloud Anycubic avec un compte autorisé.

#20 est fermé comme doublon de #37.

### P4 — À différer

- #23 — Chantier : découper les modules de plus de 900 lignes.
- #33 — Installation optionnelle par OpenSkills.
- #34 — Évaluation du graphe de code pour les tests d’impact.

## 1.2.0

Sujets à cadrer avant réalisation.

### P4 — À différer

- #24 — Idées 1.2 : deuxième imprimante, rendu Metal, portabilité.
- #43 — Conversion de projets 3MF entre slicers.
- #44 — État et caméra en lecture seule.
- #45 — Export de profils utilisateur à importer manuellement.

## Règles communes

- Conserver les projets, photos et modèles tiers dans l’atelier privé.
- Vérifier la suite complète, la frontière de publication et les témoins S/M/L concernés.
- Passer par le lanceur commun et contrôler les réglages avant et après toute transformation 3MF.
- Distinguer contrôle numérique, observation et validation physique.
- Vérifier les licences avant toute reprise tierce et les inscrire dans TIERS.md. L’AGPL est exclue.
- Répéter les mesures avant d’annoncer un gain (U65).

## Jamais

- #46 — Envoi, lancement, pause ou arrêt d’une impression depuis BENCHAB.
- Publication de fichiers de projet, de modèles tiers ou de données personnelles.
- Écriture dans les profils installés par le slicer.
- Code de pilotage LAN ou d’intégration Rinkhals et OrcaSlicer. Les connaissances restent documentées.
- Installation de l’atelier à la carte.

## Versions testées

| Composant | Version | Vérification |
| --- | --- | --- |
| Anycubic Slicer Next | 2.0.0.3 | Lanceur CLI et témoins numériques |
| macOS | 27.2 (26B123) | Installation et suite complète sur Apple Silicon |

Les versions testées décrivent le périmètre observé.
