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
température, accélération et matériel. `modification_manuelle` reste toujours
`NON VÉRIFIÉ` : la divergence prouve seulement que les valeurs intégrées au
projet diffèrent du profil comparé. Le rapport contient les SHA-256 du 3MF et
des octets de profils résolus avant et après le contrôle. Le SHA canonique en
mémoire du résolveur de presets est conservé à part du SHA du fichier ; toute
variation interrompt le rapport. La sortie est créée exclusivement sous l’atelier privé ou dans le
dossier temporaire système, sans remplacer une preuve existante.

`PASS` indique qu’au moins une comparaison typée a abouti sans écart ni
inconnu. `WARN` indique un ou plusieurs écarts connus. `NON VÉRIFIÉ` indique
qu’une partie du périmètre n’a pas pu être comparée. Aucun résultat n’établit
un changement manuel, une règle fabricant, une compatibilité physique ou une
validation d’impression.

Deux lectures complémentaires d’archives de l’atelier ont trouvé des profils
exacts par identité : 261 comparaisons procédé et 86 machine sur une archive
(7 écarts), puis 95 filament et 86 machine sur une autre (aucun écart).
Dans chaque cas, la famille sans identité exacte reste NON VÉRIFIÉE.
Les archives et les 646 profils contrôlés sont restés intacts.
