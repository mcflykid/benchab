# Comparer un 3MF aux profils

`benchab controle ecarts-profils-3mf archive.3mf --profil profil.json --rapport prive/rapports/comparaison.json`
compare en lecture seule les réglages projet, objets, parties et plaques aux
profils fournis avec `--profil`. Les parents sont cherchés uniquement dans le
dossier du profil sélectionné et les dossiers explicitement donnés avec
`--dossier-presets`. Le résolveur de presets BENCHAB suit les héritages déclarés
et attribue chaque clé au fichier qui la définit.

Le dictionnaire typé détermine les familles et formes. Les clés connues sont
comparées exactement dans leurs unités et types natifs ; aucune normalisation
ou diffusion implicite n’est appliquée. Les clés, formes, héritages ou
correspondances inconnues restent `NON VÉRIFIÉ`. Les vecteurs de filaments ne
sont comparés que si `filament_settings_id` et les cardinalités sont cohérents,
et chaque profil fourni correspond exactement à l’identité du slot. Un slot sans
profil explicite reste `NON VÉRIFIÉ`. Les fichiers JSON de profil sont bornés à
8 Mio. Un nom d’héritage hors périmètre ou un parent ambigu dans les dossiers
explicitement fournis est refusé avant chargement.

Chaque égalité ou différence indique la portée, la valeur du 3MF, le profil et
son origine par clé, et les domaines sensibles reconnus parmi débit, vitesse,
température, accélération et matériel. `modification_manuelle` reste
`NON VÉRIFIÉ` sans déclaration explicite : la divergence avec un profil ne
prouve pas une intervention. Pour comparer un champ déclaré entre deux archives,
répéter `--champ-manuel CHEMIN` avec `--avant-intervention archive-avant.3mf`,
par exemple :

`benchab controle ecarts-profils-3mf apres.3mf --profil profil.json --avant-intervention avant.3mf --champ-manuel projet/outer_wall_speed --rapport prive/rapports/comparaison.json`

Le statut `DÉCLARÉE — ÉCART VÉRIFIÉ` exige que le chemin exact existe dans les
deux instantanés typés et que sa valeur scalaire connue ait changé. Les champs
absents, inconnus, les listes et les slots ambigus restent `NON VÉRIFIÉ` ; une
valeur inchangée ne produit pas de déclaration vérifiée. Ce statut vérifie
uniquement le delta demandé : il n’authentifie ni l’auteur ni le geste. Les
deux provenances donnent chemin et SHA-256 des archives. Les archives sont
lues sans modification et protégées contre une collision avec le rapport.
Sans ces options, la liste `interventions_manuelles` est vide. Le rapport
contient les SHA-256 du 3MF et
des octets de profils résolus avant et après le contrôle. Le SHA canonique en
mémoire du résolveur de presets est conservé à part du SHA du fichier ; toute
variation interrompt le rapport. La sortie est créée exclusivement sous l’atelier privé ou dans le
dossier temporaire système, sans remplacer une preuve existante.

`PASS` indique qu’au moins une comparaison typée a abouti sans écart ni
inconnu. `WARN` indique un ou plusieurs écarts connus. `NON VÉRIFIÉ` indique
qu’une partie du périmètre n’a pas pu être comparée. Même le statut déclaratif
n’établit ni identité de l’auteur, ni geste réellement accompli, ni règle
fabricant, compatibilité physique ou validation d’impression.

Deux lectures complémentaires d’archives de l’atelier ont trouvé des profils
exacts par identité : 261 comparaisons procédé et 86 machine sur une archive
(7 écarts), puis 95 filament et 86 machine sur une autre (aucun écart).
Dans chaque cas, la famille sans identité exacte reste NON VÉRIFIÉE.
Les archives et les 646 profils contrôlés sont restés intacts.

## Cohérence documentaire des réglages

Le champ `coherence` isole cinq rapprochements : modèle d’imprimante et
matière de buse avec le profil machine fourni ; ordre des parois et motif
clairsemé avec le profil de procédé ; matière avec le profil du filament
correspondant à chaque case exacte. Les clés et leurs familles viennent du
[dictionnaire typé](reglages-et-integrite.md). La référence de chaque règle
est la valeur du profil explicite ou de son parent résolu ; la ligne
contient l’empreinte de ce fichier et celle du 3MF.

Un écart établi donne WARN même si d’autres sources manquent ; celles-ci
restent NON VÉRIFIÉES dans le détail. Le statut général de comparaison est
indépendant et tient compte du reste du périmètre. Les signalements
[communautaires](retours-communautaires.md) ne fixent aucun seuil et aucun
écart ne confirme la matière ou la buse réellement montées.
