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
| `conception cad-engine`, `assistant-projet`, `dfam` | CAO et analyse numérique |
| `conception variantes` | Compare les pièces nommées ; JSON et Markdown publiés dans un dossier neuf de l’atelier privé |
| `conception fiche-cao` | [Plan et script CAO depuis des cotes JSON](fiche-cao.md) |
| `conception trous`, `eprouvette-trous`, `vissage`, `decoupe` | Ajustements et assemblage |
| `conception filetage-nominal -- OPTIONS` | STL et rapport privé en mm, profil sur mesure ou [profil métrique ISO nominal](fixations-nominales.md#profil-métrique-iso-nominal), avec jeu radial déclaré pour l’outil intérieur |
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
| `controle paquet-3mf ARCHIVE.3mf` | [Inventaire structurel](integrite-3mf.md#inspecter-la-structure-dun-paquet) avec résumé et rapport privé en option |
| `controle couches-courtes SOURCE.stl` | [Carte des îlots](couches-courtes.md) avec résumé des trois premiers et rapport complet en option |
| `controle coutures` | [Contrôle et aperçu de coutures sur régions déclarées](../explications/coutures.md#contrôle-sur-des-régions-déclarées) |
| `controle preparer-reprise-export` | [Copie vérifiée d’une table exportée absente](plan-reparation-vues.md#préparer-la-reprise-dune-table-exportée), sans l’appliquer |
| `controle verifier-reprise-source --preuve FICHIER --sha256-preuve SHA` | Relit la preuve et la copie locales, distingue l’état de l’original sans appliquer ni rouvrir le TAR |
| `controle appliquer-reprise-source` | [Recrée un original manquant avec identité confirmée](plan-reparation-vues.md#appliquer-une-reprise-à-un-seul-original-manquant) |
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
| `donnees imputer-bobine` | [Imputation privée d’une pesée confirmée](plan-numerique-filament.md#appliquer-une-pesée-confirmée-au-stock) |
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

## Affleurement nominal

`benchab controle affleurement montage.json --coupe prive/mesures/montage.svg`
compare des enveloppes prismatiques déclarées en mm. Le repère associe trois
axes XYZ distincts à la longueur, la largeur et la profondeur de montage.

```json
{
  "schema": 1,
  "repere_montage": {"longueur": "x", "largeur": "y", "profondeur": "z"},
  "languette_mm": [15, 4, 3],
  "rainure_mm": [16, 4.2, 3],
  "tolerance_affleurement_mm": 0.02
}
```

Un désaffleurement positif indique une saillie, un désaffleurement négatif un
retrait. Les jeux indiquent la différence totale entre les deux largeurs ou
longueurs. Les résultats portent sur les cotes déclarées avec la languette
en appui au fond. L’insertion réelle et l’affleurement imprimé restent à
observer.

## Premier diagnostic Cloud Anycubic

Une copie de configuration explicitement fournie se vérifie avec
`benchab machine cloud-lecture --inspecter-session --copie-session CHEMIN`.
Pour le compte autonome déjà configuré, utiliser
`benchab machine cloud-lecture --cloud --compte` pour lister les machines,
puis ajouter `--notre-machine` pour une unique Kobra S1 ou `--machine ID`
pour sélectionner une machine précise. La lecture rapporte les données ACE
disponibles. Les raisons publiables indiquent notamment une copie manquante,
une configuration de compte manquante ou un compte situé dans le dépôt.

## Répartition des dépôts et carte des coutures

```sh
benchab controle bilan-depots TRANCHE.3mf --borne-z 4 --borne-z 12 --sortie BILAN.json
benchab controle carte-coutures TRANCHE.3mf --centre-xy 125 125 --comparaison AUTRE.3mf --sortie CARTE.json --svg CARTE.svg
```

Le bilan groupe les dépôts par plaque, objet étiqueté, type de trajet, outil
et zone Z. Une borne Z (mm) ouvre la zone suivante. Un mouvement est classé
sur son altitude finale. La masse dépend du diamètre et de la densité
déclarés. Le temps nominal porte sur le dépôt à l’avance programmée. Pour
visualiser les trajectoires d’une couche, ouvrir l’archive avec
`benchab visualisation apercu-gcode`.

La carte relève le premier dépôt de paroi extérieure par couche et objet
étiqueté. Le centre XY en mm est commun aux archives comparées. Le rapport
donne la largeur du plus petit arc contenant 95 % des départs, la
concentration circulaire (de 0 à 1), les secteurs de 10° occupés et la part
des départs qui montent ou descendent en Z. Une variation en Z est un indice
de raccord en biseau. L’aperçu comporte un panneau par archive.

## Surplomb par zone d’un maillage

`benchab controle surplombs-surface CANDIDATE.stl --unite-mm --zones ZONES.json --sortie RAPPORT.json`
mesure l’angle depuis la verticale à partir des normales tournées vers le
bas. Déclarer les zones comme une liste d’objets JSON avec `nom`, `type`,
`source` et `boite_mm` (coin minimal XYZ, coin maximal XYZ). Les zones
sélectionnent les centroïdes des triangles. Une zone `paroi_fine` déclare
`angle_max_deg`. Pour une `zone_pleine`, déclarer `hauteur_couche_mm` et
`largeur_ligne_mm`. Le seuil est atteint quand le décalage par couche vaut
une demi-largeur de ligne. Le rapport localise le maximum, donne le p99
pondéré par aire et les aires par tranche d’angles. Un dépassement vaut WARN
pour la zone. Une zone sans face descendante reste NON VÉRIFIÉE. Pour les
îlots connexes et l’accès au plateau, utiliser `surplombs-connexes`.
