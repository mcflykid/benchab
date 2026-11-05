# Orca, Rinkhals et LAN : acquis conservés, usage abandonné

Les études de l’ancien atelier ont distingué configuration du slicer, transport
réseau et firmware. Rinkhals ajoute une surcouche, des services et une maintenance
propres. Une estimation de temps ou une réponse réseau ne prouve pas le résultat
d’impression. Le transport Moonraker étudié ne transmettait pas tous les choix
Anycubic attendus au lancement ; leur absence ne devait pas devenir une option
silencieusement supposée.

Après un essai Orca/Rinkhals/LAN, la décision utilisateur du 30/10/2026 a été le
retour à Anycubic Slicer Next et au Cloud constructeur. Le retrait de Rinkhals a
été annoncé par l’utilisateur ; il n’est pas revérifié par une connexion machine
dans ce chantier. Les réglages et relevés de cette période restent des historiques
privés, jamais des profils actifs implicites.

BENCHAB conserve les connaissances utiles : distinguer temps estimé et réel,
contrôler les options embarquées, redacter les identifiants et refuser les états
inconnus. Il ne réactive pas l’ancien transport. Les adaptateurs de lecture présents
sont testés sur simulations ; aucun transfert, lancement, mouvement ou réglage
machine n’a lieu pendant la validation de l’atelier.

## LAN : connaissances, aucun code de pilotage ou d’écoute livré

Décision de l’auteur : le paquet ne contient plus de client Rinkhals, OrcaSlicer
ni d’écoute MQTT LAN. Le port 18910 et les champs du protocole observé constituent
des connaissances historiques : une écoute ne prouvait ni le fichier exécuté
ni la fraîcheur de la bobine, et couleur/RFID ne déterminaient pas l’affectation.
Aucune connexion LAN n’est nécessaire au parcours courant ; la lecture Cloud
reste séparée, en lecture seule et sur demande explicite.
