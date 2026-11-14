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
suggestion d’analyse d’image en observation confirmée. La saisie reste déclarative ; l’assistance à l’observation par zone est suivie dans #28.

### Suggestion photo locale

La commande `benchab visualisation retour-photo proposer PHOTO --destination DOSSIER`
copie l’image choisie dans un dossier privé neuf, crée un aperçu JPEG et écrit
`suggestion.json`. JPEG, PNG et HEIC sont acceptés ; HEIC est converti localement
avec `sips` quand Pillow ne sait pas le lire. L’original est conservé octet pour
octet et les deux fichiers reçoivent une empreinte SHA-256. La commande n’ouvre
pas SQLite et ne modifie aucun projet.

Le même contrat s’applique à une image déjà disponible dans l’atelier.
Déclarer son origine avec `--origine-image image_deja_disponible` ; pour un
nouveau fichier apporté au parcours, utiliser `--origine-image photo_importee`.
La valeur par défaut reste NON VÉRIFIÉ. Cette origine est une déclaration
utilisateur, pas une provenance automatiquement authentifiée. Les deux cas
copient les octets dans un dossier privé neuf.

Si la publication échoue avant le renommage, le dossier d’étape privé reste
conservé pour diagnostic ; BENCHAB ne le supprime ni ne le déplace.

Une liaison région/Z facultative est fournie par une déclaration JSON privée,
au schéma strict suivant :

```json
{
  "schema": 1,
  "source_sha256": "<SHA-256 du 3MF exact>",
  "plaque": 1,
  "region_id": "zone-choisie",
  "z_min_mm": 1.0,
  "z_max_mm": 4.0
}
```

La plaque, l’identifiant de région et la plage Z restent des déclarations
utilisateur. BENCHAB vérifie l’identité du 3MF, l’unicité du membre G-code de
plaque et le membre de réglages `Metadata/project_settings.config`; il enregistre
leurs SHA exacts quand les lectures sont valides. Une absence, une ambiguïté,
une différence d’empreinte ou une erreur d’intégrité donne NON VÉRIFIÉ. Un statut
PASS de liaison prouve seulement la correspondance des fichiers et déclarations,
jamais que la photo montre cette pièce ou cette zone.

La fiche reste `CANDIDAT` et marque l’interprétation photo NON VÉRIFIÉE. Pour
consigner séparément une observation après examen humain, calculer l’empreinte
de `suggestion.json` renvoyée par la première commande, puis lancer :

```text
benchab visualisation retour-photo confirmer DOSSIER/suggestion.json \
  --sha256 EMPREINTE --observation "Texte observé" --sortie confirmation.json
```

Cette confirmation neuve garde le SHA de la fiche et la déclaration écrite ; elle
ne modifie ni la fiche candidate, ni le projet, ni SQLite. Elle n’identifie pas
un défaut, le modèle ou l’impression et ne constitue pas une mesure physique.
Le JSON de confirmation inclut `observation_zone`, le contrat partagé schema 1 :
média source et aperçu par SHA, origine déclarée, zone exacte si liée, plage Z
en mm et observation déclarée. Sans liaison qualifiée, sa zone reste
NON VÉRIFIÉE. L’image n’est pas interprétée pour détecter une région ou un défaut.
L’observation confirmée reste distincte d’une hypothèse, d’une mesure
physique et d’un réglage manuel ; la commande n’effectue aucune de ces actions.

Un [exemple commun entièrement fictif](../examples/observation-zone-fictive.json)
illustre le format. Pour une image déjà disponible, seule la valeur
`media.origine_declaree` devient `image_deja_disponible` ; les autres champs
gardent le même schéma. Aucun exemple ne reprend de photo, projet ou
renseignement réel ; les SHA de l’exemple sont des valeurs factices, sans
archive ni image associée.
La commande affiche également `confirmation_sha256`, l’empreinte du fichier
de confirmation qu’elle a relu.

### Vérifier la portée d’une comparaison

Préparer une paire JSON privée au schéma 1. Sa liste `sources` contient deux
objets, chacun avec `suggestion` (chemin de `suggestion.json`),
`confirmation` (chemin de la déclaration humaine), `confirmation_sha256`
(empreinte affichée à la confirmation) et `archive` (chemin du 3MF tranché).
Les chemins dans cette paire sont relatifs à l’atelier privé.

```text
benchab visualisation retour-photo comparer prive/paire.json --sortie prive/comparaison.json
```

La commande revérifie les fichiers et leurs empreintes. Deux retours
déclarés sur la même archive, plaque, région et plage Z partagent le
même contexte déclaré. Avec des sources différentes, elle peut seulement
qualifier la correspondance des réglages si le modèle de machine et la
matière unique embarqués concordent. En dehors de ces cas, la portée reste
NON VÉRIFIÉE. Même source ne signifie pas même impression ; aucune
géométrie physique, cause de défaut ou identité de bobine n’est déduite.
Les images et les observations ne sont pas reproduites dans le rapport (#28).

Le rapport privé porte les empreintes du 3MF, des réglages, du G-code et de
l’identifiant de région, avec la plaque et la plage Z déclarées en mm. Le
libellé de région et le texte de l’observation ne sont pas recopiés.

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

Le [plan numérique de filament](../reference/plan-numerique-filament.md) peut
présenter l’estimation du G-code par plaque et index d’outil. Elle ne prouve ni
le fichier exécuté ni la bobine utilisée et ne modifie jamais le stock.

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

### Saisir une durée observée

Avec `benchab fabrication retour`, l’option `--duree-observee-s` ajoute au
résultat déclaré une durée positive en secondes. Indiquer dans
`--preuve-retour` comment elle a été relevée, par exemple au chronomètre.
Un retour déclaré comme interrompu garde ce statut. L’option laisse
l’estimation du slicer intacte et ne consulte aucune machine (#51).
