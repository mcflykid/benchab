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
| `conception apercu -- produire` / `decider` | Page HTML privée et décision visuelle sur les deux empreintes |
| `conception greffer-interface -- OPTIONS` | Greffe d’un filet femelle identifié et [contrôles du STL relu](fixations-nominales.md#greffer-un-filet-femelle-existant) |
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
| `visualisation atelier-preparation -- SOURCE --sortie DOSSIER_NEUF` | Atelier local avec carte des risques et résumé depuis le fichier de référence. Ajouter `--unites mm` pour un STL ou un OBJ en millimètres. Pour un 3MF de préparation admissible, `--comparer` retranche l’original et les variantes avec le même moteur. `--avec-marge` ajoute un essai à 90 % du plafond. `--qualite-supports` compare des couches fixes plus fines et les supports automatiques si les réglages le permettent. `--orientation` compare la pose d’origine et une autre face pour une instance admissible. Ajouter `--piece 'Nom · 2'` sur un 3MF à plusieurs pièces. `--exporter-variantes` conserve les 3MF de préparation relus et leur preuve d’intégrité dans le rapport. Le résumé indique les écarts estimés en durée et en matière, le verdict U25 et les pics de débit par plaque. Voir les [astuces d’impression à qualifier](astuces-impression.md). |
| Options de l’atelier de préparation | `--comparer --intention rapide` rapproche les vitesses intérieures des limites machine et filament embarquées. `--intention supports` compare les types de supports avec la référence. U25 peut écarter une variante après tranchage et empêcher son export. Le rapport distingue consommation estimée et dépôts commandés par filament. `--exporter-reference` fournit une copie à l’octet près, y compris en mode vase. `--analyser-plaque N` ou `--analyser-piece N` limite les mesures en gardant les pièces visibles. |
| Dépôt d’un 3MF dans l’atelier | BENCHAB tranche une copie temporaire et compare les variantes admissibles à la référence sans créer de projet. Quand aucun changement n’est proposé, il vérifie le débit de la référence. Un 3MF sans réglages de tranchage reste consultable en 3D. Cibler une plaque ou une pièce à l’import garde l’aperçu complet et saute le tranchage automatique. Le rapport distingue les réglages relus des commandes du G-code inchangées par plaque. |
| Supports depuis le plateau | Choisir « Comparer avec les supports depuis le plateau » ou `--comparer --intention supports-plateau` si la préparation autorise déjà les supports depuis la pièce. Le rapport compare les appuis au tranchage. Vérifier séparément dans l’aperçu que les branches peuvent sortir des creux. |
| Profil haut débit | Placer une copie du profil de filament choisi sous `prive/config/profils-debit/SHA256_DU_3MF.json`, puis choisir « Comparer le profil haut débit déclaré » ou `--comparer --intention haut-debit`. Le profil doit viser l’imprimante, le même filament et les mêmes réglages d’extrusion et de température. Seul le plafond volumique augmente. Le rapport conserve l’empreinte du profil et indique si les commandes ont changé. |
| Superposition des appuis | Dans le résumé d’une comparaison, choisir le tranchage et une couche. Le cyan suit les parcours de support déposés, l’orange repère les parcours de pièce sans appui nominal et un maillage filaire indique la pose alternative. Disponible sur la première plaque à extrudeur unique sans décalage, avec un budget de points par catégorie. Le résumé indique les tracés omis. |
| Constats dans l’atelier | Depuis une préparation conservée, choisir le défaut et la pièce, puis confirmer le constat dans « Examiner un défaut observé ». Il apparaît dans « Observations » avec la préparation, l’empreinte du 3MF, les profils de filament déclarés et, si fournis, le repère et l’empreinte de la photo. Les hypothèses restent à vérifier. |
| `atelier diagnostic --projet NOM`, `tests-impact` | Capacités et navigation |
| `references verifier`, `analyser`, `contours` | Références liées aux preuves |
| `connaissances memoire`, `etudes`, `bobine`, `pdf` | Savoir, mesures et lecture PDF textuelle optionnelle |
| `connaissances incidents` | Lie une [fiche privée](fiche-incident.md) aux fichiers de tests d’un commit ; observation CI GitHub opt-in |
| `donnees impressions`, `vues`, `reconstruire`, `sauvegarder`, `restaurer` | Requêtes et reconstruction |
| `donnees imputer-bobine` | [Imputation privée d’une pesée confirmée](plan-numerique-filament.md#appliquer-une-pesée-confirmée-au-stock) |
| `machine profils-cibles`, `affectations-ace` | Profils de travail et correspondance des filaments |
| `machine export-profil -- --parent PARENT.json --reglages DELTA.json --nom NOM --dossier DOSSIER` | Export différentiel privé JSON et INFO pour un procédé Anycubic ; chargement hors ligne ou import manuel |
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
une demi-largeur de ligne. Le rapport localise le maximum, donne le 99e centile
pondéré par aire et les aires par tranche d’angles. Un dépassement vaut WARN
pour la zone. Une zone sans face descendante reste NON VÉRIFIÉE. Pour les
îlots connexes et l’accès au plateau, utiliser `surplombs-connexes`.

## Estimer le restant RFID d’une bobine

`benchab donnees releve-ace RELEVE.json --requis-g 230 --marge-g 25 --sortie ESTIMATION.json`
enregistre une estimation privée à partir d’un relevé daté. Le JSON déclare
`position_ace`, `matiere`, `restant_pct` de 0 à 100, `date` ISO avec fuseau
et `source` (`lecture_cloud` ou `observation_utilisateur`). Renseigner
`masse_nominale_g` pour convertir le pourcentage en grammes. La masse requise
et la marge minimale sont facultatives et se déclarent ensemble. Une marge
égale ou inférieure au seuil donne WARN. Le rapport conserve le relevé
d’origine et marque l’incertitude NON VÉRIFIÉE. La pesée et le stock SQLite
suivent le parcours distinct d’imputation.

Pour un aperçu de candidate sans anciens maillages, ajouter
`--declaration APERCU.json` à `visualisation assemblage-dossier`. La fiche
contient `schema: 1`, un objet `candidate` et une liste `pieces` qui peut
être vide. Chaque entrée donne le nom simple `fichier` du STL du dossier
et son `sha256`. Un STL supplémentaire, un fichier manquant ou une
empreinte différente bloque l’aperçu. La vue indique la provenance de
chaque modèle et de ses instances.

`benchab conception registre-variantes REGISTRE.json --sortie BILAN.json`
vérifie la liste des explorations privées. Chaque entrée déclare un
`id`, un `parent` déjà présent ou `null`, le `changement` demandé, une
`source` paramétrée et un `apercu` éventuel. Les preuves comportent un
`fichier` relatif au registre et son `sha256`. La `decision` vaut
`EN ATTENTE`, `VALIDÉE`, `À AJUSTER` ou `ABANDONNÉE`, avec une `raison`
pour toute décision prise. Le champ facultatif `candidate` exige un
aperçu identifié et une décision validée. Trois candidates peuvent
figurer dans le registre. Le rapport liste les explorations abandonnées
dont les exports dérivés peuvent être examinés.

## Aperçu d’une coque allumée

`benchab visualisation apercu-lampe COQUE.stl [VARIANTE.stl] --source-mm X Y Z
--attenuation-mm-inv COEFFICIENT --sortie DOSSIER_NEUF` produit une carte
déroulée et une vue 3/4 par coque, avec un rapport JSON privé. Les STL
doivent être étanches, orientés et dans le même repère en mm. L’axe Z est
vertical. L’option `--source-mm` peut être répétée pour échantillonner une
source étendue. Chaque point a le même poids dans la puissance relative.

Le modèle additionne la lumière directe de chaque source, avec une
décroissance en carré inverse de la distance et une atténuation exponentielle
selon la longueur traversée dans la matière. Les deux candidates partagent
la même échelle de couleur. Le gris indique un trajet indéterminé et le
rapport indique la couverture. L’atténuation est une hypothèse à déclarer.
Le modèle ignore diffusion, réflexion et émission du matériau luminescent.
La validation physique reste NON VÉRIFIÉE.

## Fente ou vide dans une interface

`benchab controle fentes-interface PIECE.stl --zone-mm XMIN YMIN ZMIN XMAX YMAX ZMAX --axe x --largeur-ligne-mm 0.42 --pas-mm 0.10`
cherche les intervalles vides entre deux parois sur un axe XY déclaré.
Les coordonnées suivent le repère du STL en mm. Le seuil vaut deux largeurs
de ligne. Le maillage fermé est échantillonné par coupes XY sur une grille
bornée à trois millions de cellules. La commande rapporte les positions et
l’étendue verticale des intervalles fins retenus.

Une fente connectée à l’extérieur sur la grille produit WARN. Une cavité
sans connexion échantillonnée produit FAIL si la zone est déclarée porteuse
avec `--porteuse`. Une mesure proche du seuil, une ouverture détectée
seulement en diagonale ou un budget dépassé donne NON VÉRIFIÉ. Une fente
plus fine que le pas peut échapper au contrôle.
Les options `--json` et `--coupe` enregistrent sous l’atelier privé le
rapport et, si un vide est repéré, une coupe PNG au niveau du défaut.

## Course de vissage

`benchab controle vissage MALE.stl FEMELLE.stl --pas-mm 4 --sens droite --butee-mm 0
--phase-deg 0 --course-mm 8 --increment-mm 0.25 --zone-male-mm 0 12
--zone-femelle-mm 0 14` mesure les intersections aux poses échantillonnées.
Les STL partagent l’origine XY et un axe Z de vissage. La pièce mâle part à
`butee + course` et descend jusqu’à la butée. Vu depuis +Z, le sens droite
correspond à une rotation horaire pendant cette descente. La phase indique
la rotation à la butée dans le repère direct du modèle.

Les zones Z sont déclarées dans le repère de chaque fichier. Le calcul
d’intersection utilise les volumes coupés à ces hauteurs. Il rapporte aussi
les essais avec une phase décalée de 180°, un sens inversé et une pose
au-delà de la butée. Le jeu radial est échantillonné à la butée quand le
mâle occupe l’axe et que l’alésage femelle est ouvert sur l’axe aux plans
analysés. La limite de 0,01 mm³ s’applique aux intersections numériques.
Le verdict couvre les poses échantillonnées et les zones déclarées.
L’option `--json` enregistre un rapport privé avec les empreintes des STL.
