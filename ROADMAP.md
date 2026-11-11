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

## Version 1.3.0 — preuves et archives 3MF

La version publiée (tag `v1.3.0`, voir le [CHANGELOG](CHANGELOG.md))
regroupe le lecteur 3MF borné, les rapports d’envoi et d’attribution, la
première qualification des énumérations ASN et le témoin natif multi-volumes.
Les travaux sans preuve suffisante restent ouverts dans la milestone suivante.

## Version 1.4.0 — profils, envoi et réparation

Le [CHANGELOG](CHANGELOG.md#140--2026-11-11) regroupe les changements de la
version. Les contrôles rapprochent les profils fournis, la matière déclarée,
la buse et le démarrage ASN (#38, #41, #71). Une archive déjà tranchée peut
être revue sans relance du slicer (#106) et une copie peut recevoir une
provenance liée par SHA-256 (#42). Le plan de réparation et son application
se limitent à une vue canonique explicitement choisie (suite de #119).

Les tests et la lecture 3MF continuent à partager leurs garde-fous. L’aperçu
refuse une plaque ambiguë (#140), le verrou d’export reste confiné (#138) et
le lanceur distingue les incidents inconnus sans lever le plafond des causes
établies (#143). Aucun verdict numérique n’atteste une impression physique.

## Version 1.5.0 — contrôles et échanges de l’atelier

Le [CHANGELOG](CHANGELOG.md#150--2026-11-11) décrit les changements livrés.
Le placement d’un 3MF importé repose sur une déclaration liée par SHA-256
et des relations OPC explicites (#105). La couverture Z restitue son repère
et ses transformations (#110), et l’inventaire des envois refuse les plaques
homonymes (#147). L’export indexé de découpe (#113) conserve les indices à la
relecture. Le guide de premiers essais (#74), la vitrine du savoir (#75), la
lecture PDF optionnelle (#114) et la mise à jour des guides (#149) facilitent
la lecture des preuves. Aucun contrôle n’atteste une impression physique.

## Version 1.6.0 — preuves d’atelier et géométrie

La version documente la reprise explicite de la cible manquante d’un lien de
vue privée (#119). Le rapport de purge sépare transitions T, matrice
conditionnelle et purge physique (#72). Le contrôle de volume déposé utilise
l’enveloppe analytique des arcs G17 qualifiés (#123). Les couches courtes
décrivent les recouvrements et ambiguïtés des coupes XY (#108) ; la
cartographie STL mesure des groupes de faces par arête partagée (#93).
Ces contrôles ne décident pas d’un appui physique ni d’un réglage à appliquer.

Les quatre grands modules ciblés sont séparés (#23). La CI vérifie les types
de cinq frontières communes (#69). Le résumé des plages Z garde un rapport
complet privé (#35) et les études utilisent le lecteur 3MF borné (#122).
Le chantier de cohérence des réglages est clos après vérification des deux
volets livrés et des archives sources conservées (#39).

## Version 1.7.0 — preuves de qualité et contrôles d’atelier

Les rapports historiques des niveaux 1 et 3 restent en place. Un rejeu différent
est publié séparément et un résultat déjà conservé est relu sans réécriture (#160).
Une fiche privée relie un incident à un commit et à des fichiers de test précis ;
elle ne prouve pas leur exécution (suite de #119).

Les lecteurs communs couvrent de nouveaux parcours de qualité, d’études et de
variantes (#122). La première couche refuse les alias de plaques (#162) et les
résumés de variantes écartent les sources liées et les totaux non finis (#165).
Les ponts et surplombs attribués peuvent décrire une enveloppe G17 (#123).
Le bilan d’entretien reste conditionnel aux événements et seuils déclarés (#73).
Le résumé des surplombs (#35), les annotations communes (#127) et le découpage
de l’audit ACE (#68) complètent le lot. Aucune de ces vérifications numériques
ne valide une impression physique.

## Version 1.8.0 — volumes 3MF, preuves et préparation

Un remplacement ciblé peut produire une copie 3MF Core neuve à partir d’un
objet direct ou d’une feuille unique d’un assemblage à un niveau. Les autres
géométries, matrices, réglages et membres sont contrôlés avant publication
(#112). Les extensions du slicer et l’usage physique restent hors de ce
contrat. L’éprouvette interne lie ses métriques aux membres G-code exacts
et refuse les métadonnées de plaque répétées (#169).

Le bilan de filament lit le retour et l’envoi identifiés sans imputer de
masse à une bobine (#77). Les logements d’insert, lamage et écrou décrivent
des cotes et marges géométriques explicites, sans ajustement physique déduit
(#27). Le résumé de première couche, les lectures de plaques mixtes et de
l’éprouvette de jeux, et les annotations communes complètent le lot
(#35, #122 et #127).

## Version 1.9.0 — plaques sans ambiguïté et fiches CAO

La validation du tranchage, l’ouverture de la visualisation et les trois
parcours U25 refusent les alias de plaque et lisent le membre G-code exact
(#171, #173, #174). Les mesures restent nominales et les plafonds déclarés
gardent le même verdict. Le débit fournit aussi un affichage court avec le
rapport complet préservé (#35).

L’inscription de projet et la lecture des affectations ACE renforcent leurs
contrôles 3MF (#122). Une fiche d’incident peut lire les cas rapportés dans
un JUnit sans certifier leur exécution (#119). Une fiche de cotes génère un
plan et un script déterministes pour des blocs percés (#32). Les références
de construction et les géométries obtenues restent des preuves numériques.

## Version 1.10.0 — continuité, diagnostic et supports

Le diagnostic distingue les exports absents, liés, illisibles et différents
sans masquer les autres comparaisons ni réécrire l’atelier (#177). Les
numéros de plaque ambigus restent NON VÉRIFIÉS en continuité (#178). Le
contrôle de paroi extérieure calcule aussi le début exact du dépôt des arcs
G17 à Z constant, sans utiliser leur corde (suite de #123).

La normalisation des copies avant tranchage partage le lecteur borné et
conserve les différences déclarées, l’ordre et les métadonnées ZIP (#122).
Les valeurs d’interface de support `auto` et `concentric` ont produit des
trajectoires différentes sur le coupon du binaire identifié (#80 et #89).
Le résultat ne qualifie ni les autres valeurs, ni l’écart Z ou le retrait
physique. Le fraisage nominal prolonge le catalogue de fixations (#27).

## Version 1.11.0 — coutures, sections et reprise

Le contrôle des coutures rattache les départs de boucles à des régions
déclarées sur une plaque et un objet précis. Son aperçu est lié aux octets
du 3MF (#26). Les sections XY distinguent intérieur, extérieur et ambiguïté ;
l’appui estimé depuis le G-code reste une mesure séparée (#108).

La restauration des noms conserve le tranchage natif et vérifie
intégralement la copie publiée (#182, suite de #122). En cas de table
JSONL absente, un candidat peut être préparé hors de l’export depuis la
base en lecture seule. Il ne remplace aucune donnée ; sa preuve indique
les empreintes à revérifier (#119).

## Version 1.12.0 — preuves de préparation et de tranchage

Le comparateur ne valide plus une mesure sans dépôt clairsemé attribué
(#183). Le lanceur refuse un réglage machine illisible ou ambigu et
contrôle également le modèle et la buse dans la copie qu’il remet au
trancheur (#186, suite de #122).

La visionneuse localise les visées de surplomb vers un plan déclaré,
sans conclure à un accès global ni à un pont (#93). Le retour photo
conserve en privé l’original, l’aperçu et la confirmation séparée (#28).
Le motif `supportcubic` est qualifié pour le binaire et le témoin
observés (#80, #81). Le lamage supérieur étend les fiches cotées
avec contrôle d’interférence et volume vérifié (#27, #32).

## Version 1.13.0 — reprise, arcs et fiches cotées

Une table d’export absente peut être restaurée sur demande explicite.
La commande relit la preuve, le manifeste et la table SQLite en lecture
seule, publie exclusivement et refuse toute cible déjà présente (#119).

La première couche peut utiliser la distance exacte aux arcs G17 déposés
à Z constant dans sa grille. Les arcs hélicoïdaux ou non qualifiés restent
NON VÉRIFIÉS (#123). Les fiches cotées acceptent les poches rectangulaires
et signalent leurs interférences (#32).

La recette d’interface ajoute une projection nominale de l’écart Z
sur les dépôts du coupon, sans conclusion physique (#89). Les portées
publiques de fabrication sont documentées sous Ruff D10x (#127).

## Version 1.14.0 — preuves d’atelier et placements

Les retours photo confirmés utilisent un contrat commun avec les images déjà
disponibles, sans accès caméra (#28). Le placement des clips propose ligne,
grille et coordonnées manuelles avec réouverture et export vérifié (#31).
Les deux formes de clip restent nominales ; leur tenue et les jeux réellement
mesurés par matière restent à établir (#29).

La lecture des hauteurs refuse les plaques ambiguës (#191) et les plages de
couches utilisent le lecteur commun (#122). Le contrôle des supports rapporte
des écarts Z estimés par plaque (#89), et la sonde de modificateur compare le
dépôt clairsemé des variantes 20 % et 60 % (#82). Ni retrait ni rigidité ne
sont validés par ces résultats. Les portées données, mesure et projets
rejoignent le rattrapage D10x (#127).

## Version 1.15.0 — fiches, visserie et échanges 3MF

Le contrôle des appuis refuse un numéro de plaque ambigu et lie le SHA aux
octets relus dans le contexte commun (#194). Les autres lecteurs et les
restaurations restent à migrer par lots (#122).

Les fiches cotées acceptent un disque, des perçages, des lamages et des
poches dans une base cylindrique (#32). La découpe propose un passage de
vis et des logements de tête et d’écrou à cotes déclarées, sans choisir
une vis ni qualifier un filetage (#27).

La comparaison 3MF détecte les différences de réglages et de palette.
Elle compare aussi la peinture littérale des facettes lorsqu’un maillage
direct, son ordre et le dialecte sont établis. Les propriétés Core et la
conversion entre slicers restent ouvertes (#43). Un coupon relie des
surfaces en surplomb aux axes de pont effectivement tranchés, sans conclure
à la tenue (#93). Treize docstrings de contrôles complètent le rattrapage
historique (#127).

## Prochaine version : 1.16.0

### 1. Fiabiliser les fondations et les preuves

Traiter les prérequis avant les nouveaux réglages :

- **Annotations** : #127 suit le rattrapage des portées anciennes sous le
  contrôle Ruff D10x introduit par #37. Poursuivre les domaines historiques
  restants, dont contrôles, conception et visualisation.
- **Lecture G-code** : #123 étend les usages géométriques au-delà
  du contrat qualifié de #13 ; les témoins déjà acquis restent conservés.
  La mesure dans une boîte, l’enveloppe du dépôt et la revue des ponts et
  surplombs attribués peuvent utiliser la courbe. La continuité des parois
  extérieures et la première couche sur demande couvrent G17 à Z constant.
  Les hélices en continuité et la cinématique restent à qualifier. #133 a conservé
  le débit mesurable de l’arc dans la finition des parois. #134 suit la
  précision des arcs I/J natifs que le contrat de rayon constant ne qualifie pas.
- **Fichiers et archives** : #122 poursuit la migration des lecteurs et
  écrivains spécialisés. La normalisation, la restauration des noms
  et le précontrôle machine utilisent le lecteur commun. Le plan d’origine
  doit encore partager la garde XML (#196). Les autres transformations du
  trancheur exigent leurs propres preuves ; les
  remplacements Core de 1.8 restent distincts.
- **Données et reprise** : #119 poursuit la récupération fondée sur les
  empreintes. #19 cherche à éviter le parcours complet après chaque écriture,
  tout en détectant les exports corrompus.

### 2. Qualifier le comportement réel du slicer

#80 établit les valeurs énumérées consommées par une version identifiée du
slicer. Il précède les comparaisons de remplissage #81 et la preuve d’effet
G-code des modificateurs #82, puis les variantes de supports #89.
La [qualification native #48](docs/reference/integrite-3mf.md#qualification-native-dun-modificateur-48)
couvre une sonde multi-volumes. #49 garde le lot des 32 archives historiques aux
réglages ambigus. Les essais natifs conservent leurs conditions d’application.

### 3. Compléter les parcours de préparation

| Lot | Travaux restant ouverts |
| --- | --- |
| Concevoir et assembler | #32 : couvrir d’autres opérations cotées ; #27 : filetages, autres fixations et ajustements réels ; #29 : jeux et insertion des clips à mesurer |
| Examiner les défauts | #93 : hypothèses de pont et comparaison aux trajectoires |
| Matière et usage | #72 : coût de purge ; #77 : matière par bobine/outil ; #73 : entretien |
| Échanger | #43 : propriétés Core, composants et conversion entre slicers ; #45 : export manuel de profils, décision d’usage encore requise |

### 4. Conserver les expériences à leur bon niveau de preuve

Le [guide des premiers essais](docs/guides/premiers-essais.md) (#74) aide à
préparer le témoin et à consigner les mesures dans leur portée.

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

- #19 : coût des transactions. Un prototype de réemploi des JSONL préserve les
  octets, mais la validation des références parcourt encore la base. Le prototype
  ne suffit pas à clore le chantier.
- #127 : rattraper les annotations des contrôles et lanceurs après les
  primitives communes de lecture, de copie et de publication.
- #35 : compléter les rapports courts après les parcours d’envoi, de plages Z,
  de surplombs, de première couche et de débit.
- #119 : qualifier d’autres familles de reprise et la preuve d’exécution
  au-delà du contenu déclaré d’un rapport JUnit. Une vue canonique peut être régénérée après
  contrôle du plan et copie vérifiée de l’ancienne vue. La cible d’un lien cassé
  peut être recréée par copie prouvée ; un original introuvable reste NON VÉRIFIÉ.

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
