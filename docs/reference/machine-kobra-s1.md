# Kobra S1 Combo et ACE 2 Pro

Cette page décrit la configuration de l’auteur relevée en octobre–novembre 2026,
pas toutes les machines de cette gamme. Les profils sauvegardés constituent la
référence de la préparation ; une fiche commerciale ne prouve pas la machine
réellement chargée. Qualification logicielle BENCHAB : macOS et Kobra S1.

## Configuration et vérifications

| Élément | Configuration observée | Limite de la preuve |
| --- | --- | --- |
| Machine | Kobra S1 Combo avec ACE 2 Pro | Déclaration et fichiers locaux, pas lecture physique permanente. |
| Buse | Laiton, 0,4 mm, déclarée le 26/10/2026 | Vérifier la buse montée, surtout après changement. |
| Plateau | PEI texturé ; volume nominal du profil 250 × 250 × 250 mm | La surface utile dépend des bordures, purges et zones interdites. |
| Logiciel | Anycubic Slicer Next 2.0.0.3 dans les références historiques | Ne pas extrapoler les incidents aux autres versions. |
| Alimentation | Filaments affectés par l’ACE | Profil, fichier et emplacement déclaré ne prouvent pas la bobine physique. |

Avant préparation, relever matériau réel, diamètre et type de buse, plaque,
état de surface, ventilation et géométrie utile. Les filaments chargés peuvent
user une buse laiton : la présence de marbre ou de pigments ne chiffre pas cette
usure. Aucun remplacement automatique n’est déduit de cette remarque.

## Débit et vitesse

Les plafonds de profils système relus dans Slicer Next 2.0.0.3 sont des paramètres,
pas des capacités mesurées : PLA High Speed 18, PLA 16, PLA+ 15, Marble/Glow/Matte/
Silk 12 et PLA générique 8 mm³/s. Un profil utilisateur peut différer.
La vitesse de déplacement à vide, la vitesse de paroi et le débit d’extrusion
sont trois choses distinctes. Une vitesse affichée ne démontre pas une vitesse
constante atteinte sur une petite pièce.

Le débit dépend de la section réelle de ligne et de la vitesse. Examiner le
G-code avec `benchab controle debit-gcode`, puis les ralentissements et les
accélérations. Une réussite SUNLU à plafond 32 mm³/s ne mesure pas le maximum
thermique de la tête : voir [profils et procédés](profils-et-procedes.md).

## ACE et conditions réelles

Ne jamais déduire l’affectation physique des identifiants du profil. Au contrôle
d’envoi, rapprocher filament déclaré, couleur, emplacement réel et purge.
Un relevé réseau peut être ancien ; une session ou une caméra ne remplace pas
la confirmation du fichier et de la bobine. Les stocks, comptes et identifiants
restent privés. Toute commande physique exige une demande explicite.

Le parcours de référence utilise le constructeur et son G-code de démarrage.
Les essais Rinkhals/Orca/LAN sont décrits dans
[l’historique des choix](../explications/transports-historiques.md), pas prescrits pour
une préparation actuelle. Contrôler première couche, propreté et fonctionnement
des ventilateurs sans lancer de calibration pendant une impression.

## Sources

Fiche machine historique relue le 05/11/2026 ; observations de l’auteur sur sa
Kobra S1 Combo (buse déclarée le 26/10), profils système Slicer Next 2.0.0.3 et
fiches d’expérience. Provenance par empreinte et portée dans
[le corpus technique](../../benchab/reference/connaissances.jsonl) et
[son catalogue de sources](../../benchab/reference/sources-savoir.jsonl).
