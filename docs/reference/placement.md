# Contrat de placement

La routine valide le placement avant tout contrôle métier et avant de créer ses
rapports. Le validateur commun est `benchab.controles.placement` ; il ne modifie
ni le document ni les réglages d’impression (#104).

## Structure reconnue

Le document est un objet JSON. `schema: 1` est la version reconnue. Les anciens
placements sans `schema` restent acceptés après les mêmes vérifications de
structure ; toute version explicite inconnue est refusée. Les clés JSON
dupliquées sont refusées.

- `plates` : liste non vide d’objets ; `name` est un texte non vide, unique dans
  la liste. Si présent, `index` correspond à la position, en base 1.
- `objects` : liste non vide d’objets ; `name` est non vide et unique sur sa
  plaque, `plate` désigne une plaque existante, en base 1.
- `xy` : deux coordonnées finies en mm, dans le repère XY du plateau déclaré.
- `size` : deux dimensions XY ou trois dimensions XYZ, finies, positives ou
  nulles, en mm. Les anciens placements d’identités peuvent garder des dimensions
  nulles ; elles ne prouvent aucune enveloppe géométrique.
- `reglages` : dictionnaire facultatif, interprété uniquement par le dictionnaire
  typé des réglages chez les consommateurs. Le validateur de placement ne
  normalise ni ne réindexe ses valeurs.
- `plates[].objects` : compte facultatif, nécessairement égal au nombre d’objets
  déclarés sur la plaque.
- `assembly_manifest_sha256` : empreinte SHA-256 facultative de 64 chiffres
  hexadécimaux. Son format est contrôlé ici ; la liaison au manifeste source
  nécessite sa preuve distincte. L’absence d’empreinte n’en fabrique pas une.

Les champs supplémentaires sont conservés. Les valeurs booléennes ne sont pas
des coordonnées ou des indices. Une erreur indique le champ, par exemple
`objects[0].plate`, et interrompt la routine avant son premier contrôle.

## Portée de la preuve

Le gabarit produit son placement depuis la déclaration d’assemblage et conserve
son empreinte. Une génération depuis un modèle importé relève du parcours #105.
Un placement d’identités ne constitue pas une déclaration de disposition réelle.
Les attentes de source, transformations et informations indéterminées doivent
rester distinctes des enveloppes mesurées ensuite dans le G-code.

La validation structurelle n’atteste ni placement physique, ni tenue, ni absence
de collision. Les contrôles de disposition et la revue gardent leurs propres
mesures et limites.

## Générer depuis un 3MF importé (#105)

`benchab imports placement SOURCE.3mf declaration.json --sortie prive/.../placement.json`
produit le même contrat depuis les géométries, identifiants et transformations
3MF. La déclaration doit être liée au SHA-256 exact de la source et nommer
explicitement le repère XY, les plaques, leur nom, puis chaque `build_item`,
son nom et sa plaque. Le SHA se calcule avec `shasum -a 256 SOURCE.3mf`.
La personne qui écrit la déclaration atteste que le repère plateau est déjà
applicable à la source, y compris lorsqu’elle déclare plusieurs plaques ;
BENCHAB ne déduit pas cette correspondance des seules coordonnées du 3MF.
Exemple minimal :

```json
{
  "schema": 1,
  "source_sha256": "SHA256_HEXADECIMAL_DE_L_ARCHIVE",
  "repere_xy": "plateau",
  "plates": [{"index": 1, "name": "Plaque explicitement déclarée"}],
  "objects": [{"build_item": 1, "plate": 1, "name": "Objet déclaré"}]
}
```

Le SHA de déclaration est conservé avec celui de la source ; le rapport conserve
les identifiants de modèle/objet, matrices brutes et transformations en mm. Sur
`PASS`, `--sortie` contient directement le contrat placement utilisable par
`controle routine`, et stdout contient le rapport complet. Sur `NON VÉRIFIÉ`,
aucun fichier de placement n’est publié. Les réglages d’impression ne sont pas
lus. La déclaration est lue dans une borne de 1 Mio. Un fichier sans liaison
explicite, un repère absent ou une association ambiguë produit `NON VÉRIFIÉ`
sans générer de coordonnées. Les sources multi-plaque
doivent fournir toutes les affectations explicitement : le format natif de
chaque trancheur n’est pas encore interprété automatiquement. Aucun G-code ni
réglage d’impression n’est lu ; la sortie ne constitue pas une disposition
tranchée ni une validation physique.

Le parcours avec identités exige aussi un StartPart OPC interne, unique et
résolu depuis `_rels/.rels`. Chaque `p:path` Production doit correspondre à une
relation OPC interne du fichier `.model` source vers le même modèle cible.
L’absence ou l’ambiguïté de ces relations produit `NON VÉRIFIÉ`. Le lecteur de
scène générique conserve son comportement historique et n’utilise pas ces
relations comme preuve d’identité.

Pour empêcher la séparation manuelle d’une liaison, la déclaration peut aussi
contenir `liens_fonctionnels`, une liste d’objets `{ "build_items": [1, 2],
"name": "Silhouette et languette" }`. Chaque lien est lié au SHA de la
déclaration et de la source. BENCHAB calcule l’intersection volumique des corps
fermés avec l’opération géométrique Manifold ; des bornes qui se touchent ou se
chevauchent ne constituent pas une preuve d’intersection positive. Le lien
déclaré interdit à lui seul de répartir les objets liés sur plusieurs plaques,
même si le volume mesuré vaut 0 mm³ ou reste NON VÉRIFIÉ. Sur une même plaque,
le volume mesuré, y compris 0 mm³, est consigné à titre d’observation ; un calcul
impossible reste NON VÉRIFIÉ. Le rapport distingue l’identité des objets
déclarés et ces liaisons fonctionnelles. Le volume calculé ne prouve pas la
tenue physique de la liaison.
Les liens fonctionnels ne sont pas devinés depuis les seules coordonnées ou
intersections du 3MF : une séparation non déclarée ne peut donc pas être
signalée par cette garde. Cette limite évite de confondre des coordonnées
locales qui se recouvrent sur des plaques indépendantes.
