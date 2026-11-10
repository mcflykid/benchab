# Enregistrer un retour d’impression

Le retour décrit la pièce réellement imprimée, pas seulement la candidate.
Consulter `benchab donnees --help` et `benchab connaissances --help` ainsi que
[la référence des données](../reference/donnees.md). Conserver tous les fichiers,
photos et identifiants de projet dans `prive/`.

## Relier la pièce à la préparation

Noter date, préparation, plaque et empreinte du fichier envoyé si connue.
Séparer fichier préparé, fichier envoyé et fichier exécuté : une ressemblance
de nom ou une ouverture dans le slicer ne démontre pas leur identité. Relever
matériau, buse, température, ventilation, mode de vitesse et interventions à
l’écran. Marquer « inconnu » tout élément non confirmé.

## Décrire le résultat par zone

Noter résultat global et défauts locaux : face extérieure, dessous des pattes,
couture, filetage, montage ou tenue en service. Préciser hauteur ou couche de
début, évolution et arrêt éventuel. Une pièce fonctionnelle peut présenter une
couture inacceptable ; un dessus propre ne valide pas le dessous.

Conserver photos privées et commentaire de l’auteur. Ne pas transformer une
suggestion d’analyse d’image en observation confirmée. Le futur retour photo
assisté ne fait pas partie de la 1.0.

## Distinguer fait, hypothèse et validation

Écrire le défaut constaté d’abord. Pour chaque cause possible, indiquer les
indices, les contre-exemples et ce qui manque pour conclure. Une référence
réussie peut partager la machine et le filament sans partager le modèle :
la comparaison géométrique est alors limitée. Plusieurs paramètres changés
ensemble ne permettent pas d’attribuer la réussite à un seul.

Enregistrer portée, source, conditions (date, filament, réglages), nombre réel
d’occurrences et statut : mesuré, observé une fois, hypothèse ou validé dans
une portée précise. Ne pas compter plusieurs récits de la même pièce comme
plusieurs essais. La répétition d’un réglage de profil ne prouve pas sa validation.

## Capitaliser sans publier le projet

Conserver le lien exact dans la base privée. Publier le savoir utile après
reformulation : type de pièce plutôt que nom du projet, observations et
conditions sans photos, fichiers, stock ni identifiant personnel. Une correction
ultérieure doit préciser quelle hypothèse elle invalide sans effacer le fait.

Un retour sans préparation identifiée conserve une capture privée de la
déclaration dans `donnees/sources/retours/`, reliée par son empreinte SHA-256.
Le contrôle mémoire vérifie cette capture ; elle atteste le texte enregistré,
sans prouver le résultat physique ni identifier un fichier envoyé.

Choisir ensuite un changement explicite et comparer avec le fichier précédent.
Les [retours d’expérience](../explications/retours-d-experience.md) donnent des
exemples : surplomb imparfait, couture visible, picots dont la cause reste
inconnue. Aucune nouvelle impression n’est autorisée par l’enregistrement du retour.

Un retour portant sur l'ensemble, sans travail restant déclaré, fait passer le
projet à IMPRIME. Ce statut signifie qu'il a été imprimé ; le résultat physique
reste distinct et peut être « défaut ». Un retour partiel ne clôt pas le travail.
La simulation de conservation recense les fichiers retenus, les doublons et les
intermédiaires proposés à la relecture. Elle ne supprime rien, ne déduit aucune
régénérabilité d'une recette et ne compte aucun gain accessible sans accord.

## Sources

Contrat de données et faits physiques déclarés en octobre–novembre 2026 :
[données](../reference/donnees.md), [frontière publique/privée](../decisions/2026-11-04-frontiere-public-prive.md),
[connaissances sourcées](../../benchab/reference/connaissances.jsonl).
