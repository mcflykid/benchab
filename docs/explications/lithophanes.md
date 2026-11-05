# Lithophanes sphériques : réussite et défaut

Deux modèles différents ont été imprimés avec du PLA lumineux sur la Kobra S1
Combo de l’auteur. Le premier est une création personnelle ; le second vient
d’une collection tierce. Les projets, leurs identifiants, modèles et photos
restent privés. La comparaison des réglages est utile ; la comparaison des
géométries ne constitue pas une expérience contrôlée.

## Une réussite sur un modèle personnel

Une impression déclarée parfaite, rendu lumineux et montage de douille confirmés
en octobre 2026. Réglages relus : 210 °C première couche puis 190 °C, plateau
55 °C, flow 0,96, plafond 12 mm³/s, six parois, remplissage 100 % et couture
aléatoire. Aucun changement de température à l’écran signalé.

Des changements manuels ont eu lieu : mode Quiet temporaire puis Standard,
ventilation auxiliaire abaissée de 60 à 50 puis 30 %. La réussite porte sur ce
parcours complet, pas seulement sur les valeurs initiales. L’ancienne indication
230 °C a été contredite par la confirmation du 22/10/2026. La tenue thermique
à long terme n’a pas été mesurée.

## Un défaut sur un modèle tiers

Le 04/11/2026, arrêt au début de l’hémisphère sud : picots, gouttes et petits
vers de filament répartis sur la face extérieure ; anneau supérieur et filetage
propres. Bobine déclarée sèche. Résultat : défaut, une occurrence. Les photos
restent chez l’auteur ; aucune analyse visuelle des photos n’est revendiquée.

Lecture seule du G-code entre Z 20 et 45 mm : 25 à 80 morceaux de paroi
extérieure par couche contre 2 pour la référence personnelle ; environ 63 %
de segments inférieurs à 0,3 mm contre 10 à 20 %. Accélération de paroi 5000
contre 2000 mm/s², largeur minimale de cordon 75 % contre 85 %, vitesse de
remplissage des interstices 250 contre 60 mm/s. Ce sont des paramètres et
trajectoires relus, pas la preuve de la cause physique.

Les deux fichiers enregistrent une auxiliaire à 60 %. Les 30 % de la réussite
sont une intervention à l’écran, pas une différence de fichier. Le modèle tiers
peut produire davantage de microsegments par son propre maillage.

## Hypothèses révisées et contrôles

Un socle noir, imprimé avec accélération extérieure 5000 et largeur minimale
75 %, présente des parois sans picots. Ces deux réglages ne suffisent donc pas
à expliquer le défaut. Le maillage du modèle tiers, éventuellement combiné au
filament lumineux et aux démarrages/arrêts répétés, devient l’hypothèse principale,
sans preuve causale ni essai isolé.

`benchab controle continuite-paroi` mesure morceaux, microsegments et
accélération par couche ; il dispose de seuils absolus et indique la portée de
la référence : même modèle, ou seulement même machine et même matière.
Une alerte est un risque à examiner, pas un diagnostic automatique de picots.
Voir l’aide de la commande et [les contrôles](../reference/controles.md).

Avant une nouvelle préparation, vérifier maillage, trajectoires aux hauteurs
critiques, couture et refroidissement, puis comparer les changements explicitement.
La réussite du montage de douille ne valide pas l’aspect de la sphère.

## Sources

Retours de l’auteur et analyse en lecture seule déclarés le 04/11/2026 ; relevés
de profil et confirmation du 22/10. Conditions, hypothèses et provenance dans
[le corpus technique](../../benchab/reference/connaissances.jsonl) et
[les sources anonymisées](../../benchab/reference/sources-savoir.jsonl).
