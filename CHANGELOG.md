# Changements

Historique des versions publiques ; les tags Git identifient les candidates livrées.

## À venir — 1.5.0

- Envois : refuse les plaques homonymes et lit les masses et outils en flux, avec CRC complet et copie vérifiée par SHA-256 (#147, suite de #122).

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
