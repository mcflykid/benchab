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
Les cibles README au-delà de 2 Mio ou sans UTF-8 valide restent à vérifier.

Les actions proposées contiennent la cible privée relative, le SHA-256 attendu,
l'empreinte du rendu source et les préconditions d'une future application :
sauvegarde vérifiée avant, source recalculée, cible revérifiée sans lien et
contrôle après. Elles ne sont jamais appliquées par cette commande. Un lien ou
une structure ambiguë reste à vérifier sans suivre sa cible. Une base absente,
illisible ou un contenu indécidable renvoie `NON VÉRIFIÉ` et aucune action
correspondante. Les fichiers sans SHA déclaré dans une source canonique ne sont
pas proposés à la réparation.

Exemple : `benchab controle plan-reparation --prive /chemin/atelier`.
Le résultat est un aperçu de la commande future ; sauvegarde, application et
vérification restent à concevoir dans une étape distincte de #119.

Le plan exige l’identité et le schéma BENCHAB, ainsi que le format de journal
SQLite ordinaire. Un journal WAL reste NON VÉRIFIÉ sans ouverture par SQLite.
Chaque action décrit aussi l’état et le SHA actuels de la cible, quand connus.
