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
- #30 — Géométrie des goupilles et tenons à clip.
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
