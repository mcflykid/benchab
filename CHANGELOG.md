# Changements

Historique des versions publiques ; les tags Git identifient les candidates livrées.

## 1.32.0

- Le registre privé vérifie la filiation, les preuves et les promotions des variantes (#268).

- Le diagnostic de finition décrit la continuité, le fond et la largeur observée en mode vase (#246).

- Un aperçu de candidate identifie les maillages déclarés et refuse les fichiers étrangers (#260).

- Mesure du surplomb par zone selon le procédé déclaré (#266).
- Le relevé RFID d’ACE estime le restant à partir de la masse nominale déclarée (#248).

- Le bilan G-code répartit la matière et le temps nominal de dépôt par plaque, outil, objet, type de trajet et zone Z (#267).
- La carte des coutures relève les départs de paroi par couche et compare leur dispersion en angle et altitude (#269).
- La continuité de paroi en mode vase regroupe les dépôts selon les couches déclarées par le moteur (#249).

## 1.31.1

- L’écriture d’un 3MF canonique refuse un corps imprimé ouvert ou mal orienté et le nomme (#250).
- Les producteurs concernés respectent l’atelier sélectionné et le temporaire natif (#257).
- Un test garantit le rapport U25 par plaque lorsque l’identifiant du seul filament est invalide (#262).
- U25 rapproche les noms d’objets du G-code et couvre le mode vase à objet unique (#249).
- Les sous-commandes acceptent leurs options et affichent leurs aides métier (#252).
- Le guide explique l’exécution depuis un worktree et le test de version indique comment réparer une installation en retard (#254, #255).
- La documentation précise la portée du mode vase et le rapport distingue la déclaration de buse montée du profil Glow (#256, #270).

## 1.31.0

- L’import 3MF restitue les topologies avant et après soudure et protège les liaisons entre corps lors du placement
  sur plusieurs plaques (#272, #273).
- Le tranchage signale les refus d’intégrité et qualifie les réécritures décimales du débit. Les avertissements
  Anycubic sont lus avec leur en-tête natif (#258, #261, #274).
- U25 localise le pic par objet et outil, avec les seuils embarqué et explicite (#275).
- Le contrôle d’affleurement calcule les jeux nominaux et produit une coupe à partir des cotes déclarées (#276).
- La lecture Cloud expose les raisons de configuration publiables (#253).

## 1.30.1

- Les réparations de vues écrivent leurs preuves sous `qualite/reparation-vues/` (#243).

## 1.30.0 — 2026-11-12

- Rapports. Les contrôles et le planning disposent de sorties courtes avec
  accès au détail. La routine conserve les journaux d’échec et le contrôle
  de réimpression publie son rapport hors du projet contrôlé (#35).
- Reprise. Les fiches d’incident vérifient le rattachement d’un JUnit à
  une tentative de CI et rapportent les résultats des cas déclarés (#119).
- Filament. Une pesée et une affectation confirmées permettent une
  imputation unique au stock. La recette avec une pesée réelle reste
  ouverte (#77).
- Surplombs. La comparaison des axes de pont tient compte de la rotation
  déclarée et distingue les objets nommés dans le G-code. Les preuves
  portent sur les fichiers et les trajectoires nominales (#93).
- Correctifs. Les sorties existantes sont refusées avec un diagnostic
  explicite (#234). Les chemins durables sont contrôlés après décodage
  JSON (#236). La CI contrôle la présence et la forme des docstrings
  du paquet (#127).

## 1.29.0 — 2026-11-11

- Diagnostic d’atelier. La relance du contrôle des références utilise le
  module installé et vise le même atelier, même si son chemin contient
  des espaces (#231).
- Entretien. Chaque compteur indique la période couverte et les données
  manquantes dans les retours. Les compteurs restent non vérifiés lorsque
  leur historique est insuffisant (#73).
- Annotations. Les fonctions et classes publiques du paquet ont passé la
  vérification de présence des docstrings Ruff D10. La CI applique ce
  contrôle à tout le paquet en plus de la garde des portées modifiées (#127).
- Documentation. Le guide des rapports recense les options déjà disponibles
  (#35), et la référence des commandes signale le profil ISO nominal (#27).

## 1.28.0 — 2026-11-11

- Fixations : génère le profil métrique ISO nominal à partir du diamètre
  et du pas déclarés. Le fond de la vis est arrondi et l’outil intérieur
  exige un jeu radial déclaré. L’ajustement physique reste à vérifier (#27).
- Réglages : documente l’arrondi M203 Z reproduit sur le binaire local
  Anycubic 2.0.0.3 et précise la portée du code source public (#49).
- Visualisation : documente les contrats publics de l’atelier et des
  outils de consultation, sans changer leurs traitements (#127).

- Documentation : refond le README autour du démarrage, des guides par
  usage et de la sauvegarde de l’atelier privé (#230).

## 1.27.0 — 2026-11-11

- 3MF : compare les matériaux et la peinture d’assemblages de plusieurs
  feuilles Core locales distinctes. Les identifiants, l’ordre et les
  translations doivent correspondre ; les écarts de facettes restent
  localisés. Cette étape ne qualifie pas un échange entre slicers (#43).
- CAO : accepte des poches borgnes et des ouvertures traversantes
  oblongues, cotées sur leurs deux axes. Vérifie le fond, les marges
  et le volume nominal avant la génération du modèle (#32).

## 1.26.0 — 2026-11-11

- Reprise : recrée sur demande un seul fichier source absent depuis une
  copie candidate vérifiée, avec chemin et SHA de référence confirmés
  séparément. Refuse tout remplacement et relit le fichier publié (#119).
- Rapports : propose un résumé du plan de réparation après enregistrement
  de son JSON complet. Les rejeux G-code et l’entretien gardent également
  le verdict et la référence au rapport intégral (#35, #73).
- Variantes : refuse les sorties hors atelier. JSON et Markdown sont
  publiés ensemble, sans ouvrir SQLite ; le témoin gyroïde utilise la
  même publication privée (#225).
- SQLite : réserve la publication du manifeste contre les écritures WAL
  concurrentes. Réutilise les SHA d’un instantané de transaction encore
  valide, sans alléger le contrôle des références (#226, suite de #19).
- Annotations : précise les contrats des variantes, compensations de trous,
  outils DfAM et capacités locales sans modifier leurs calculs (#127).
- Conception : produit le STL et le rapport d’un témoin gyroïde confiné
  à une enveloppe cotée et à des protections déclarées (suite de #87).
  Le contrôle de géométrie ne qualifie pas la tenue physique.
- Consommation : le même lecteur 3MF valide le SHA attendu et inventorie
  l’envoi. Les rapports des témoins S, M et L restent identiques (#223).
- Annotations : précise les contrats des outils CAO et de finition
  sans changer leurs calculs (suite de #127).

## 1.25.0 — 2026-11-11

- Matière : relie une déclaration de pesée à un retour, une plaque, un
  membre G-code et un index explicite. La variation déclarée reste distincte
  de l’estimation du slicer ; aucun stock n’est imputé (suite de #77).
- Rapports : le plan de consommation peut afficher un résumé qui renvoie
  au JSON intégral enregistré dans l’atelier privé. Le statut et les motifs
  sont conservés (suite de #35).

## 1.24.0 — 2026-11-11

- CAO : accepte une ouverture traversante explicitement cotée, droite
  ou arrondie. Les fonds borgnes et marges restent vérifiés (suite de #32).
- Réglages : peut vérifier les conditions embarquées des observations
  sur les styles grille et ajusté, sans changer le parcours historique
  ni déduire une tenue ou un retrait (suites de #80 et #89).

## 1.23.0 — 2026-11-11

- Inventaire : calcule le SHA de l’envoi une seule fois et mesure la taille
  dans son lecteur 3MF ouvert, avec contrôle final et rapport inchangé (#219).
- 3MF : compare les déclarations d’un composant Core unique, local et
  préalablement défini, à placements identiques (suite de #43).
- G-code : clôt la vérification des usages G17 couverts, avec tests des
  cercles complets refusés dans les contrôles linéaires. Les arcs arrondis
  et le firmware restent hors du verdict (#123, #134).
- Supports : compare les styles grille et ajusté à réglage isolé, avec
  écart Z nominal et répétition, sans conclusion sur le retrait (#80, #89).

## 1.22.0 — 2026-11-11

### Plaques et CAO

- Revue locale : refuse les numéros de plaque homonymes et lie le SHA
  aux membres réellement lus. L’ordre des archives valides est conservé (#212).
- Synthèse : écarte les mesures et estimations d’une archive de plaques
  ambiguë, même si chaque graphie a une empreinte déclarée (#213).
- Fixations : ajoute deux méplats axiaux déclarés aux profils nominaux
  sans modifier le filet triangulaire historique (suite de #27).
- CAO : construit des poches à angles arrondis et vérifie les collisions
  sur leur contour, avec des STEP numériques relus (suite de #32).
- Capacités : ajoute un accès direct et un résumé court, avec profil
  maître explicite et rapport complet conservé (suite de #35).
- Annotations : complète les 24 dernières docstrings publiques manquantes
  selon Ruff D10x dans les contrôles, sans modifier leur logique (#127).

### Reprise, ponts et densité

- Reprise : relit une preuve et sa copie privée sans toucher au projet.
  La présence et l’identité de l’original font l’objet d’un verdict séparé
  (suite de #119).
- Surplombs : propose une portée et un axe pour des plafonds rectangulaires
  horizontaux lorsque deux bords opposés continus sont établis. La tenue
  physique reste NON VÉRIFIÉE (suite de #93).
- Densité : consigne une sonde Application dont la sortie ne passe pas
  le contrôle des réglages. L’effet sur le G-code reste non qualifié (#82).

### Purge, 3MF et qualité

- Purge : calcule le SHA depuis le 3MF déjà ouvert, avec la vérification
  finale de la source conservée (#216).
- Rapports : le bilan de purge propose un résumé avec rapport détaillé
  privé, sans changer la somme conditionnelle ou le code (suite de #35).
- 3MF : compare la peinture et les matériaux déclarés sur un objet direct
  à translation identique. Aucune conversion de format (suite de #43).
- Qualité : documente le cache, les états et les codes du lanceur local
  sans changer sa logique (suite de #127).

### Performance et arcs

- Transactions : évite des constructions répétées lors de la validation
  des textes relatifs ordinaires. Les références durables, le commit et les
  exports gardent leurs vérifications (suite de #19).
- Arcs : distingue une compatibilité géométrique sous hypothèse d’arrondi
  d’un simple chevauchement de rayons. Aucun changement du lecteur strict,
  de la validation du firmware ou d’U25 (suite de #134).

### Observation de tour et remplissage

- Purge : observe sur demande le dépôt nominal de rôle Prime tower par
  outil et diamètre déclaré. Le bilan conditionnel reste séparé et la
  purge physique non vérifiée (suite de #72).
- Remplissage : mesure l’effet de la hauteur de combinaison sur les
  niveaux de dépôt clairsemé, avec répétition et paroi externe inchangée
  (suites de #80 et #81).

### Rapports et inventaire

- Rapports : le contrôle des écarts Z des supports propose un résumé et
  conserve un rapport privé complet. Un guide explique comment retrouver
  le détail des contrôles déjà couverts (suite de #35).
- Inventaire : documente les budgets en octets et les limites de lecture
  des archives sans modifier le comportement (suite de #127).

## 1.21.0 — 2026-11-11

- Rejeux : compare deux ou trois reçus privés et les G-code qu’ils
  identifient. Les divergences du style arborescent fort sont reproduites,
  sans attribution de cause ni adoption automatique (#208).
- Capacités machine : hache le 3MF dans le lecteur épinglé, sans charger
  deux fois l’archive entière par son chemin. Les rapports valides
  et U25 conservent leurs mesures (#210).
- Arcs G17 : rapporte une demande nominale maximale par axe, séparée
  de la cinématique et des vitesses physiques (suite de #123).

## 1.20.0 — 2026-11-11

- Reprise : prépare une copie passive d’un original déclaré dans une
  sauvegarde TAR, avec empreintes, manifeste et sortie neuve (suite de #119).
- Densité : construit deux ou plusieurs bandes sous une face déclarée ;
  un coupon natif mesure séparément la zone modifiée et le témoin (#83).
- Tranchage : qualifie les deux états de la paroi supplémentaire alternée
  sur le binaire observé ; aucune résistance n’est déduite (#80, #81).
- Supports : consigne deux G-code différents à entrée identique pour le
  style arborescent fort ; la répétabilité reste NON VÉRIFIÉE (#89, #208).
- Continuité : affiche un résumé avec le rapport privé complet et précise
  les limites des arcs planaires (#35, #123).
- Maintenance : documente les contrats du cache, des rapports courants
  et de l’inspection structurelle des paquets (#127).

## 1.19.0 — 2026-11-11

- Fiches d’incident : vérifie sur demande une tentative CI publique du
  commit exact. L’observation du workflow reste distincte du rapport
  JUnit et ne certifie pas un cas individuel (suite de #119).
- Surplombs : compare les axes déposés des ponts à un îlot horizontal,
  avec SHA, plaque et translation déclarés (suite de #93).
- Trancheur : qualifie `crosszag` sur le binaire observé et refuse le
  repli silencieux de `2dlattice` vers `crosshatch` (suites de #80 et #81).
- CAO : ajoute le fraisage conique à diamètre et angle déclarés, avec
  vérification numérique des STEP (suites de #27 et #32).

## 1.18.0 — 2026-11-11

- Lecture 3MF : termine la migration des lecteurs métier et documente
  les validateurs sur instantané qui gardent leur contrat (#122).
- Optimisation : refuse une source remplacée pendant le clone et vérifie
  l’ordre, le commentaire et le contenu non modifié (#202).
- Synthèse d’atelier : refuse le XML hors contrat depuis le fichier lié par
  SHA, sans changer les rapports S/M/L valides (#203).
- Comparaison 3MF : résout les matériaux de base Core déclarés dans un
  maillage direct ; la conversion reste NON VÉRIFIÉE (suite de #43).
- Remplissage : prépare des paliers radiaux disjoints et mesure sur un
  coupon l’effet numérique d’un changement de densité (suite de #83).
- Supports : reproduit l’effet d’une consigne XY sur les trajets d’un
  coupon, sans conclure au retrait (suite de #89).

## 1.17.0 — 2026-11-11

- Restauration 3MF : conserve le commentaire global du ZIP et lit les
  composants par le lecteur commun, avec contrôle des CRC (#199, suite de #122).
- Production 3MF : vérifie les deux membres de géométrie contre leurs
  octets prévus avant de remplacer la destination (#200).
- Appuis G-code : propose l’échantillonnage des arcs G17 planaires exacts
  sur demande et conserve un rapport privé complet en mode résumé. Le
  mode par défaut et le verdict U25 restent inchangés (suites de #123 et #35).
- Filetage nominal : produit un STL extérieur ou un outil de soustraction
  triangulaire avec jeu déclaré, hors norme ISO (suite de #27).
- Visionneuse : documente le repère de la transformation de translation
  sans modifier son comportement (suite de #127).

## 1.16.0 — 2026-11-11

- Origine 3MF : lit les modèles du plan dans le contexte commun, avec
  plafonds et XML protégé. Un contenu hors contrat reste NON VÉRIFIÉ
  (#196, suite de #122). Le parcours d’écriture reste distinct.
- Arcs : rapporte un intervalle XY conditionnel d’après le demi-pas
  déclaré, sans modifier le lecteur exact ni U25 (suite de #134).
- Rapports : résume la comparaison de projets 3MF tout en conservant le
  détail privé dans une sortie neuve (suite de #35).
- Supports : reproduit l’effet d’une variation de l’écart Z supérieur
  sur le coupon natif, sans conclure au retrait (suite de #89).

## 1.15.0 — 2026-11-11

- Appuis G-code : refuse les numéros de plaque homonymes avant sélection,
  conserve le cache et lie le SHA du rapport au descripteur relu (#194).
- Découpe : propose un passage de vis, un lamage et un logement d’écrou
  à cotes déclarées sur une coupe plane (suite de #27).
- CAO : ajoute une base cylindrique aux fiches cotées, sans changer les
  anciens plans rectangulaires (suite de #32).
- Échanges 3MF : compare les réglages, l’ordre déclaré de la palette et,
  sur un maillage direct identique, la peinture littérale. Ne convertit
  pas les formats et ne conclut pas à leur compatibilité (suite de #43).
- Surplombs : recoupe la carte d’un coupon et les axes de pont de deux
  3MF tranchés, avec altitude et limites de comparaison (suite de #93).
- Annotations : complète treize docstrings de contrôles de géométrie,
  d’orientation et de trajectoires (suite de #127).

## 1.14.0 — 2026-11-11

- Retour photo : un format privé commun à une photo importée ou déjà
  disponible distingue suggestion, confirmation et mesure. La comparaison
  relie le contexte exact sans reproduire les images ni le récit (#28).
- Découpe : ajoute deux clips nominaux (#29) et le placement en ligne, en
  grille ou manuel dans la visionneuse. Les positions sont conservées et
  le réassemblage vérifié à l’export (#31). L’insertion reste à mesurer.
- Supports : estime les écarts Z des dépôts horizontaux depuis une plaque
  exacte, avec couverture et exclusions. Aucune conclusion de retrait (suite de #89).
- Remplissage : compare le dépôt clairsemé dans une zone et un témoin
  pour le couple 20 %/60 % qualifié. Le contexte de mesure est déclaré ;
  peau et squelette restent NON VÉRIFIÉS (suite de #82).
- 3MF : le résumé des hauteurs refuse les numéros de plaques ambigus
  (#191). La lecture et la relecture des plages partagent le lecteur
  borné et gardent le différentiel de réglages (suite de #122).
- Annotations : vérifie les docstrings publiques des domaines données,
  mesure et projets (suite de #127).

## 1.13.0 — 2026-11-11

- Première couche : ajoute une mesure optionnelle des arcs G17 planaires,
  sans approximation par la corde (suite de #123).
- CAO : ajoute les poches rectangulaires aux fiches déterministes et
  vérifie dix constructions répétées sur cinq nouvelles fiches (#32).
- Supports : compare l’écart Z nominal sur les dépôts de la recette
  d’interface, sans conclure au retrait physique (suite de #89).
- Reprise : applique sur demande une seule table d’export absente après
  contrôle de la preuve, du manifeste et de la base en lecture seule (suite de #119).
- Annotations : termine le rattrapage des docstrings manquantes en
  fabrication, sans changement de comportement (suite de #127).

## 1.12.0 — 2026-11-11

- Remplissage : un dépôt sans rôle ou l’absence de dépôt clairsemé attribué
  ne valide plus les compteurs du comparateur (#183).
- Tranchage : vérifie la machine avec le lecteur 3MF borné et refuse les
  réglages illisibles, ambigus ou mal formés. Modèle et buse doivent rester
  identiques dans la copie remise au moteur (#186).
- Surplombs : ajoute des visées verticales échantillonnées vers un plan
  déclaré et leur localisation dans la visionneuse (suite de #93).
- Remplissage : qualifie le motif `supportcubic` pour le binaire et le
  témoin observés, sans appliquer un réglage (suite de #80 et #81).
- Retour photo : conserve en privé un original et un aperçu vérifiés ;
  une confirmation déclarée reste séparée du projet (suite de #28).
- CAO : ajoute le lamage supérieur à la fiche cotée, contrôle les
  interférences et vérifie les volumes sur cinq pièces (suite de #27 et #32).

## 1.11.0 — 2026-11-11

- Coutures : relie les débuts de boucles extérieures à des régions déclarées,
  signale les alignements visibles et fournit un aperçu lié au SHA (#26).
- Reprise : prépare une copie vérifiée d’une table exportée absente sans
  l’appliquer. La preuve et le JSONL sont vérifiés avant et après
  publication exclusive (suite de #119).
- Sections XY : classe en option la position des îlots face aux enveloppes
  de la même coupe ; conserve les ambiguïtés et sépare l’appui G-code (#108).
- Tranchage : restaure les noms de plaque dans une copie neuve, conserve la
  sortie native et vérifie tous les octets publiés (#182).
- Annotations : documente les fonctions de fabrication historiques,
  sans modifier leur comportement (suite de #127).
- Tests : fixe les instants simulés pour vérifier le stockage et la reprise
  des durées sans dépendre de l’horloge de la CI (#185).

## 1.10.0 — 2026-11-11

- Diagnostic : signale les exports absents, liés, mal formés ou divergents
  sans arrêter les autres comparaisons et sans modifier l’atelier (#177).
- Continuité : choisit le membre G-code exact et refuse les numéros de
  plaque ambigus même avec une sélection explicite (#178).
- Continuité : mesure le début du dépôt sur les arcs G17 de paroi extérieure
  à Z constant sans approximation de corde (suite de #123).
- Archives : normalise les copies du trancheur depuis le lecteur commun,
  en préservant les métadonnées et les différences permises (suite de #122).
- Supports : qualifie deux valeurs d’interface sur un seul binaire
  identifié, avec un effet numérique répété sur coupon (suite de #80 et #89).
- Fixations : ajoute un fraisage conique traversant à cotes explicites,
  sans cote normative ni jeu déduit (suite de #27).
- Annotations : précise le rôle et les limites des outils de préparation
  sans changer leur comportement (suite de #127).

## 1.9.0 — 2026-11-11

- Tranchage : refuse deux membres G-code pour un même numéro de plaque et
  contrôle l’archive de sortie dans un même contexte (#171).
- Archives : contrôle intégralement la copie d’un 3MF avant l’inscription
  d’un nouveau projet, sans changer ses octets (suite de #122).
- Incidents : compare une liste explicite de cas à un rapport JUnit borné ;
  l’exécution des tests reste non attestée (suite de #119).

- Visualisation : valide les numéros de plaque et les CRC avant l’ouverture
  du slicer (#173).
- U25 : relie les trois parcours au membre G-code exact, sans changer le
  plafond ni le verdict des archives univoques (#174).
- Rapports : affiche jusqu’à trois plaques en écart pour le débit et conserve
  le rapport complet (suite de #35).
- Archives : lit réglages, CRC et empreinte dans un seul contexte pour les
  affectations ACE (suite de #122).
- Conception : génère un plan déterministe et un script CAO depuis des cotes de
  bloc et de perçages explicites (suite de #32).

## 1.8.0 — 2026-11-11

- 3MF : remplace une cible directe ou une feuille non partagée d’un assemblage
  Core en préservant les autres géométries, matrices et membres (#112).
- Études : associe les résultats de l’éprouvette interne aux membres G-code
  exacts et refuse les métadonnées de plaque ambiguës (#169).
- Filament : consulte les estimations d’un retour et de son envoi vérifié,
  sans décompter de bobine ni modifier l’atelier (suite de #77).
- Conception : génère trois outils de logement à cotes explicites, sans
  compensation ni prétention d’ajustement physique (suite de #27).
- Archives : vérifie les paquets de plaques mixtes et la provenance des réglages
  de l’éprouvette de jeux avec le lecteur commun (suite de #122).
- Rapports : affiche les plaques et défauts prioritaires de première couche ;
  les diagnostics complets restent dans un rapport neuf (suite de #35).
- Annotations : précise les limites des contrôles de dépôt et les états du
  lanceur local (suite de #127).

## 1.7.0 — 2026-11-11

- Tests : la recette CLI de sauvegarde simule l’espace libre sans changer la
  réserve appliquée aux sauvegardes réelles (#163).
- Preuves : conserve les rapports de qualité historiques et publie un nouveau
  dossier si le rejeu produit des résultats différents (#160).
- Archives : partage le lecteur borné des fichiers de qualité et refuse les
  identités de plaque et d’objet ambiguës (suite de #122).
- Variantes : borne la lecture des résumés et refuse les preuves liées, les
  plaques ambiguës et les totaux non finis (#165, suite de #122).
- G-code : calcule les bornes de dépôt des arcs G17 dans les ponts et surplombs
  attribués, sans en déduire la portée libre ou l’appui (suite de #123).
- Annotations : documente les primitives de publication, de reprise et de
  copie typée des réglages (suite de #127).
- Surplombs : fournit un résumé des cinq principales zones et un rapport
  complet sur demande (suite de #35).
- Première couche : refuse les numéros de plaque ambigus et réutilise le même
  membre 3MF en optimisation (#162, suite de #122).
- ACE : sépare les contrôles d’affectation sans changer leurs règles ni leurs
  verdicts. Le contexte des attestations inclut la version du code (#68).
- CI : stabilise le test du délai sans imposer un temps de démarrage à Python (#161).
- Entretien : calcule un bilan conditionnel depuis des événements privés
  déclarés, sans inférer des heures physiques du trancheur (suite de #73).
- Études : utilisent le lecteur 3MF commun pour les réglages et le G-code
  de couches variables et de remplissage localisé (suite de #122).
- Apprentissage : relie une fiche privée d’incident à un commit et à ses
  fichiers de test exacts ; l’exécution reste déclarative (suite de #119).
- Archives : bornes et CRC communs pour les références fixes et la comparaison
  des 3MF, y compris le suffixe en majuscules (suite de #122).

## 1.6.0 — 2026-11-11

- Suivi : clôture le chantier de cohérence des réglages après relecture des deux
  volets livrés et nouvelle comparaison de préparations en lecture seule (#39).
- Surplombs : cartographie les groupes de triangles STL par arête partagée,
  sans déduire la possibilité d’un pont ou le besoin de support (suite de #93).
- Rapports : ajoute une sortie privée complète et un résumé plafonné pour le
  contrôle des plages Z, avec JSON historique conservé (suite de #35).
- Îlots : décrit les recouvrements et ambiguïtés entre coupes XY, sans conclure
  sur les appuis réellement tranchés (suite de #108).
- Maintenance : sépare les entrées, copies et publications des quatre gros modules
  en conservant les rapports des témoins et l’invalidation des études (#23).
- Atelier : restaure une cible de lien privé absente par copie d’identité prouvée et conserve le lien et sa preuve (suite de #119).
- Purge : compte les transitions explicites par plaque et sépare la matrice documentaire de toute mesure ou qualification du binaire (suite de #72).
- Qualité : documente les gardes communes de chemins et verrous (suite de #127).
- Capacités machine : vérifie l’enveloppe XYZ analytique des dépôts G17 sans corde ;
  vitesse et accélération par axe restent NON VÉRIFIÉES (suite de #123).
- Études : utilisent le lecteur 3MF borné pour les réglages de remplissage, de résistance
  et de stratégies, avec CRC conservés pour le comparateur (suite de #122).
- CI : vérifie les types des réglages, chemins, processus, interpréteur G-code
  et base, sans dépendance de vérification pour l’installation utilisateur (#69).

## 1.5.0 — 2026-11-11

- Preuves : les contrôles historiques publient des fichiers neufs et la routine préserve les résultats précédents à la reprise (#152).
- Débit U25 : la sortie de rapport ne peut plus écraser un 3MF source ni une preuve existante (#151).
- Imports : produit un placement traçable depuis un 3MF et une déclaration liée par SHA-256, sans déduire les plaques, le repère ou les réglages (#105).
- Finition : sépare les vérifications de mouvement, débit, couverture et proposition, avec rapports identiques (suite de #68).
- Envois : refuse les plaques homonymes et lit les masses et outils en flux, avec CRC complet et copie vérifiée par SHA-256 (#147, suite de #122).
- Documentation : actualise les commandes, les règles de preuve et les limites décrites dans les guides historiques (#149).
- Guide : relie les premiers essais d’une machine ou d’une bobine aux témoins, aux températures, aux mesures d’ajustement et au retour d’impression (#74).
- Savoir : génère hors ligne une vitrine du corpus public, par matière, réglage, défaut, entretien et code, avec statuts et portées (#75).
- Connaissances : lit les PDF textuels page par page avec l’extra optionnel, les empreintes et les limites explicites (#114).
- Découpe : export NPZ indexé en mm et relecture brute comparée, sans fusion ni profil d’impression (#113).
- Fabrication : contrôle la couverture Z déclarée et rend visibles la hauteur de base, les matrices et les décalages (#110).
- CLI : centralise les délégations homogènes et utilise la table commune des codes de statut sans changer les commandes ni leurs options (#18).
- Envoi : résumé des diagnostics et rapport complet privé facultatif, en conservant les codes historiques (suite de #35).
- Qualité : complète les annotations du module commun d’empreintes, sans changer ses résultats (suite de #127).
- Visionneuse : l’index et les flux G-code des supports partagent le contexte 3MF et conservent le cache par empreinte (suite de #122).
- Atelier : propose un candidat privé de SHA exact pour un lien de vue cassé, avec ambiguïtés explicites et sans réparation automatique (suite de #119).

## 1.4.0 — 2026-11-11

- Slicer : regroupe les incidents inconnus uniquement par opération et contexte vérifiable ; conserve le plafond des causes identifiées (#143).
- Fabrication : revue et contrôle final d’une archive déjà tranchée, avec conservation des décisions et mesures déclarées (#106).
- Profils : signale les écarts documentés de matière, machine et procédé, avec les sources et les contrôles manquants visibles (#41).
- Envoi : contrôle documentaire du produit, de la génération ACE, du chemin d’alimentation, du carton, de la buse et du démarrage ASN qualifié (#71).
- Aperçu : les plaques 3MF ambiguës sont refusées ; le catalogue peut reprendre une référence valide sans modifier les sources (#140, suite de #122).
- Préparation et planning : lecture 3MF commune, contrôle des CRC, des numéros de plaque et des métadonnées XML (suite de #122).
- Provenance : registre public de signalements communautaires contextualisés et non prescriptifs (#40).
- G-code : mesure analytique des arcs G17 dans les régions de remplissage, avec reprise d’extrusion et repère G92 (suite de #123).
- Densité : verdict numérique d’effet isolé par comparaison région/témoin hors zone (suite de #82).
- Finition : le débit d’un arc peut donner FAIL même si sa couverture géométrique reste NON VÉRIFIÉE (#133).
- Visionneuse locale : gabarit HTML et scripts Cloud/G-code extraits en ressources empaquetées, sans changer les actions ni le rendu autonome hors ligne (#67).
- Visionneuse : lecture JSON commune et bornée sur les quatre routes POST locales (#66).
- Aperçu G-code : le titre garde les marqueurs littéraux sans retraiter les données (#136).
- Installation : voie OpenSkills facultative testée dans un projet jetable (#33).
- Données : conserve inode et octets des JSONL canoniques, répare les exports divergents depuis SQLite et publie le manifeste en dernier ; première étape de #19.
- Qualité : un seul graphe de dépendances par lot, avec la suite complète conservée pour le code (#34).
- Données : revalidation du répertoire d’export et du verrou avant publication, même si l’atelier est déjà ouvert (#138).
- Contrôle : comparaison des réglages projet, objets, parties et plaques aux profils explicitement fournis ; écarts sensibles, provenance par clé et filiations de profils en lecture seule (#38).
- Atelier : plan passif et réparation explicite d’une vue canonique, avec preuve de la source et sauvegarde relue (suite de #119).
- 3MF : copie privée avec SHA-256 du source, partie JSON facultative et contrôle des membres conservés ; le slicer peut retirer cette provenance (#42).

## 1.3.0 — 2026-11-11

- Réglages : contexte commun des slots filament, identités et ordre conservés, variantes suffixées appariées séparément et résidus NON VÉRIFIÉS (#103).
- Envoi : avertissements natifs par plaque, codes inconnus conservés et origine non établie signalée (#109).
- Revue locale : mesures de recul et recouvrement recalculées depuis les lignes G-code liées ; contradiction bloquante et données manquantes explicites (#111).
- Envoi : l'indicateur de conformité reflète le verdict complet, y compris les contrôles communs (#125).
- Qualité : Ruff D10x contrôle les nouveaux modules et portées publiques modifiées ; les annotations historiques sont reprises par lots (#37, suite #127).
- Lecture 3MF : flux G-code séquentiel borné avec caches SHA préservés et garde de chemin avant le cache (première étape de #122).
- Lecture 3MF : réouverture avec un index et un quota neufs, réentrée simultanée refusée (#128) ; import des modèles et réglages sur le lecteur commun (#122).
- Import : attribution déclarée des modèles, liée à leur SHA-256 et conservée à la reprise (#36).
- Réglages : comparaison des valeurs énumérées avec les essais du binaire ASN identifié ; les replis observés échouent et les valeurs non essayées restent NON VÉRIFIÉES (première étape de #80).
- Lecture 3MF : intégrité avant/après, noms et résultats de plaques et optimisation utilisent les limites et le décodage communs (suite de #122).
- Lecture 3MF : préparation et planning utilisent l’index partagé, CRC vérifié, empreintes G-code conservées et noms de plaque XML bornés (suite de #122).
- Qualification ASN : tranchage natif d’une sonde synthétique à deux volumes ; rôles, géométrie et réglages contrôlés, effet de la densité mesuré dans le G-code (#48).


## 1.2.0 — 2026-11-11

- G-code : arcs G2/G3 du dialecte G17 XY documenté calculés analytiquement, longueur et débit nominaux exacts ; repère spatial distinct après G92 XYZ. Les contrôles géométriques qui ne modélisent pas encore les arcs restent NON VÉRIFIÉS (#13).
- Chemins et écritures : destinations privées et temporaires communes pour les exports, refus des sorties publiques même dans un checkout temporaire, gardes des hooks avant résolution et remplacements UTF-8 atomiques avec temporaires uniques (#17).
- Archives : lecteur 3MF commun ouvert une fois par contexte, index non ambigu, budgets de décompression, XML sans DTD ni entité et réglages typés. Migration des contrôles de lecture ; parcours mémoire spécialisés déjà bornés conservés (#64).

Les projets, préparations et connaissances privées sont conservés. Les règles
strictes U25 de 1.1.0 restent applicables ; un résultat numérique ne vaut pas
validation physique et aucune commande machine n’est nécessaire à cette mise à jour.

## 1.1.0 — 2026-11-11

- U25 : calcul commun en mm³/s avec diamètre déclaré ; chaque dépassement établi du plafond donne FAIL, percentile 99 informatif, données insuffisantes NON VÉRIFIÉES (#12).
- Tranchage public : catalogue d’identifiants permanents transmis explicitement, avec priorité sur la variable d’environnement historique ; identifiants présents conservés (#101).
- Optimisation : sélection explicite du profil maître pour proposer et comparer, origine et priorité visibles, confinement conservé (#102).
- Diagnostic d’installation en lecture seule, sans création de base ni mesure, avec contrôles Python/verrou, SQLite, Git, espace et preuves disponibles (#76).
- Continuité : export JSON privé neuf et synthèses par bandes Z, détails par couche conservés, bornes et couverture explicites (#107).
- Tests : collecte directe des mesures, unités, rattachement et reprise sans double comptage ; réseau externe bloqué dans le processus pytest avec opt-in explicite (#14, #65).
- CI : contrôle réel des secrets et de la frontière publique, avec fixtures synthétiques et guides de contribution et de signalement privé (#56).
- Git : circuit de fusion squash et nettoyage des branches documentés (#96).
- Livraison : version unique dans le paquet, README indépendant des données privées, CHANGELOG éditorial préservé et tests d’acceptation nommés par domaine (#70).

Changement de verdict U25 : les témoins figés S/M/L conservent leurs fichiers et
leurs maxima calculés. Avec un plafond déclaré de 16 mm³/s, des maxima de
16,1565 mm³/s (S) et 16,7116 mm³/s (M/L) passent de PASS historique à FAIL.
La tolérance physique de 5 % est supprimée ; seul l’arrondi numérique est admis.
Ce verdict porte sur les commandes du fichier, pas sur un débit physiquement mesuré.

- Retours humains sans préparation : capture privée vérifiable par SHA-256, sans création d'impression ni de préparation (#118).

- Connaissances : réussite contextuelle en PLA noir avec vitesses relevées et mode Sport, sources anonymisées et limites de réutilisation (#39, #40).

- Lecteur G-code commun : continuité et trajets de remplissage raccordés, interpréteur découpé et caches concurrents protégés (#13, lot de consolidation ; arcs toujours NON VÉRIFIÉS).

- Hooks : résolution du dossier à chaque appel et limite du filtre shell documentée (#21).
- Aide CLI : options, positionnels et sous-commandes des contrôles et fabrications délégués visibles sans atelier (#98).
- Relevé thermique : consignes G9111 et M104/M109 issues de l’interpréteur, avec ligne, cible et extinction (#99).
- Optimisation : projet relatif résolu depuis l’atelier choisi, indépendamment du dossier courant (#100).
- Routine : placement validé et erreur localisée avant les contrôles et la création des rapports (#104).

- Audit ACE : lecture des réglages projet par le dictionnaire typé et refus des clés JSON dupliquées (#58).

- Mise à jour de `cryptography` pour corriger les vulnérabilités signalées par Dependabot.
- Délais bornés pour quatre appels et verdict NON VÉRIFIÉ si le délai est dépassé (#59).
- Clôture du chantier de construction et suivi du développement par issues GitHub.
- CI macOS rétablie avec actions et uv épinglés.
- Sauvegarde et restauration : espace requis proportionnel aux données, avec plancher configurable (#60).

## 1.0.1

- `codes-sortie` (#1) — Harmonisation des verdicts des contrôles et signalement d’une ouverture du slicer à l’issue inconnue.
- `integrite-plaques` (#2) — Comparaison exacte de l’affectation des instances aux plaques et des liaisons d’assemblage.
- `debit-stopiteration` (#3) — Plaque sans index signalée NON VÉRIFIÉE et diagnostic d’usage pour les options incomplètes.
- `envoi-indexerror` (#4) — Lecture typée des cases de température et de débit et verdict NON VÉRIFIÉ si la case de l’extrudeur manque.
- `seance-verrou` (#5) — Jeton de fermeture, expiration, reprise et trace des fermetures forcées.
- `publication-commit` (#6) — Commit et export avant publication des fiches, reprise sans doublon et mesures réexportées.
- `delais-processus` (#7) — Délai borné pour les anciennes étapes, les lectures Git et le nettoyage du pilote Safari.
- `gcode-g28` (#8) — Référencement G28 pris en compte et axe E commun lors des changements d’outil.
- `u20-regex` (#9) — Lecture XML des objets et placements, transform facultatif et XML invalide signalé.
- `u36-sha` (#10) — Protection des copies renommées par les empreintes déclarées dans qualite.json.
- `frontiere-prive` (#11) — Contrôle Git de l’atelier privé réellement sélectionné.
- `trancher-temporaires` (#15) — ZIP relu avant remplacement atomique et nettoyage des échecs de restauration.
- `decisions-statuts` (#16) — Portée explicite des règles et confinement des preuves et références durables.
- `petits-defauts` (#22) — Arrondi des durées, diagnostic SystemExit, export UTF-8, initialisation SQLite et nettoyage du manifeste temporaire.
- `feuille-1-0-1` (#25) — Ordre de correction traçable et installation complète par défaut, vérifiée sur clone neuf.
- Installation neuve : Playwright inclus par défaut, verrou conservé et protections du dépôt actives dans le temporaire système.

## 1.0.0

- Atelier local en français pour concevoir, préparer et contrôler les impressions FDM.
- Dictionnaire typé des réglages, intégrité 3MF et contrôle des commandes machine.
- Témoins numériques S, M et L, revue indépendante et suivi des retours dans un atelier privé.
