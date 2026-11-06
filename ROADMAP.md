# Feuille de route

Les issues détaillent le périmètre, les tests et les dépendances.
P1 : risque critique avéré à corriger en premier ; P2 : garde-fous matériels,
de données ou de validation ; P3 : évolution ciblée ; P4 : confort ou étude à
différer. À priorité égale, traiter d’abord les prérequis communs et les
travaux qui permettent de vérifier les suivants.

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

## 1.0.2

Correctifs de la revue du 6 novembre et cohérence de la livraison.

- P1 — #58 : lecture typée des réglages du 3MF dans l’audit ACE.
- P2 — #59 : délais bornés et verdict NON VÉRIFIÉ (suite de #7).
- P2 — #60 : budget d’espace proportionnel et tests de sauvegarde indépendants du disque.
- P4 — #70 : README et version du paquet cohérents, noms des tests par domaine.

## 1.1.0

Évolutions de l’atelier et de ses outils de développement.

### P2 — À engager

**Premier lot — fiabilité G-code.** Commencer par #13 pour unifier l’interpréteur,
puis traiter #12 pour unifier le contrôle de débit U25. Rejouer les mêmes témoins
S/M/L aux deux étapes.

- #13 — Interpréteur G-code commun, caches concurrents et découpage du lecteur.
- #12 — Chantier : un seul contrat de débit U25.
- #17 — Chemins et écritures atomiques, dont le temporaire système commun ; prépare #24.
- #64 — Lecteur 3MF borné : ZIP, XML et réglages typés ; coordonner avec #17 et #42.
- #65 — Réseau externe bloqué par défaut dans les tests ; recette explicite avec #52.
- #71 — Contrôle d’envoi : compatibilité ACE, dureté et diamètre de buse, en-tête et démarrage.
- #76 — Diagnostic d’installation en lecture seule ; sortie courte coordonnée avec #35.
- #21 — Hooks : dossier figé à l'import et filtre de commandes contournable.
- #14 — Tests manquants : collecte des mesures.
- #36 — Provenance et licence de chaque modèle.
- #38 — Comparaison des réglages entre 3MF et profil système.
- #48 — Qualifier le tranchage natif des 3MF multi-volumes.
- #49 — Qualifier les 32 archives historiques aux réglages ambigus.
- #51 — Consigner les validations physiques des témoins et assemblages.

### P3 — Ensuite

- #18 — Répartiteur CLI, table des statuts et exceptions de la bibliothèque.
- #19 — Chantier : coût de `Base.transaction` proportionnel à la base entière.
- #37 — Annotations du code et contrôle Ruff D.
- #35 — Sorties courtes, rapports complets et manifeste de connaissances pour les agents.
- #39 — Savoir communautaire Kobra S1, corpus d’écarts de profils et cohérence des réglages.
- #40 — Sources, codes d’erreur et pièges d’usage ; rattacher la lecture Cloud à #52.
- #41 — Avertissements de cohérence pour les réglages 3MF.
- #42 — Traçabilité des 3MF.
- #56 — Hygiène et secrets en CI, sondes Git et empreintes des vérificateurs ; UTF-8, Ruff, format, délais et mises à jour.
  Coordonner avec #37.
- #66 — Validation des POST de la visionneuse ; couvrir la recette #50.
- #67 — Gabarits HTML/JS de la visionneuse ; comparer le rendu avec la recette #50.
- #26 — Coutures visibles et règle U31.
- #27 — Filetages, inserts, écrous et contrôle des pauses d’insertion avec #13.
- #28 — Retour photo assisté et observations par zone ; schéma commun à concevoir avec #44.
- #29 — Connecteurs à clip et jeux par matière.
- #30 — Géométrie des goupilles et tenons à clip.
- #31 — Placement des clips dans la visionneuse.
- #32 — Conception déterministe avec fiches JSON.
- #50 — Recetter la visionneuse dans Safari et au pavé tactile.
- #52 — Recetter le Cloud Anycubic en lecture seule, seul transport réseau retenu ;
  voir la [décision Cloud](docs/decisions/2026-11-06-cloud-lecture-seule.md).
- #43 — Conversion 3MF par familles KEEP / CLAMP / REPLACE ; après #36, #38 et #64.
- #72 — Bilan de purge par paire, après #13.

#20 est fermé comme doublon de #37.

### P4 — À différer

- #23 — Découper les gros modules, dont l’analyseur des capacités machine.
- #33 — Installation optionnelle par OpenSkills.
- #34 — Évaluation du graphe de code pour les tests d’impact.
- #74 — Parcours des premiers essais, de la nouvelle machine ou bobine jusqu’au retour.

## 1.2.0

Sujets à cadrer avant réalisation.

### P3 — Données d’usage

- #73 — Échéancier d’entretien par heures et bobines déclarées.
- #77 — Consommation par bobine et par outil ACE, avec imputation unique après retour confirmé.

### P4 — À différer

- #24 — Idées 1.2 : deuxième imprimante, rendu Metal, portabilité après le socle de #17.
- #68 — Découper les contrôles de finition et d’affectation ACE, après #58.
- #69 — Typage progressif des modules communs, après #13 et #17.
- #44 — État et images déjà disponibles en lecture seule, après le schéma commun #28.
  Déclencher une capture est une écriture. Maillage de plateau conditionné à une source Cloud en lecture seule.
- #45 — Export de profils utilisateur à importer manuellement, après #40.
- #75 — Vitrine statique du corpus public, après #40 et #56.

## Lots à coordonner

- **G-code** : garder #13 puis #12. Réutiliser l’interpréteur pour #72 et les pauses de #27 ;
  garder les rapports de purge et de débit séparés, car leurs verdicts répondent à des questions différentes.
- **3MF et envoi** : #64 porte le lecteur borné et le corpus privé de robustesse ; #38 porte la comparaison typée.
  #42 garde la traçabilité, #43 la conversion et #71 les verdicts d’envoi. Ces étapes partagent les fixtures,
  mais chaque transformation ou verdict garde ses propres critères.
- **Connaissances et CI** : #39 coordonne #40 et #41, #35 sert le manifeste. #56 et #37 partagent la
  configuration Ruff ; la sécurité et les annotations restent des livraisons distinctes.
- **Observations** : concevoir ensemble le schéma #28/#44, sans faire dépendre le retour photo d’une caméra.
  #73 et #77 partagent les événements d’impression, avec des sorties et des règles d’écriture distinctes.
- **Chemins et hooks** : #17 fournit les contrôles réutilisables par #21 ; garder la politique des hooks dans #21.

## Règles communes

- Conserver les projets, photos et modèles tiers dans l’atelier privé.
- Vérifier la suite complète, la frontière de publication et les témoins S/M/L concernés.
- Passer par le lanceur commun et contrôler les réglages avant et après toute transformation 3MF.
- Distinguer contrôle numérique, observation et validation physique.
- Vérifier les licences avant toute reprise tierce et les inscrire dans TIERS.md au développement. L’AGPL est exclue.
- Entrer chaque fait technique communautaire comme hypothèse, avec sa source et sa portée ; le vérifier avant
  d’en faire un verdict. Sans licence, re-sourcer idées et faits sans copier. Les 3MF/G-code tiers restent privés.
- Répéter les mesures avant d’annoncer un gain (U65).

## Jamais

- #46 — Envoi, lancement, pause ou arrêt d’une impression depuis BENCHAB.
- Publication de fichiers de projet, de modèles tiers ou de données personnelles.
- Écriture dans les profils installés par le slicer.
- Pilotage par LAN, Cloud ou autre transport. La lecture réseau reste limitée au Cloud Anycubic.
- Client LAN, même en lecture seule : piste écartée (#78), voir la [décision Cloud](docs/decisions/2026-11-06-cloud-lecture-seule.md).
- Intégration Rinkhals, Moonraker ou OrcaSlicer. Les connaissances restent documentées.
- Installation de l’atelier à la carte.

## Versions testées

| Composant | Version | Vérification |
| --- | --- | --- |
| Anycubic Slicer Next | 2.0.0.3 | Lanceur CLI et témoins numériques |
| macOS | 27.2 (26B123) | Installation et suite complète sur Apple Silicon |

Les versions testées décrivent le périmètre observé.
