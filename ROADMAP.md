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

## 1.0.2

Correctifs de la revue du 6 novembre et cohérence de la livraison.

- P1 — #58 : lecture typée des réglages du 3MF dans l’audit ACE.
- P2 — #59 : délais bornés et verdict NON VÉRIFIÉ (suite de #7).
- P2 — #60 : budget d’espace proportionnel et tests de sauvegarde indépendants du disque.
- P2 — #21 : résoudre la racine des hooks à l’appel et qualifier leur filtre.
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
- #35 — Sorties courtes et rapports complets pour les agents.
- #39 — Savoir communautaire Kobra S1 et cohérence des réglages.
- #40 — Sources et provenance des connaissances communautaires.
- #41 — Avertissements de cohérence pour les réglages 3MF.
- #42 — Traçabilité des 3MF.
- #56 — Hygiène du dépôt et secrets en CI ; UTF-8 (suite de #22), Ruff, format, délais et mises à jour. Coordonner avec #37.
- #66 — Validation des POST de la visionneuse ; couvrir la recette #50.
- #67 — Gabarits HTML/JS de la visionneuse ; comparer le rendu avec la recette #50.
- #26 — Coutures visibles et règle U31.
- #27 — Bibliothèque de filetages, inserts et écrous.
- #28 — Retour photo assisté et observations par zone.
- #29 — Connecteurs à clip et jeux par matière.
- #31 — Placement des clips dans la visionneuse.
- #32 — Conception déterministe avec fiches JSON.
- #50 — Recetter la visionneuse dans Safari et au pavé tactile.
- #52 — Recetter la lecture Cloud Anycubic avec un compte autorisé.

#20 est fermé comme doublon de #37.

### P4 — À différer

- #23 — Découper les gros modules, dont l’analyseur des capacités machine.
- #33 — Installation optionnelle par OpenSkills.
- #34 — Évaluation du graphe de code pour les tests d’impact.

## 1.2.0

Sujets à cadrer avant réalisation.

### P4 — À différer

- #24 — Idées 1.2 : deuxième imprimante, rendu Metal, portabilité après le socle de #17.
- #68 — Découper les contrôles de finition et d’affectation ACE, après #58.
- #69 — Typage progressif des modules communs, après #13 et #17.
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

## Étude des supports

Les variantes restent des comparaisons. Lire les distances Z dans les G1 Z
effectivement tranchés, en distinguant les dépôts des relevages. Les interfaces
multimatières et les ailettes restent NON VÉRIFIÉES pour le retrait jusqu’aux
mesures sur les témoins publics générés par l’atelier, sans modèle tiers.

| Jalon | Priorité | Issue | Périmètre |
| --- | --- | --- | --- |
| 1.0.2 | P4 | #70 | S7. Actualiser les renvois des contrôles et vérifier les chemins des docstrings. |
| 1.1.0 | P2 | #80 | Étendre les énumérations qualifiées aux styles et motifs des supports. |
| 1.1.0 | P2 | #89 | S2. Comparer les familles de supports après #80 et mesurer l’écart réellement tranché. |
| 1.1.0 | P3 | #90 | S5. Mesurer retrait, marques et dessous sur témoins publics. Réutiliser le retour par zone de #28. |
| 1.1.0 | P3 | #91 | S1. Comparer les interfaces dans une autre matière, après #71 et #72. Retrait à vérifier avec #90. |
| 1.1.0 | P3 | #93 | S4. Cartographier les îlots de surplomb avant tranchage, pour guider le placement manuel. |
| 1.2.0 | P3 | #92 | S3. Comparer les ailettes détachables. Retrait à vérifier avec #90. |
| 1.2.0 | P4 | #75 | S6. Résumer les observations de supports dans la vitrine publique, avec leur portée. |

Les cotes publiées pour les ailettes servent d’hypothèses. Les sources sous
AGPL et le wiki OrcaSlicer fournissent des noms et des principes. Une éventuelle
reprise MIT sera attribuée dans TIERS.md.

## Lots de réalisation

Le premier lot de réalisation commence par #13 puis #12. Le circuit des
correctifs 1.0.2 peut avancer séparément. Dans chaque lot, les prérequis
précèdent les travaux qui en dépendent. Les travaux indépendants peuvent
avancer en parallèle.

| Jalon | Lot | Ordre proposé |
| --- | --- | --- |
| 1.0.2 | Correctifs | #21, #70 |
| 1.1.0 | Lot 1 — Fiabilité G-code | #13, #12 |
| 1.1.0 | Lot 2 — Socle 3MF, chemins et recette numérique | #17, #64, #65, #14, #48, #49, #76, #80, #81, #82, #89, #51 |
| 1.1.0 | Lot 3 — Provenance, réglages et contrôle d’envoi | #37, #36, #38, #71, #18, #35, #40, #39, #41, #42, #43, #56 |
| 1.1.0 | Lot 4 — Mesures, supports et recettes | #19, #26, #28, #52, #66, #67, #50, #72, #91, #84, #90, #93 |
| 1.1.0 | Lot 5 — Fixations, conception et guides | #27, #29, #31, #32, #23, #33, #34, #74 |
| 1.2.0 | Lot 6 — Données d’usage et optimisation volumique | #73, #77, #83, #92, #68, #69, #85, #86, #87 |
| 1.2.0 | Lot 7 — Portabilité, profils et restitution | #24, #44, #45, #75 |

#30 est fermé comme doublon de #29. #29 porte la géométrie des clips
et les jeux par matière. #31 porte le placement dans la visionneuse,
après #29. #43 est planifié en 1.1.0.

Les dépendances bloquantes sont enregistrées dans GitHub. Les liens de
coordination et de validation restent précisés dans les issues, notamment
le retrait mesuré pour les supports et la calibration du calcul volumique.

## Versions testées

| Composant | Version | Vérification |
| --- | --- | --- |
| Anycubic Slicer Next | 2.0.0.3 | Lanceur CLI et témoins numériques |
| macOS | 27.2 (26B123) | Installation et suite complète sur Apple Silicon |

Les versions testées décrivent le périmètre observé.

## Étude du remplissage

Le premier lot G-code reste #13 puis #12. Les variantes de remplissage servent
à comparer des résultats, sans adoption automatique. Un modificateur sans
effet mesurable dans le G-code donne FAIL. Les éléments finis et les lois
d’échelle restent NON VÉRIFIÉS tant qu’aucune mesure physique ne les confirme
dans les conditions étudiées.

| Jalon | Priorité | Issue | Périmètre |
| --- | --- | --- | --- |
| 1.1.0 | P2 | #13 | R9. Mesurer les trajets avec l’interpréteur commun. |
| 1.1.0 | P2 | #80 | R1. Qualifier les valeurs énumérées par Anycubic Slicer Next. |
| 1.1.0 | P2 | #81 | R2. Comparer les options natives qualifiées après #80. |
| 1.1.0 | P2 | #82 | R3. Mesurer l’effet des modificateurs par motif après #80. |
| 1.1.0 | P3 | #72 | R7. Comparer un filament de cœur différent avec le bilan de purge et le contrôle ACE #71. L’extension est P4. |
| 1.1.0 | P3 | #84 | R6. Mesurer la rigidité sur les éprouvettes existantes et coordonner la recette avec #51. |
| 1.2.0 | P3 | #83 | R4 puis R5. Convertir le champ de densité après #82, puis étudier les contraintes avec les mesures de #84. |
| 1.2.0 | P4 | #85 | R8. Comparer l’orientation du motif selon l’effort après #81 et #84. |
| 1.2.0 | P4 | #86 | R10. Comparer les temps réels et estimés par motif à partir des retours ou du Cloud en lecture seule. |
| 1.2.0 | P4 | #87 | R11. Comparer un réseau gyroïde modélisé dans la pièce. |

Les slicers et guides sous AGPL restent des repères pour les noms de réglages
et les principes généraux. La reprise éventuelle d’une source compatible
exige une attribution dans TIERS.md. Le choix d’un solveur volumique inclut
sa licence et ses dépendances, car l’installation reste tout ou rien.
