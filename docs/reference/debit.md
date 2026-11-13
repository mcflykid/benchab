# Contrat commun du débit U25

Le débit nominal commandé est calculé à partir du dépôt de filament en mm,
du diamètre déclaré en mm et de la durée nominale du trajet déposé en secondes.
L’interpréteur commun exclut le remboursement des rétractions du dépôt net.
Le calcul renvoie un volume en mm³ et un débit en mm³/s. Il ne simule pas
les accélérations, le firmware, le mode Sport ni le débit réellement atteint.

Les contrôles de débit G-code, d’envoi et de capacités machine utilisent le
même calcul et le même verdict : chaque dépassement établi d’un plafond
déclaré donne **FAIL**. Seul l’arrondi numérique est toléré :
`max(1e-9, plafond × 1e-10)` mm³/s. La marge historique de 5 % est supprimée.
Le maximum est contrôlé même lorsque le dépassement est trop court pour
apparaître dans le percentile 99 pondéré par longueur, qui reste informatif.

Le diamètre est lu avec le dictionnaire typé, à l’indice exact de l’outil.
Sans diamètre, plafond ou dépôt mesurable, le débit est **NON VÉRIFIÉ**.
Un dépassement établi reste FAIL si une autre mesure est incomplète.
Les contrôles gardent leur source de plafond explicite : réglages du 3MF,
déclaration d’envoi ou limites déclarées au contrôle de capacités. Des plafonds
différents peuvent donc produire des verdicts différents, avec leur provenance.

U21 n’impose aucun plafond universel : un réglage déclaré supérieur à
12 mm³/s n’est pas refusé pour cette seule raison. Les observations de réussite
restent liées à leurs conditions ; elles ne modifient pas automatiquement
les réglages ni les plafonds d’un autre projet.

`benchab controle debit-gcode FICHIER --sortie RAPPORT.json` conserve le
rapport complet dans une destination neuve de l’atelier privé ou dans un
temporaire système hors du dépôt. Une sortie existante, liée ou identique
à l’archive est refusée. Le rapport d’un verdict FAIL peut être sauvegardé
sans modifier le fichier 3MF.

Une plaque est mesurée depuis le membre G-code exact du 3MF : un zéro
initial dans un nom unique est accepté et deux noms pour le même numéro
sont refusés (#174).

Avec `--sortie RAPPORT.json`, `--resume` affiche le verdict, le chemin de
la preuve et jusqu’à trois plaques, en commençant par celles en écart. Pour
chaque plaque, le résumé donne jusqu’à trois erreurs et, s’il existe, le
premier dépassement établi avec le nom de l’objet, le filament, le maximum
et le plafond en mm³/s. `--detail` affiche le rapport complet. Dans les deux
cas le fichier JSON reste intégral et le code de retour est inchangé.
Sans ces options, l’affichage historique est conservé.

Le rapport U25 conserve le plafond embarqué et celui qui a servi au verdict.
Pour chaque objet et outil, il situe le pic par ligne G-code, couche, hauteur et
type de trajet, puis donne la longueur cumulée au-dessus du plafond embarqué
et la longueur totale déposée. Des coordonnées XY et une extrusion E arrondies
peuvent amplifier un pic sur un segment court. Les deux longueurs permettent de
distinguer ce cas d’un dépassement réparti sur une part étendue du parcours.
