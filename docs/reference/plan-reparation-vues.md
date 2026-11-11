# Plan de régénération des vues privées

`benchab controle plan-reparation` relit la base privée SQLite en mode `ro` et
`query_only`, puis compare les rendus calculés par `donnees.vues._rendre`,
`_fiches_filaments` et `_manifeste`. La commande n'ouvre pas `Base`, n'enregistre
pas de mesure et n'écrit aucun fichier.

Le JSON distingue `PRESENTE_IDENTIQUE`, `ABSENTE`, `DIVERGENTE`,
`LIEN_A_VERIFIER` et `A_VERIFIER`. Une divergence signifie seulement que les
octets présents diffèrent du rendu actuel de la source SQLite ; elle ne permet
pas de conclure à une falsification. Le README est comparé dans sa section
marquée uniquement ; le reste du README n'entre pas dans la comparaison.
Les textes lus au-delà de 2 Mio ou sans UTF-8 valide restent à vérifier ;
l'application refuse aussi une vue antérieure qui dépasse cette borne.

Les actions proposées contiennent la cible privée relative, son SHA antérieur
ou `ABSENTE`, l'identité de la base, le SHA-256 attendu, l'empreinte logique du
rendu et les préconditions d'application. Un lien ou une structure ambiguë reste
à vérifier sans suivre sa cible. Une base absente, illisible ou un contenu
indécidable renvoie `NON VÉRIFIÉ` et aucune action correspondante. Les fichiers
sans source canonique ne sont pas proposés à la réparation.

Pour un lien symbolique relatif cassé présent à la place d'une vue canonique,
l'opérateur peut fournir un ou plusieurs chemins candidats explicitement relatifs à l'atelier
privé avec `--candidat-lien`. Le plan lit seulement les fichiers réguliers sans
lien, de 2 Mio au plus, et compare leur SHA au rendu SQLite. Un unique SHA exact
produit `propositions_liens` avec `PROPOSER_LIEN_CANDIDAT_SHA_EXACT` ; aucune
copie ou modification du lien n'est effectuée. Plusieurs candidats identiques,
un candidat absent, lié, externe, trop grand ou divergent laissent le lien
`LIEN_A_VERIFIER` et le plan `NON VÉRIFIÉ`. La preuve établit seulement
l'identité des octets avec le rendu actuel : elle ne prouve pas l'historique,
l'origine du fichier ni sa pertinence comme sauvegarde. Les fichiers manquants
sans candidat exact, les liens encore fonctionnels, absolus, sortant du privé,
avec parents liés ou d'autres formes restent NON VÉRIFIÉS.

Exemple : `benchab controle plan-reparation --prive /chemin/atelier`.
Un candidat d'identité vérifiable se déclare explicitement :
`benchab controle plan-reparation --prive /chemin/atelier --candidat-lien vues-deplacees/INDEX.md`.
Une seule vue canonique peut ensuite être appliquée explicitement depuis ce
plan :

```sh
benchab --prive /chemin/atelier controle plan-reparation > /tmp/plan-vues.json
benchab --prive /chemin/atelier controle appliquer-reparation-vue \
  --plan /tmp/plan-vues.json --cible vues/INDEX.md
```

La commande accepte uniquement `vues/INDEX.md` ou une fiche
`vues/etats/<24 caractères hexadécimaux>.md` explicitement présente dans le
plan. Elle relit le rendu dans une transaction SQLite `ro` et `query_only`,
vérifie l'identité de la base et les empreintes prévues, puis refuse toute
reprise ou changement de source, de cible ou de lien. Le plan ne permet pas de
réparer `README.md`, le manifeste, le changelog privé ni un fichier de projet.

Une vue divergente est copiée dans une sauvegarde privée neuve sous
`sauvegardes/vues/`, relue et vérifiée avant le remplacement atomique. Une vue
absente n'a pas de contenu antérieur à sauvegarder. La commande contrôle ensuite
le SHA de la vue réparée et ne modifie aucune table SQLite. Régénération et
réparation partagent le verrou `cache/vues.lock`. Les fichiers source, le
journal éditorial, les profils et les sorties machine ou réseau restent hors
de cette opération.

Le plan exige l’identité et le schéma BENCHAB, ainsi que le format de journal
SQLite ordinaire. Un journal WAL reste NON VÉRIFIÉ sans ouverture par SQLite.
Chaque action décrit aussi l’état et le SHA actuels de la cible, quand connus.

Pour une proposition de lien cassé, une étape distincte et explicite peut copier
la candidate sélectionnée vers la cible relative absente du lien :

```sh
benchab --prive /chemin/atelier controle appliquer-reparation-vue \
  --plan /tmp/plan-vues.json --cible vues/INDEX.md --candidat vues-deplacees/INDEX.md
```

Cette voie recalcule le plan et le rendu SQLite sous le verrou, relit le lien et
la candidate privée, puis conserve une preuve neuve relue sous
`sauvegardes/vues/` avant la copie. Seules les cibles feuille absentes dont les
parents existent sans lien, à l'intérieur de `vues/`, sont admissibles ; une vue
canonique ou le manifeste est refusé. La publication exclusive refuse toute
cible apparue entre-temps. Le lien et la candidate sont conservés, et le contrôle
final vérifie leurs empreintes. `PASS` signifie que les octets copiés égalent le
rendu actuel de SQLite ; l'origine historique et la pertinence de la candidate
restent **NON VÉRIFIÉES**. Un échec après la preuve garde celle-ci comme trace et
ne revendique pas une réparation.

## Préparer la reprise d’une table exportée

Une table JSONL absente peut être recalculée dans un **dossier neuf** distinct
d’`export/`. La commande ne publie pas cette copie à la place du fichier
manquant et ne reconstruit pas SQLite.

```sh
benchab --prive /chemin/atelier controle preparer-reprise-export \
  --table projets --sortie /chemin/atelier/rapports/reprise-projets
```

Le registre est ouvert en lecture seule, sans journal WAL ni fichier
auxiliaire présent, et le manifeste d’export doit rester valide et stable.
La table (64 Mio maximum) est sérialisée dans le même ordre et le même format
que lors de l’export normal. La publication exclusive conserve le JSONL
et `preuve.json` ensemble si le SHA-256 relu correspond au SHA déjà déclaré
dans le manifeste. Une cible existante, liée, une source modifiée ou un SHA
divergent donnent un refus sans dossier final. Après le renommage, les SHA du
JSONL et de `preuve.json` sont relus depuis le dossier publié épinglé ; tout
écart refuse le PASS et laisse le dossier publié intact pour diagnostic, même
s’il est altéré ou remplacé par un tiers. Aucun second renommage ni suppression
n’est tenté après publication. Sur disque, `preuve.json` garde le statut
`CANDIDAT`, les SHA attendus et la mention de revérification ; seul le retour
de commande devient `PASS` après relecture exacte. Les autres tables ne sont
pas évaluées par cette commande. Le statut PASS qualifie une **copie non
appliquée**, et ne dispense pas d’un contrôle de cohérence avant toute reprise.

### Appliquer une seule table absente

L’application est une commande distincte et demande le SHA-256 exact de
`preuve.json`, renvoyé par la préparation :

```sh
benchab --prive /chemin/atelier controle appliquer-reprise-export \
  --preuve /chemin/atelier/rapports/reprise-projets/preuve.json \
  --sha256-preuve SHA256_PREUVE_RENVOYE_PAR_LA_PREPARATION
```

La preuve doit rester au statut `CANDIDAT`, désigner la table et le JSONL
frère exacts, et déclarer le même SHA que le manifeste courant. Sous le verrou
`cache/export.lock`, la commande relit les deux membres bornés, ouvre SQLite en
lecture seule et en mode rollback, puis compare octet pour octet le JSONL à la
sérialisation indépendante du snapshot. Une seule table réellement absente peut
être publiée : un stage neuf dans `export/` est renommé vers son nom canonique
par une opération exclusive, sans alias et sans remplacement. Après publication,
la commande relit la preuve, le JSONL candidat, le manifeste et la base, puis
revérifie les chaînes de dossiers. Son dernier contrôle relit le membre canonique
depuis le descripteur du dossier d’export épinglé. Le candidat, les autres tables
et le manifeste ne sont pas modifiés. Si une vérification postérieure échoue,
la cible publiée reste en place pour diagnostic et aucun PASS n’est renvoyé. Un
refus avant publication peut laisser un fichier de stage caché `.reprise-*` dans `export/` ; il est
conservé pour diagnostic, sans suppression par chemin. La preuve sur disque garde
`CANDIDAT` ; seul le résultat de la commande peut indiquer `PASS`.
Un refus CLI renvoie le code 2. Cette opération ne remplace aucun export,
n’applique aucune autre table et ne crée pas de mesure de séance.

## Préparer une copie d’un original depuis une sauvegarde

Une seule source de projet peut être copiée depuis une sauvegarde tar BENCHAB
quand le membre et son SHA-256 sont connus. La commande ne restaure pas le projet
et ne modifie ni source existante, ni base, ni profil :

```sh
benchab --prive /chemin/atelier controle preparer-reprise-source \
  --archive /chemin/sauvegarde.tar --sha256-archive SHA256_TAR \
  --membre projets/SLUG/sources/FICHIER --sha256-source SHA256_SOURCE \
  --sortie /chemin/atelier/rapports/reprise-source
```

Le tar doit être un fichier ordinaire stable sans lien et son SHA porte sur tous
ses octets. La limite est de 65 Gio pour l’archive, 100 000 entrées, 4 Gio par
membre, 64 Gio pour les fichiers cumulés et 32 Mio pour le manifeste. Les noms,
types et tailles sont validés contre `BENCHAB-MANIFEST.json` ; le membre demandé
doit être un fichier régulier `projets/SLUG/sources/FICHIER` dont le SHA du
manifeste, le SHA déclaré et les octets lus concordent. Aucun arbre n’est extrait
et les liens ou membres TAR spéciaux sont refusés. Les extensions PAX et
GNU sont bornées en nombre, en profondeur et en taille (64 Mio cumulés,
64 Kio pour un corps PAX). Les membres creux sont refusés. Les deux blocs
de fin TAR et le remplissage nul du producteur sont exigés.

La sortie doit être un dossier neuf sous `prive/rapports/`. Il contient la copie
`source/FICHIER` et `preuve.json`. Le fichier de preuve conserve `CANDIDAT` ; le
retour `PASS` signifie seulement que la copie et la preuve publiées ont été
relues et correspondent aux empreintes prévues. Cela ne certifie ni l’origine
historique de l’archive ni la pertinence de l’original, et n’applique pas la
copie au projet. Une archive changée, un membre absent ou altéré, un SHA
différent, une sortie existante ou un chemin ambigu refusent la publication.
Si une incohérence est constatée après le renommage, la sortie est laissée
en place pour diagnostic et ne donne pas PASS (#119).

La copie préparée peut être relue à partir du SHA-256 de `preuve.json`
fourni par la préparation :

```sh
benchab --prive /chemin/atelier controle verifier-reprise-source \
  --preuve /chemin/atelier/rapports/reprise-source/preuve.json \
  --sha256-preuve SHA256_PREUVE
```

Le code 0 atteste seulement que la preuve et la copie concordent. L’état de
l’original est rapporté séparément : IDENTIQUE, MANQUANT, DIVERGENT ou NON
VÉRIFIÉ si son chemin ou sa lecture est hors contrat. Le code 2 refuse une
preuve ou une copie illisible, liée ou altérée. La commande ne rouvre pas
le TAR, ne crée ni original ni base, et ne donne pas d’accord de restauration
(#119).
