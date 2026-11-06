# Concentrer la lecture réseau sur le Cloud Anycubic

Décision finale du 6 novembre 2026. La recette de lecture est suivie dans #52.
L’option d’un client LAN natif, y compris en lecture seule, est écartée (#78).
BENCHAB concentre ses vérifications et sa maintenance sur le Cloud Anycubic.

La lecture Cloud reste explicite et limitée à sa liste blanche. Le rapport
distingue l’âge des données, les états indisponibles, le statut d’impression
et, si la route le fournit, le code de défaut. Aucun repli LAN, aucune
nouvelle permission et aucun identifiant dans les sorties publiques. Les
faits techniques communautaires entrent comme hypothèses avant qualification.

L’étude d’un client LAN dans
[anycubic-cloud-api](https://github.com/Nino6689/anycubic-cloud-api) n’entraîne
aucune reprise du code LAN ni de certificats. Le firmware constructeur reste
le périmètre ; les exclusions de Rinkhals et Moonraker ne changent pas.
L’interdiction d’envoi et de pilotage d’une impression (#46) reste en vigueur
quel que soit le transport.

#28 et #44 conçoivent ensemble le format d’observation par zone. Une image
importée ou déjà disponible peut renseigner ce format ; déclencher une
capture est une écriture et reste hors de la lecture Cloud. L’étude du
maillage du plateau reste conditionnelle à une source Cloud fiable en
lecture seule ; aucune réalisation n’est planifiée à défaut.
