# Commandes

Toutes passent par `uv run benchab`. `--prive CHEMIN` précède la famille de commandes.
`--help` décrit les arguments de la famille ; pour les adaptateurs à arguments
libres, `-- --help` transmet l’aide au module. Les sorties restent privées.
Le [guide de lecture des rapports](../guides/lire-les-rapports.md) indique les
options de résumé et de détail déjà couvertes.

| Famille | Usage |
| --- | --- |
| `init`, `etat`, `configuration` | Atelier et profil machine |
| `projet NOM --niveau S` | Projet transactionnel S/M/L |
| `seance debut`, `seance fin`, `seance etat` | Verrou logique et journal |
| `imports importer`, `geometrie`, `reparer`, `maillage-avance`, `echange-blender`, `placement` | Import, géométrie et placement explicitement déclaré |
| `conception cad-engine`, `assistant-projet`, `variantes`, `dfam` | CAO et variantes |
| `conception fiche-cao` | [Plan et script CAO depuis des cotes JSON](fiche-cao.md) |
| `conception trous`, `eprouvette-trous`, `vissage`, `decoupe` | Ajustements et assemblage |
| `conception filetage-nominal -- OPTIONS` | STL et rapport privé à partir de cotes déclarées en mm : filet triangulaire extérieur ou outil de soustraction avec jeu radial explicite ; hors norme ISO et sans preuve d’ajustement |
| `conception gyroide-temoin` | [STL et rapport d’un coupon gyroïde numérique](gyroide-temoin.md) |
| `fabrication trancher`, `revue-archive`, `preparation`, `optimisation`, `plaques-mixtes` | Préparation, revue d’archive et tranchage |
| `fabrication couches-variables`, `strategies-procede`, `resistance` | Études de procédé |
| `controle capacites-machine SOURCE.3mf --maitre PROFIL.json` | Demandes nominales, limites et contraintes numériques depuis deux entrées privées ; `--resume --sortie RAPPORT.json` garde le détail privé |
| `controle rejeux-gcode RECU1 RECU2 --plaque N` | Compare les G-code déjà publiés et distingue les octets, les dépôts de support et les agrégats ; rapport neuf facultatif |
| `controle plages-z` | Compare les plages de couches d’un 3MF à une intention Z explicite, en mm ; `--sortie` donne un résumé borné et un rapport privé, `--detail` ajoute le JSON complet à stdout |
| `fabrication exportable`, `rattachements`, `envois-slicer`, `retour`, `planning` | Livrables, historique et retours |
| `fabrication tracer-3mf SOURCE --sortie NOM.3mf` | Copie privée avec empreinte source facultative ; conservation native NON VÉRIFIÉE |
| `fabrication remplacer-maillage` | Remplace une géométrie directe identifiée par SHA dans une copie privée ; périmètre strict et slicer NON VÉRIFIÉ |
| `controle envoi`, `routine`, `regles`, `continuite-paroi` | Contrôles de préparation |
| `controle ecart-z-supports SOURCE.3mf [--plaque N]` | [Écarts Z nominaux des dépôts autour des supports](ecart-z-supports.md), lecture seule et sans preuve physique |
| `controle comparer-projets-3mf SOURCE CANDIDAT` | [Diagnostic en lecture seule](comparer-projets-3mf.md) des réglages, palettes et peinture littérale, sans conversion |
| `controle coutures` | [Contrôle et aperçu de coutures sur régions déclarées](../explications/coutures.md#contrôle-sur-des-régions-déclarées) |
| `controle preparer-reprise-export` | [Copie vérifiée d’une table exportée absente](plan-reparation-vues.md#préparer-la-reprise-dune-table-exportée), sans l’appliquer |
| `controle verifier-reprise-source --preuve FICHIER --sha256-preuve SHA` | Relit la preuve et la copie locales, distingue l’état de l’original sans appliquer ni rouvrir le TAR |
| `controle preparer-reprise-source` | [Copie non appliquée d’un original depuis un tar](plan-reparation-vues.md#préparer-une-copie-dun-original-depuis-une-sauvegarde), avec SHA d’archive et de membre |
| `controle appliquer-reprise-export` | Applique une table absente depuis une preuve CANDIDAT et son SHA explicitement déclaré |
| `controle surplombs-connexes SOURCE.stl --unite-mm [--plateau-z-mm Z [--dossier-apercu DOSSIER_NEUF]] [--portee-pont-mm PORTEE]` | Groupes de triangles orientés vers le bas ; visées ponctuelles vers un plan Z déclaré en mm et visionneuse privée en option, sans validation d’appui |
| `controle ponts-surplombs STL TRANCHE.3mf` | [Axes de pont projetés sur un îlot](../explications/surplombs-ponts-supports.md#comparer-un-îlot-à-une-plaque-choisie), avec SHA, plaque et translation déclarés |
| `controle entretien --historique FICHIER [--sortie RAPPORT]` | Bilan conditionnel depuis un historique privé explicite, sans SQLite ni action machine |
| `controle ecarts-profils-3mf` | Compare les réglages 3MF aux profils explicitement fournis ; rapport privé |
| `controle publication`, `frontiere`, `typographie` | Dépôt partageable |
| `qualite niveau1`, `niveau3`, `incrementiel`, `resume`, `tout`, `hooks`, `archives`, `comparer-fabrication` | Qualité logicielle et impact des tests |
| `visualisation visionneuse`, `serveur`, `apercu-gcode`, `commentaires`, `photo`, `retour-photo`, `inspection-scene`, `assemblage-dossier` | Inspection commune et suggestion privée de retour |
| `atelier diagnostic --projet NOM`, `tests-impact` | Capacités et navigation |
| `references verifier`, `analyser`, `contours` | Références liées aux preuves |
| `connaissances memoire`, `etudes`, `bobine`, `pdf` | Savoir, mesures et lecture PDF textuelle optionnelle |
| `connaissances incidents` | Lie une [fiche privée](fiche-incident.md) aux fichiers de tests d’un commit ; observation CI GitHub opt-in |
| `donnees impressions`, `vues`, `reconstruire`, `sauvegarder`, `restaurer` | Requêtes et reconstruction |
| `machine profils-cibles`, `affectations-ace` | Profils de travail et correspondance des filaments |
| `machine cloud-lecture`, `telemetrie`, `session-compte` | Lecture Cloud ; tests simulés, autorisation réseau distincte |
| `vote --preparer`, `vote DOSSIER`, `vote --etat` | [Conseil et relecture](vote.md) |

Les attestations ACE sont liées à l’empreinte du contexte d’audit, qui inclut
le code. Après une modification de l’audit, les anciennes attestations restent
des preuves historiques et ne qualifient pas automatiquement un rapport neuf.
Ne changez pas leur empreinte pour les faire accepter.

Voir le format et les limites du [contrôle des plages Z](plages-z.md).
Les limites de la cartographie STL sont décrites dans
[Surplombs, ponts et supports](../explications/surplombs-ponts-supports.md).

Le placement traçable depuis une source importée est décrit dans [son contrat](placement.md).

Les contrôles spécialisés supplémentaires sont listés par `uv run benchab controle --help`.
Les cibles Make des projets générés utilisent ces mêmes modules ; elles ne créent
pas une deuxième chaîne de contrôle.

La [lecture PDF textuelle](lecture-pdf.md) reste bornée et ne fait ni OCR ni rendu.

La comparaison des réglages 3MF aux profils explicitement fournis est décrite dans
[le guide des écarts de profils](ecarts-profils-3mf.md).

`benchab visualisation assemblage-dossier DOSSIER --unites mm --sortie prive/vue.html`
collecte les STL d’un dossier privé. L’ancien `assembly.json` voisin est accepté :
`primary` associe les noms de STL à 16 valeurs de matrice col-major ; `extras`
contient des instances `{name, src, asm}`. La même visionneuse fournit une
disposition en grille et les poses assemblées ; les extras sont masqués dans
la grille. Sans dossier explicite, la recherche reste dans le privé (d’abord
`output/stl`, puis `stl`, puis la racine courante privée). Les dimensions sont
en mm déclarés. Aucun montage ou ajustement physique n’est validé par cette vue.
