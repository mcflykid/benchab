# Lecture bornée des archives 3MF

Les contrôles de lecture utilisent `benchab.archives_3mf.Archive3MF` pour ouvrir
un ZIP une seule fois par contexte. L’index refuse les noms ambigus, absolus ou
traversants, les doublons, les liens symboliques, plus de 2 048 membres, un
membre supérieur à 512 Mio ou un total déclaré supérieur à 2 Gio. Une lecture
est limitée à la taille déclarée, à la limite par membre et au cumul de 2 Gio
de flux décompressés lus par contexte, y compris les relectures. Le XML et la
configuration JSON ont chacun un plafond de 32 Mio. La source est ouverte avec
refus de liens symboliques et ouverture non bloquante, puis vérifiée comme
fichier régulier par `fstat`; l’identité du descripteur est revérifiée à la
fermeture.

`ouvrir_flux(membre)` expose un flux séquentiel non seekable. Chaque appel à `read()`
rend au plus 64 Kio, même si l’appelant demande davantage. La taille déclarée est
réservée au budget cumulatif avant l’ouverture du membre : une lecture partielle ne
peut donc pas provoquer une seconde décompression gratuite. La taille réelle et le
CRC sont contrôlés par `zipfile` si le consommateur atteint EOF ; une sortie partielle
ferme le membre sans décompresser le reste. Les flux échappés sont fermés à la sortie
du contexte d’archive. `ouvrir()` garde son comportement historique de flux mémoire.

Les lecteurs G-code et le contrôle de continuité utilisent ce parcours pour les membres
`Metadata/plate_N.gcode`. Les caches SHA et analyse restent en place ; les membres
mis en cache sont rendus depuis le cache borné existant.

Le contrôle qualité lit la liste de plaques et les noms d’objets depuis le XML
borné de `model_settings.config`. Le niveau 3 lit résultats slicer, réglages
typés et G-code des plaques depuis le même contexte d’archive ; les index
numériques ambigus sont refusés et chaque G-code associé à une mesure doit être
présent et non vide.

Une même instance peut être rouverte après fermeture : chaque contexte repart
avec un index et un quota neufs. Une réentrée pendant un contexte actif échoue
sans fermer ni modifier celui-ci.

L’import de modèles applique le lecteur XML aux modèles, relations OPC et
métadonnées d’objets. Il conserve son plafond cumulé de 512 Mio pour ces
membres et sa limite de faces. La lecture autonome des réglages conserve son
plafond de 8 Mio.

Le contrôle d’intégrité avant/après lit les réglages du projet, les métadonnées
des objets et les plages de couches avec un plafond de 8 Mio par configuration.
Le profil de hauteurs de couches reste opaque : seul son SHA-256 est calculé par
blocs. Le même garde XML s’applique aux configurations en mémoire préparées par
les écrivains. Les lecteurs d’optimisation, de noms de plaques, de résultats du
slicer, de préparation et de planning utilisent aussi l’index commun. La
préparation contrôle le CRC de tous les membres dans le même contexte que les
empreintes G-code ; le planning lit ses métadonnées de plaque en XML sécurisé
avec un plafond explicite de 8 Mio.
Les doublons de chemin et les graphies qui donnent le même numéro de plaque
sont refusés. L’optimisation conserve également la vérification du CRC de tous
les membres.

L’aperçu des supports peut partager le contexte déjà ouvert par le catalogue :
l’index et les flux G-code d’une scène utilisent le même fichier. Le cache par
empreinte reste disponible et le lecteur refuse un contexte lié à un autre
fichier, même si le membre demandé est en cache. Le catalogue garde son plafond
de 256 Mio par archive et de 128 Mio par G-code de support.

L’inventaire des envois locaux lit masses et outils par plaque en flux, sans
charger tout le G-code. Une ligne de plus de 1 048 576 caractères est refusée.
Le CRC de chaque membre, y compris les entrées de répertoire, reste vérifié. Deux graphies
du même numéro de plaque sont refusées avant inventaire ou archivage ; une
copie neuve doit garder le SHA-256 de l’archive entièrement contrôlée.

Le XML se lit avec `xml()` : DTD et entités sont refusées en UTF-8, UTF-16 et
UTF-32 avant ElementTree. Les réglages se lisent avec `reglages()`, qui délègue
au dictionnaire typé existant sans ajouter de normalisation. Le contexte est
lecture seule ; les écritures ZIP explicites en modes `w` et `a` gardent leur
parcours d’intégrité propre.

Les budgets ont été comparés à 273 archives privées existantes : la plus grande
mesurée déclarait 9 580 886 octets au total et un membre maximal de 7 053 957
octets. Les archives témoins S/M/L restent donc sous les limites par plusieurs
ordres de grandeur.

Les contrôles déjà migrés sont U20 (plaques-interface), U22 (règles), U25
(débit G-code), capacités machine, envoi, supports requis, décollement,
orientations des supports, identifiants filaments, revue locale et vérification
des réglages. Les études de couches variables et de remplissage localisé lisent
les réglages via `reglages()` ; l’étude de remplissage associe les résultats aux
membres G-code par numéro de plaque non ambigu et conserve le lecteur en flux.
`finition_parois` conserve son parcours mémoire borné à 64 Mio et lit en flux
les plaques des grandes archives déclarées en mode vase. `paquet_3mf` valide
les octets fournis dans son propre parcours mémoire borné. Les écritures ZIP
en mode `w` ou `a` ne sont pas concernées.

La comparaison de reconstruction vérifie les CRC, y compris pour un membre
présent d’un seul côté ou deux archives binaires identiques. Les suffixes
`.3mf` et `.3MF` suivent le même parcours. Chaque archive est ouverte une fois
par comparaison ; les empreintes complètes avant publication protègent les
entrées. Un membre jusqu’à 64 Mio conserve son diagnostic détaillé. Au-delà,
ses octets sont comparés par empreinte en flux : un écart reste bloquant et
son détail est NON VÉRIFIÉ. Une vignette reste un élément documentaire.

La sélection des plaques mixtes (U36) lit les réglages avec le dictionnaire
typé et contrôle aussi le CRC des membres non utilisés. Elle continue de
filtrer les pièces protégées par nom, chemin et empreinte ; la lecture ne
modifie ni préparation, ni profil, ni réglage (suite de #122).

L’éprouvette de jeux vérifie la source de ses réglages typés avant de créer
une pièce ou de lancer le moteur. La préparation neuve et le lancement
continuent d’utiliser les contrôles propres au témoin.

L’éprouvette d’optimisation interne relie ses résultats aux noms exacts des
membres de plaque dans un seul contexte borné. Un indice unique comme
`plate_01.gcode` reste lisible ; deux graphies du même numéro sont refusées.
Une plaque aux clés `index`, `prediction` ou `weight` absentes, répétées ou
mal formées est refusée. Les métriques de dépôt et les opérations du lanceur
sont inchangées (#169).

À l’import d’une source de nouveau projet, la copie privée du 3MF passe les
mêmes budgets et contrôles CRC avant l’inscription. Ses octets restent ceux
de l’original : aucune normalisation ni extraction n’est effectuée (#122).

À la sortie d’un tranchage, les résultats, réglages attendus, membres de plaque
et CRC sont vérifiés depuis une même lecture du 3MF. Deux fichiers G-code qui
portent le même numéro sous des graphies différentes sont refusés (#171).

`Archive3MF.sha256()` calcule aussi l’empreinte du fichier depuis le descripteur
déjà ouvert et restaure le curseur ZIP. L’audit ACE lit ainsi les réglages, vérifie
les CRC de tous les autres membres et lie le SHA source au même contexte, sans
relecture indépendante du chemin (#122).
