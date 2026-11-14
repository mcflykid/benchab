# Retours d’expérience : conserver les défauts et leurs limites

Les observations viennent de l’auteur sur sa Kobra S1 Combo. Un résultat global
réussi peut comporter plusieurs défauts locaux. Les causes et les parades
restent des hypothèses tant qu’une comparaison ou un nouvel essai ne les valide
pas. Les fichiers, photos et noms des projets sont privés.

| Défaut constaté | Contexte et occurrences connues | Cause ou limite | Parade à examiner |
| --- | --- | --- | --- |
| Picots, gouttes et filament enroulé | Lithophane sphérique tiers en Glow, bobine sèche, 04/11/2026, un arrêt | Maillage et microsegments possiblement combinés au filament ; cause non prouvée | Continuité de paroi, examen du maillage et trajectoires entre Z 20 et 45 mm. |
| Couture très visible | Socle noir, 04/11/2026, une pièce | Couture alignée sur patte visible, cause probable | Choix d’une face cachée et vérification dans l’aperçu ; réglages d’essuyage candidats seulement. |
| Dessous des pattes imparfaits | Même type de socle fonctionnel, 04/11/2026 | Surplomb imprimé sans support ; dessus réussi | Orientation, géométrie ou support local à comparer. |
| Plafonds de poches affaissés | Pièce à poches sans supports, retour du 24/10/2026 | Ponts insuffisants ; cause thermique non isolée | Lire appuis, type de ligne et trajet, modifier seulement une candidate déclarée. |
| Décollement de petites pièces hautes | Mécanismes en eSUN, retours des 21–22/10 | Bordure absente dans les cas rapportés ; causalité non isolée | Bordure et appui réels, puis stabilité et espacement. |
| Fente de 0,8 mm refermée | Mécanisme en eSUN, octobre 2026 | Limite locale géométrie/tranchage | Lire le G-code de l’ouverture et revoir sa dimension utile. |
| Emboîtements trop serrés | eSUN, jeu 0,2–0,3 mm sur certaines pièces | Orientation et cumul de jeux non maîtrisés | Reprendre la mesure U32 dans son domaine, contrôler toute la course. |
| Rotor bloqué, entraînement absent | Mécanisme imprimé, retour du 21/10 | Contact trop radial ou entraînement non positif selon les variantes | Vérifier contacts dans les deux sens et ordre de montage. |
| Écailles au sommet arrondi | eSUN à 220 °C, plancher 2 mm/s et cible 15 s | Chaleur accumulée possible, non isolée | Comparer temps de couche, section et refroidissement ; ne pas copier le succès Glow à 190 °C. |
| Supports durs à retirer et défaut local | Figurine SUNLU à 220 °C, couche 0,08 mm, retour d’octobre | Le succès global ne prouve pas l’absence de marques | Examiner interfaces et retrait ; aucune validation universelle des supports. |
| Ajustement d’usage incompatible | Support noir bien imprimé, 02/11/2026 | Bracelet particulier ne passant pas ; dimensions d’usage distinctes de la surface | Vérifier l’objet réel et son insertion avant de valider la fonction. |

Lorsque le nombre exact d’impressions n’est pas établi, le registre indique
« inconnu » ; plusieurs journaux décrivant la même pièce ne sont pas plusieurs
occurrences. Les détails supplémentaires et défauts formulés de façon moins
précise restent consultables dans le corpus, sans inventer de diagnostic.

## Réussite, observation et preuve

Conserver séparément conformité numérique, qualité visuelle, montage et tenue
en service. Une pièce à douille bien ajustée ne valide pas sa tenue thermique.
Un G-code intact ne prouve pas que ses paramètres n’ont pas changé à l’écran.
Une bobine sèche déclarée écarte une supposition facile, sans prouver le mécanisme
du défaut. Une photo peut montrer une zone ; seule une observation confirmée
devient un fait dans la base.

La [relecture avant vote](../reference/vote.md) recherche les risques absents des
contrôles. En cas d’unanimité ou de contrôles tous PASS, préparer trois scénarios
d’échec avec signes précoces, zones/hauteurs et secours. Les points de surveillance
accompagnent le dossier d’envoi ; ils ne changent pas un NON VÉRIFIÉ en validation.

## Sources

Retours physiques historiques des 21–28/10, 02/11 et 04/11/2026, déclarations
de l’auteur et analyses numériques :
[connaissances avec conditions et statut](../../benchab/reference/connaissances.jsonl),
[empreintes des documents sources](../../benchab/reference/sources-savoir.jsonl).
Pour enregistrer un nouveau cas : [retour d’impression](../guides/retour-impression.md).
