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
