# Feuille de route

Le [CHANGELOG](CHANGELOG.md) décrit les versions livrées. Les issues conservent
les critères, les preuves et les dépendances ; une expérience sans résultat
établi reste ouverte. Les priorités distinguent P1 (défaut reproduit critique),
P2 (fiabilité et validation), P3 (fonction ciblée) et P4 (étude ou confort).

## Version 1.1.0

Cette version regroupe les correctifs préparés après 1.0.1 et les évolutions
suivantes ; aucune version intermédiaire 1.0.2 n’est nécessaire :

| Domaine | Résultat | Issues |
| --- | --- | --- |
| Débit U25 | Calcul commun, diamètre déclaré, maximum strict, percentile 99 informatif | #12 |
| Tranchage et optimisation | Catalogue d’identifiants transmis, choix explicite du profil maître et origine visible | #101, #102 |
| Diagnostic | Contrôle d’installation en lecture seule, sans initialisation ni mesure | #76 |
| Rapports de paroi | Export privé neuf et synthèse par bandes Z, détails conservés | #107 |
| Retours humains | Capture de déclaration avec empreinte, sans préparation ou impression inventée | #118 |
| Tests et CI | Mesures directes, isolation réseau du processus pytest, contrôle des secrets | #14, #65, #56 |
| Livraison | Version unique, README indépendant du privé, tests nommés par domaine | #70 |

Les correctifs #21, #58, #59, #60, #98, #99, #100 et #104 sont inclus.
La consolidation du lecteur #13 est partielle : les arcs G2/G3 et la qualification
du repère des couches après G92 XYZ restent à traiter. Le changement U25 peut
faire passer un fichier de PASS à FAIL sans modifier ses trajectoires ; voir
le [contrat du débit](docs/reference/debit.md).

## Version 1.2.0 — fondations

| Domaine | Résultat | Issues |
| --- | --- | --- |
| Lecture G-code | Arcs G17 XY analytiques, repère spatial après G92 ; géométrie non modélisée explicitement NON VÉRIFIÉE | #13 |
| Destinations et écritures | Gardes communes, checkout temporaire protégé, écritures UTF-8 atomiques sans nom temporaire fixe | #17 |
| Archives | Lecteur ZIP/XML commun borné, réglages typés et migration des contrôles de lecture | #64 |

Voir [la décision de livraison](docs/decisions/2026-11-11-fondations-1-2.md).
Les parcours mémoire spécialisés déjà bornés sont conservés ; les autres
familles de lecteurs seront migrées dans un lot distinct. Les projets et leurs
preuves restent privés et ne sont pas réécrits par cette version.

## Prochaine version : 1.3.0

### 1. Fiabiliser les fondations et les preuves

Traiter les prérequis avant les nouveaux réglages :

- **Lecture G-code** : #123 étend les usages géométriques au-delà
  du contrat qualifié de #13 ; les témoins déjà acquis restent conservés.
- **Fichiers et archives** : #122 poursuit la migration des familles hors contrôles ;
  #42 suit la traçabilité 3MF.
- **Contrôle avant envoi** : #71 pour ACE/buse/démarrage, #109 pour les
  avertissements natifs, #111 pour les mesures structurées des zones locales.
- **Provenance et profils** : #36 avant l’enrichissement communautaire #40 ;
  #38 compare les réglages au profil système. #103 factorise le contexte typé
  des listes sans réindexation implicite.

### 2. Qualifier le comportement réel du slicer

#80 établit les valeurs énumérées consommées par une version identifiée du
slicer. Il précède les comparaisons de remplissage #81 et la preuve d’effet
G-code des modificateurs #82, puis les variantes de supports #89.
#48 qualifie les 3MF multi-volumes ; #49 garde le lot des 32 archives historiques
aux réglages ambigus. Un test synthétique ne remplace pas cette qualification.

### 3. Compléter les parcours de préparation

| Lot | Travaux restant ouverts |
| --- | --- |
| Préparer sans reconstruire | #106 : revue locale et contrôle final d’une archive déjà tranchée |
| Géométrie et 3MF | #105 : placement traçable ; #110 : intention de couverture Z ; #112 : remplacement ciblé ; #113 : échanges indexés de découpe |
| Concevoir et assembler | #32 : fiches déterministes ; #27 : filetages/inserts ; #29 : clips ; #31 : placement dans la visionneuse |
| Examiner les défauts | #26 : coutures ; #28 : observations photo ; #93 : îlots de surplomb |
| Matière et usage | #72 : coût de purge ; #77 : matière par bobine/outil ; #73 : entretien ; #74 : premiers essais guidés |
| Échanger | #43 : conversion entre slicers ; #45 : export manuel de profils, décision d’usage encore requise |

### 4. Conserver les expériences à leur bon niveau de preuve

Les tickets suivants exigent une recette ou des données supplémentaires ; ils
ne sont pas fermés par une simulation ou par un simple rapport numérique :

- #51 : validations physiques des témoins et assemblages.
- #84 et #90 : rigidité des remplissages, retrait et marques des supports.
- #86 : durées réellement observées ; #85 : effort principal et orientation,
  avec les limites des mesures disponibles.
- #91 : interface dans une autre matière ACE ; #92 : ailettes détachables.
- #50 : recette Safari et pavé tactile ; #52 : Cloud avec compte autorisé.

Les études #83 (champ de densité/contraintes) et #87 (gyroïde modélisé) restent
séparées des preuves physiques. Aucun nouveau réglage n’est adopté automatiquement.

### 5. Entretenir le code et organiser l’apprentissage

- #18 : répartiteur CLI ; #19 : coût des transactions ; #23 : modules trop longs.
- #66 et #67 : validation des requêtes et séparation des gabarits de la visionneuse.
- #37 puis #69 : annotations et types ; #68 : contrôles de finition et ACE.
- #35 : rapports courts et détaillés, avec progression déjà apportée par le diagnostic.
- #34 : évaluation du graphe de tests d’impact ; #33 : installation optionnelle.
- #39 regroupe le savoir et la cohérence des réglages ; #40 porte la provenance
  des connaissances, #41 les avertissements de cohérence et #75 la vitrine publique.
- #114 : lecture PDF optionnelle des guides, sans dépendance imposée à tous les usages.
- #119 regroupe le diagnostic, la réparation conservatrice et l’apprentissage des
  incidents. Les premiers outils existent ; l’autoréparation générale reste à concevoir.

La boucle attendue est : retour sourcé → défaut reproduit → correctif → test de
régression → contrôle et relecture → livraison. Une vue peut être régénérée depuis
la base ; un lien peut être réparé sur preuve d’identité. Un original introuvable
reste explicitement NON VÉRIFIÉ, et les anciens apprentissages sont conservés.

## Études ultérieures

#24 garde les pistes de deuxième imprimante, portabilité et rendu Metal. #44 suit
l’état et la caméra en lecture seule. Leur présence dans la roadmap ne déclare
ni une machine configurée ni une intégration déjà validée.

Les projets, photos et preuves personnelles restent dans `prive/`.
La lecture Cloud reste privilégiée ; aucun parcours LAN n’est prévu.
