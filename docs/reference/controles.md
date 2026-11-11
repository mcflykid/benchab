# Contrôles et statuts

PASS : le critère numérique testé est satisfait. WARN : un seuil mérite revue.
FAIL : le critère est violé. NON VÉRIFIÉ : une donnée ou une preuve manque.
Un statut global ne supprime pas les détails et les zones restant à vérifier.

`uv run benchab controle envoi FICHIER` rassemble les contrôles du fichier final.
Les options `--declaration`, `--geometrie`, `--revue-locale` et `--reference`
rattachent leurs preuves. `--relation-reference` distingue même source de modèle,
seulement même machine/filament, et relation inconnue.
Sans option, la commande conserve l’affichage JSON complet historique. Avec
`--sortie CHEMIN_PRIVÉ_NEUF`, elle sauvegarde ce rapport complet dans l’atelier
privé et affiche un résumé compact trié par FAIL, NON VÉRIFIÉ puis WARN ; les
noms de contrôle et numéros de plaque disponibles restent visibles. Le champ
`rapport` indique le fichier relatif à l’atelier ; les messages et chemins
détaillés restent dans ce fichier. `--detail` affiche le rapport complet dans stdout, y
compris lorsqu’une sortie fichier est demandée. Le chemin de sortie doit être
privé et neuf ; une destination existante ou un lien est refusé.
Le rapport conserve les [avertissements natifs du slicer](avertissements-slicer.md),
y compris les codes inconnus et les origines non vérifiées. Seul le verdict
global PASS active `conforme_numerique` ; le statut historique reste séparé.
Les constantes et critères vérifiables sont reliés dans [les règles](regles.md).
Le [contrat commun du débit U25](debit.md) compare chaque dépassement établi
au plafond déclaré ; le percentile 99 reste informatif.
Le [diagnostic d’installation](diagnostic-installation.md) reste en lecture seule.
Sa sonde `git check-ignore` est une exception au lanceur avec captures : elle
utilise un délai de 2 s et `GIT_OPTIONAL_LOCKS=0`, sans créer de fichier de diagnostic.
La routine vérifie le [contrat de placement](placement.md) avant le premier
contrôle ; une déclaration invalide donne une erreur localisée sans créer ses rapports.

Le contrôle d’envoi accepte en option le champ `buse.diametre_mm` (nombre en
mm) dans la déclaration. Ce bloc active la comparaison avec le diamètre de
buse typé du 3MF et, si les valeurs HRC sont présentes, la comparaison de
`nozzle_hrc` à `required_nozzle_HRC` pour chaque filament utilisé. Un diamètre
différent ou une dureté embarquée sous l’exigence donne `WARN` ; les données
absentes ou ambiguës donnent `NON VÉRIFIÉ`. Le bloc doit contenir uniquement un
`diametre_mm` numérique strictement positif ; un bloc nul ou malformé rend tous
les contrôles de buse `NON VÉRIFIÉ`. La forme typée de `nozzle_diameter` a été
relue le 2026-11-11 ; la date générale du dictionnaire reste celle de sa collecte.
Sans ce bloc, les anciens rapports
gardent leurs contrôles et leur forme. Ces comparaisons portent sur les valeurs
des fichiers ; elles ne mesurent pas le matériel monté et n’établissent aucune
compatibilité physique.

Les familles couvrent : plaques CLI/interface, réglages, pièces, paquet 3MF,
finition/parois, débit, appuis, première couche, décollement, ponts internes,
colonnes isolées, couches courtes, supports requis, orientations, disposition,
ergots et assemblage. Les modules partagent un lecteur G-code indexé par SHA-256.
Le contrôle des [couches courtes](couches-courtes.md) analyse un STL avant tranchage ;
ses recouvrements de coupes ne remplacent ni `colonnes-isolees` ni l’analyse
d’appui des trajectoires G-code.
Une revue locale ne vaut que pour la zone et l’empreinte qu’elle vise ; un fichier
modifié invalide sa preuve. Le contrôle de [continuité](continuite-paroi.md)
utilise des seuils absolus et précise la portée de toute référence.

Le [contrôle des changements d’outil](purge-changements.md) sépare les
transitions écrites dans le G-code et le calcul conditionnel d’une matrice.
Il ne mesure pas une purge physique.

Une opportunité de découpe n’est ni une coupe calculée ni une validation de
connecteur. Les [contrôles propres à la découpe](../guides/decoupe.md) sont distincts.
Le contrôle de publication est décrit dans le [guide développeur](../guides/developpement.md).

Les rapports explicites des contrôles de débit, réglages, supports,
décollement, ergots, ponts, première couche, colonnes et géométrie sont publiés
dans un fichier neuf, sous l’atelier privé ou dans un temporaire système hors
dépôt. Un fichier existant ou un lien est refusé. Les G-code conservés avec
`supports-requis --garder` sont traités comme des preuves neuves, plaque par
plaque. Une étude d’orientation publie ensemble son rapport et ses STL dans
un dossier neuf. Le renouvellement explicite de la revue locale avec
`--appliquer` conserve les décisions humaines et refuse une sortie liée au
tranchage, à la déclaration d’envoi ou à l’autre sortie.

La routine lit uniquement les résultats du contrôle courant. Relancer la
routine conserve les fichiers identiques et publie les fichiers différents
dans un sous-dossier de rejeu neuf. Les anciennes preuves restent consultables.
Si aucun rapport n’a été produit, le résultat le signale et indique le journal
de cette exécution.


## Reconstruction et réimpression

```sh
uv run benchab qualite archives prive/projets/ma-piece prive/mesures/inventaire.json
uv run benchab qualite comparer-fabrication -- --attendu prive/references/ma-piece --obtenu prive/projets/ma-piece/candidats --sortie prive/mesures/comparaison.json
uv run benchab controle reimpression -- --projet prive/projets/ma-piece
```

L’inventaire ne suit aucun lien et n’extrait aucune archive. Les budgets et
inspections incomplètes restent explicites et ne produisent pas PASS. Le rapport
doit être neuf, privé et hors de la source. Le comparateur distingue géométrie,
fabrication, identité binaire du conteneur et documentaire ; aucune différence
de G-code ou de réglage n’est ramenée à une simple date. Même un conteneur ZIP
à contenu identique garde son écart d’identité binaire dans le rapport.
Le contrôle de réimpression vérifie le verrou local et les empreintes, sans
transformer leur identité en accord d’envoi ou en observation physique.

Les contrôles historiques `benchab qualite niveau1` et `niveau3` publient les
rapports JSON/Markdown et, pour le niveau 3, `plaques.json` comme un groupe neuf.
Un rejeu aux octets identiques ne réécrit rien. Si le résultat diffère, la
nouvelle version est rangée dans `preuves/qualite/rejeu-<sha256>/` et son
emplacement est annoncé. L’empreinte porte sur tous les noms et octets du groupe ;
un résultat déjà conservé est relu et vérifié. Un groupe de rejeu incomplet ou
altéré est conservé et signalé. Un ancien tableau seul ne devient jamais celui
d’un contrôle interrompu. Les destinations liées, les collisions avec les
entrées et les préparations figées sont refusées avant publication.


Le contrôle `premiere-couche` accepte `--json RAPPORT --resume` pour afficher
cinq plaques prioritaires et jusqu’à cinq défauts par plaque. Le résumé
signale les omissions et donne le chemin du rapport neuf. Un diagnostic long
est tronqué à l’écran ; son texte intégral et sa plaque restent dans le JSON.
Avec `--detail`, l’affichage reproduit le rapport complet. Sans ces options,
l’affichage historique est conservé (suite de #35).
Les arcs G-code restent NON VÉRIFIÉS par défaut. L’option explicite
`--arcs-planaires` qualifie uniquement les arcs G17 déposés à Z physique
constant dans les deux premières couches : la couverture est calculée par
distance analytique au cercle et à son intervalle angulaire déposé, sur la
même grille numérique que les lignes, avec une limite cumulée de deux millions
de cellules candidates et de 8 192 arcs retenus ; le parseur limite ses
diagnostics à 128. Le Z physique doit correspondre au marqueur de couche à
0,0005 mm près. Les extrémités doivent préserver le rayon à une tolérance
absolue de 0,000000001 mm, sans tolérance relative. Les arcs sans échantillon
couvert, les hélices, autres plans ou contextes non pris en charge restent
NON VÉRIFIÉS. Cette mesure numérique ne prouve ni l’adhérence ni la tenue
physique de la pièce.

## Contrôles privés d’un projet L

Les fichiers `src/controles/*.py` exposent `verifier(projet)` et renvoient un
statut. Le pilote `src.verifier` conserve un rapport individuel JSON sous
`preuves/controles/` avec les sorties complètes ; les erreurs d’import et
d’exécution donnent FAIL avec leur pile. Le rapport agrégé est
`preuves/qualite-projet.json`. Ces rapports neufs remplacent les anciens logs
texte de sous-processus ; un rapport existant n’est jamais écrasé.
Un résultat inconnu reste NON VÉRIFIÉ (code 3), FAIL donne 2 et WARN donne 1.
Un exemple sans mécanisme déclaré reste NON APPLICABLE. Les valeurs et les SHA
d’un partenaire déclaré imprimé sont des contrôles de fichier, sans validation
physique de l’ajustement.

Le contrôle complet borne chaque lancement externe (Ruff et pytest) à 1 800 s,
arrête son groupe de processus en cas de dépassement et conserve les journaux entiers.
Un code de découverte pytest 5 reste FAIL. La RSS par étape est une somme de RSS
du groupe échantillonnée, pas un pic d'allocation exact ; les maxima du processus
appelant et de ses enfants cumulés sont présentés séparément.

`benchab qualite syntaxe-privee PROJET` vérifie les scripts Python actifs de
`prive/projets/PROJET` hors dépendances et archives de projet, sans les exécuter ni créer de `.pyc`. Un script illisible
ou invalide donne FAIL ; l'absence de script reste NON VÉRIFIÉE. Le projet doit
être choisi explicitement et les liens internes restent refusés.

## Codes de sortie des verdicts

Les rapports numériques de l’envoi, des règles, de la continuité, de la routine,
du contrôle complet et de la chaîne portent le code que retourne leur commande.

| Verdict | Code |
| --- | ---: |
| PASS | 0 |
| WARN ou WARNING | 1 |
| FAIL | 2 |
| NON VÉRIFIÉ | 2 |
| INCONNU, BLOQUE ou ATTENTE HUMAINE | 2 |

Lire le statut du rapport pour distinguer les situations qui partagent le code
2. Les codes natifs des processus enfants restent disponibles dans leurs traces.

Les étapes de `qualite tout` sans délai configuré disposent de 1 800 s.
Les lectures de l’index et des blobs Git disposent de 10 s. Un dépassement
échoue avec un motif et arrête le groupe créé pour la commande. Le pilote
Safari de recette est lui aussi arrêté dans son propre groupe.

Les lectures de débit et de continuité suivent une position E et une dette
de rétraction communes à l’axe E. La sélection Tn identifie le filament.
`G92 E` change le repère sans acquitter la rétraction. Un référencement
`G28` remet les axes demandés à l’origine nominale. Un segment de dépôt
immédiat après référencement en cours de trajectoire est exclu et signalé.

Pour `controle regles`, les résultats évalués et `hors_portee` sont séparés.
Les règles hors portée ne comptent pas dans le code global. Une donnée
manquante dans un contrôle exécutable garde le code NON VÉRIFIÉ.

Les mesures de recul/recouvrement déclarées pour une revue de zone suivent le
contrat explicité dans [mesures locales de recul et recouvrement](mesures-recul-recouvrement.md).
Elles sont recalculées depuis les membres G-code de plaque avant acquittement.

### Intervalles des arcs arrondis

`benchab.gcode.arcs_arrondis.intervalle_xy` est une sonde numérique de G17.
Elle reçoit les positions XY absolues, les décalages I/J relatifs, le sens
G2/G3 et un demi-pas déclaré en mm. Pour chaque axe, le vecteur radial de
départ hérite d’une erreur de champ ; celui d’arrivée en cumule trois.
L’intersection des intervalles radiaux et les bornes du balayage encadrent
la longueur XY d’un cercle éventuel. Si la direction est indéterminée,
les rayons sont incompatibles ou l’angle peut franchir zéro ou un tour,
la sonde ne fournit pas de longueur.

Pour établir la compatibilité des champs, la sonde calcule exactement les
extrema de la différence des rayons au carré. Avec `q = arrivée − départ`
et `d = (I, J)`, cette différence vaut `2 q·d − |q|²`. La preuve porte
sur la représentation décimale courte des nombres reçus, pas sur les
lexèmes d’une ligne G-code. Ces décimales sont interprétées comme des
fractions : chaque composante de `q` varie à ± deux demi-pas, et celle de
`d` à ± un demi-pas. Si les
extrema entourent strictement zéro, il existe un cercle dans les bornes ;
une égalité nominale exacte suffit aussi. Un contact isolé en frontière
ne qualifie pas l’arrondi, et si les extrema excluent zéro, la sonde
ne fournit pas de longueur.

Le statut reste **NON VÉRIFIÉ** : un cercle compatible avec l’hypothèse
ne prouve ni le cercle d’origine, ni le mouvement du firmware. Le lecteur
exact, les contrôles géométriques et U25 ne consomment pas ces intervalles
(#134).

Sur un cylindre tranché avec ASN 2.0.0.3, les 700 arcs observés ont un
cercle compatible avec l’hypothèse d’un demi-pas de 0,0005 mm par champ.
L’intervalle de longueur XY le plus large mesure 0,01450 mm. Le lecteur
strict refuse toujours les mêmes 697 arcs de cette sonde. Ce résultat
n’identifie pas le code du firmware et ne modifie aucun seuil.
