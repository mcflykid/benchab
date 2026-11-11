# Interpréteur G-code commun

Cette décision conserve le périmètre du premier lot livré en 1.1.0. Le lot
1.2.0 ajoute les arcs du dialecte documenté et le repère spatial après G92 ;
voir [les fondations 1.2.0](2026-11-11-fondations-1-2.md) et
[la portée de la continuité](../reference/continuite-paroi.md).

Le lot #13 précède le contrat de débit #12. Les zones locales et plusieurs
contrôles utilisent déjà `Interpreter` ; la continuité possède encore une copie
de ses règles modales et les caches partagés ne sont pas protégés.

## Contrat retenu

- Conserver un seul suivi XYZ, extrusion, rétraction et changement d’outil.
  La continuité consomme les mouvements et événements de `Interpreter` et
  conserve seulement ses compteurs métier par couche.
- La mesure des trajets de remplissage réutilise le même lecteur pour les
  modes absolus/relatifs, la rétraction et le dépôt partiel ; sa couverture
  incomplète et ses diagnostics deviennent explicites.
- Découper `feed_line` et ses traitements en fonctions de complexité au plus 20.
  Les arcs G2/G3 sans modèle géométrique restent explicitement NON VÉRIFIÉS ;
  supprimer leur branche morte ne les qualifie pas.
- Accepter les alias linéaires G00/G01 et la syntaxe compacte déjà reconnue
  par le lecteur commun ; les paramètres invalides et commandes sans modèle
  invalident la couverture.
- Tn est une identité numérique de parsing. Son acceptation ne prouve aucune
  compatibilité matérielle ; les contrôles d’envoi conservent cette responsabilité.
- Conserver le refus métier d’une mesure de continuité dont le repère G92 XYZ
  n’est pas qualifié, même si le lecteur sait suivre l’état modal.
- Borner explicitement les diagnostics du lecteur de continuité, sans faire
  disparaître l’indication d’une couverture incomplète.
- Verrouiller les mutations et compteurs des caches, sans conserver le verrou
  pendant la lecture du fichier ou l’analyse. Deux insertions de la même clé
  ne doivent réserver qu’une fois sa taille.

## Vérifications

Écrire et observer les régressions avant correction ; tester modes absolus et
relatifs, G28, G92, outils, rétractions et commandes inconnues. Exercer les
lectures et analyses concurrentes avec un compte exact d’octets. Rejouer les
témoins S/M/L déjà tranchés et expliquer tout écart. Exécuter Ruff, le contrôle
de complexité et la suite complète avant la PR.

Aucune commande machine ni modification des profils installés. Les mesures
nominales ne simulent pas les accélérations et ne prouvent pas la tenue physique.
