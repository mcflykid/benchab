# Commandes

Toutes passent par `uv run benchab`. `--prive CHEMIN` précède la famille de commandes.
`--help` décrit les arguments de la famille ; pour les adaptateurs à arguments
libres, `-- --help` transmet l’aide au module. Les sorties restent privées.

| Famille | Usage |
| --- | --- |
| `init`, `etat`, `configuration` | Atelier et profil machine |
| `projet NOM --niveau S` | Projet transactionnel S/M/L |
| `seance debut`, `seance fin`, `seance etat` | Verrou logique et journal |
| `imports importer`, `geometrie`, `reparer`, `maillage-avance`, `echange-blender`, `placement` | Import, géométrie et placement explicitement déclaré |
| `conception cad-engine`, `assistant-projet`, `variantes`, `dfam` | CAO et variantes |
| `conception fiche-cao` | [Plan et script CAO depuis des cotes JSON](fiche-cao.md) |
| `conception trous`, `eprouvette-trous`, `vissage`, `decoupe` | Ajustements et assemblage |
| `fabrication trancher`, `revue-archive`, `preparation`, `optimisation`, `plaques-mixtes` | Préparation, revue d’archive et tranchage |
| `fabrication couches-variables`, `strategies-procede`, `resistance` | Études de procédé |
| `controle plages-z` | Compare les plages de couches d’un 3MF à une intention Z explicite, en mm ; `--sortie` donne un résumé borné et un rapport privé, `--detail` ajoute le JSON complet à stdout |
| `fabrication exportable`, `rattachements`, `envois-slicer`, `retour`, `planning` | Livrables, historique et retours |
| `fabrication tracer-3mf SOURCE --sortie NOM.3mf` | Copie privée avec empreinte source facultative ; conservation native NON VÉRIFIÉE |
| `fabrication remplacer-maillage` | Remplace une géométrie directe identifiée par SHA dans une copie privée ; périmètre strict et slicer NON VÉRIFIÉ |
| `controle envoi`, `routine`, `regles`, `continuite-paroi` | Contrôles de préparation |
| `controle surplombs-connexes SOURCE.stl --unite-mm` | Groupes de triangles orientés vers le bas ; rapport numérique sans validation d’appui |
| `controle entretien --historique FICHIER [--sortie RAPPORT]` | Bilan conditionnel depuis un historique privé explicite, sans SQLite ni action machine |
| `controle ecarts-profils-3mf` | Compare les réglages 3MF aux profils explicitement fournis ; rapport privé |
| `controle publication`, `frontiere`, `typographie` | Dépôt partageable |
| `qualite niveau1`, `niveau3`, `incrementiel`, `resume`, `tout`, `hooks`, `archives`, `comparer-fabrication` | Qualité logicielle et impact des tests |
| `visualisation visionneuse`, `serveur`, `apercu-gcode`, `commentaires`, `photo`, `inspection-scene`, `assemblage-dossier` | Inspection commune |
| `atelier diagnostic --projet NOM`, `tests-impact` | Capacités et navigation |
| `references verifier`, `analyser`, `contours` | Références liées aux preuves |
| `connaissances memoire`, `etudes`, `bobine`, `pdf` | Savoir, mesures et lecture PDF textuelle optionnelle |
| `connaissances incidents` | Lie une [fiche privée](fiche-incident.md) aux fichiers de tests d’un commit, sans exécution |
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
