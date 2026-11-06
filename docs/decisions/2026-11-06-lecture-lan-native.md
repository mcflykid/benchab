# Autoriser la lecture du mode LAN natif Anycubic

Décision acceptée le 6 novembre 2026. Réalisation suivie dans #78.

BENCHAB pourra lire l’état de la Kobra S1 sous firmware constructeur en mode
LAN natif, sur demande explicite. C’est une alternative à la lecture Cloud,
sans identifiant de compte Anycubic ni bascule automatique vers le Cloud.
Les éventuels paramètres d’accès local restent dans l’atelier privé et ne
figurent pas dans les rapports publics.

Le client ne pourra émettre que les requêtes d’observation d’une liste
blanche fermée et testée. Toute autre requête sera refusée avant le transport.
L’âge des données, les délais et les états indisponibles seront visibles.
Aucune commande machine, activation de caméra, configuration du réseau,
opération d’appairage, mise à jour ou changement de mode ne fait partie de ce
périmètre. En particulier, déclencher une capture caméra compte comme une
écriture ; #44 et #28 partagent le format d’observation, indépendamment de la
manière dont l’image est fournie.

Le [guide de mise à jour Kobra S1 d’Anycubic](https://wiki.anycubic.com/en/fdm-3d-printer/kobra-s1-combo/firmware-update-guide)
indique que le firmware ne se met pas à jour en mode LAN : il faut quitter ce
mode avant de suivre le parcours constructeur. BENCHAB ne change pas ce mode.

Le [client LAN d’anycubic-cloud-api](https://github.com/Nino6689/anycubic-cloud-api)
est une source possible sous GPL-3.0-or-later. Une éventuelle reprise se limitera
aux lectures, après vérification de leur comportement, des fichiers et de la
licence ; créditer `WaresWichall` et `Nino6689` et inscrire la reprise dans
TIERS.md. Les certificats embarqués et le code de pilotage sont exclus. Les
faits techniques communautaires restent au statut « hypothèse » à leur entrée
dans le corpus, avec leur source et leur portée.

Cette décision ouvre un travail futur. Elle ne remet pas en service
[l’ancien transport](../explications/transports-historiques.md). L’exclusion
de Rinkhals et Moonraker ainsi que l’interdiction d’envoyer ou de piloter une
impression (#46) restent en vigueur.
