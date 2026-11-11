# Intégrité des transformations 3MF

Voir [réglages et intégrité des préparations](reglages-et-integrite.md) pour le
contrat commun : plan exact avant écriture, relecture du fichier temporaire et
refus de toute différence non autorisée avant livraison.

## Producteur de fichiers neufs

Le gabarit construit directement un 3MF avec une provenance embarquée explicite :
nom du nouveau fichier, indices d'objet et de volume commençant à zéro, centre
du maillage local en mm. Cette provenance décrit la géométrie embarquée ; elle
ne reconstitue pas l'historique d'un maillage importé. Le volume unique hérite
de l'extrudeur déclaré sur son objet. Les objets multivolumes gardent leurs
affectations distinctes.

Les plaques nouvelles sont déverrouillées ; leur chemin de G-code désigne une
sortie prévue, encore absente avant le tranchage. La correspondance de plaque
du format Anycubic contient un `1` par couleur de filament déclarée. Ce champ
de format ne vaut pas routage physique dans un chargeur de filament.

Ces choix de construction sont fondés sur les sources du moteur :
[héritage de l'extrudeur et centrage](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Model.cpp#L2419),
[indices source et métadonnées de plaque](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp#L7645).
Ils sont vérifiés par réouverture géométrique et tranchage réel. Aucun code
tiers n'est repris pour les produire.

## Copie avec empreinte source

`benchab fabrication tracer-3mf SOURCE --sortie NOM.3mf` crée une copie neuve
sous l’atelier privé. Il ajoute seulement
`Metadata/benchab-provenance.json` avec le schéma 1, l’outil `BENCHAB` et le
SHA-256 des octets du 3MF source, ainsi que l’Override `application/json`
correspondant dans `[Content_Types].xml`. Aucun lien de paquet n’est ajouté.
L’archive source, ses profils et les autres membres restent inchangés ; une
sortie existante, une archive déjà marquée, signée ou structurellement invalide
est refusée. Le contrôle d’intégrité des réglages exige zéro différence.

Cette partie est facultative pour le format et ne vaut ni signature ni preuve
d’auteur. La sonde locale sur Anycubic Slicer Next 2.0.0.3 a montré que le
slicer accepte cette partie dans l’import, mais ne la conserve pas dans son
export. La variante `MustPreserve` a été refusée par le moteur. La provenance
doit donc être contrôlée après chaque réenregistrement ; l’outil ne garantit
pas sa persistance native.

Le profil de référence reste un choix explicite et empreinté. Un profil que
le moteur réindexe est refusé par le lanceur ; aucune conversion silencieuse
n'est faite pour rendre un ancien profil compatible. Les valeurs inconnues
restent intactes et apparaissent NON VÉRIFIÉES dans le rapport de tranchage.

### Matrices documentaires de provenance des volumes

La métadonnée `part/matrix` décrit la provenance utilisée pour recharger un
volume ; elle est distincte du `transform` des composants 3D, qui place la
géométrie. Ces deux champs ne sont jamais interchangeables. Les clés
`matrix`, `source_file`, `source_object_id`, `source_volume_id` et les décalages
`source_offset_*` ne sont connues que dans leur portée structurelle de partie.
Une clé projet homonyme reste inconnue ; un nom d’objet ne peut pas fabriquer
cette portée.

Sur le binaire macOS 2.0.0.3 identifié dans `config/kobra-s1.toml`, les sondes ont
observé la recomposition documentaire `V × M`, avec `V` le repère du volume et
`M` la matrice source. Le contrat actuel reconnaît uniquement les volumes
centrés issus du producteur, avec translations pures, provenance unique et
`M = V`. Il ne généralise pas ce constat aux rotations, autres versions ou
fichiers ambigus. [Le format du moteur stocke séparément la transformation de
source](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Format/bbs_3mf.cpp#L7530).

Le plan est construit **avant** le moteur : chemins XML, identités, empreinte
source et valeurs natives attendues. Toute différence supplémentaire, absente
ou différente fait échouer l’opération. Seules les matrices documentaires
prévues peuvent être remises à leur valeur source dans une nouvelle copie.
Le fichier natif reste conservé. Les membres G-code et 3D doivent garder leurs
SHA-256 octet par octet entre natif et copie restaurée ; le reste du XML doit
être identique, et le contrôle complet des réglages doit finir sans différence.
Les composants 3D et les réglages d’impression ne sont jamais corrigés après
le moteur. Le rapport conserve le plan et les états source/natif/restauré.

Les trois essais réels de diagnostic restent enregistrés en échec d’intégrité.
Leur entrée de diagnostic portait un rôle de volume invalide : le contrôle
renforcé refuse donc aussi ces archives en rejeu, sans quatrième lancement.
Le mécanisme documentaire est testé sur des fixtures synthétiques de rôle
correct ; ce n’est pas une qualification moteur supplémentaire ni une
validation physique. Une forme non qualifiée garde le contrôle strict
ordinaire ; aucune exception générale n’est ajoutée aux matrices.


Le rôle d’un volume fait partie du contrôle d’intégrité. `modifier_part` désigne
une zone de réglages ; `normal_part` désigne un corps imprimé. Un libellé
inconnu peut être interprété comme corps normal par le moteur : le producteur
le refuse explicitement, sans conversion silencieuse. Les liaisons partie /
composant et la géométrie locale doivent aussi rester identiques, même quand
le moteur renumérote ses ressources. [La conversion officielle des types est
explicite et possède un retour par défaut](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/Model.cpp#L2402).

#### Qualification native d’un modificateur (#48)

Une nouvelle sonde synthétique corrige le rôle de l’entrée historique et utilise
le plan d’origine avant le lancement. Sur ASN 2.0.0.3 et le
[binaire identifié](enumerations-asn.md#binaire-observé), deux copies d’une
boîte de 20 × 20 × 8 mm portent le même modificateur de 10 × 10 × 6 mm.
La seule différence autorisée est la densité clairsemée du modificateur :
20 % ou 60 %.

| Densité du modificateur | Valeur relue | Segments déposés du remplissage clairsemé |
| --- | --- | ---: |
| 20 % | 20 % | 7 263 |
| 60 % | 60 % | 13 783 |

Les deux tranchages natifs ont réussi. Les liaisons `normal_part` et
`modifier_part`, les empreintes géométriques et les transformations des
composants sont vérifiées par le plan. Après restauration des seules matrices
documentaires, l’intégrité source/sortie ne relève aucune différence de
réglages ; les deux variantes ne diffèrent entre elles que sur la densité
prévue. Les SHA du G-code natif et restauré coïncident pour chaque essai, et
l’interpréteur ne signale aucune erreur. Les entrées et les 646 profils
contrôlés sont intacts.

Les essais historiques restent des échecs conservés. Ce résultat qualifie
ce producteur, ce témoin et ce binaire. Il ne mesure ni une tenue physique,
ni l’effet de tous les modificateurs ou motifs verrouillés.

### Export de découpe depuis un 3MF

La découpe produit de nouveaux corps et de nouvelles plaques : elle ne transfère
pas implicitement les réglages par objet du modèle initial. Sans profil fourni
explicitement, son export reste géométrique et **NON PRÊT** pour l’impression.
Le rapport de chaque corps énumère les retraits, ajouts et changements exacts
de réglages aux niveaux projet, objet, partie et plaque. Cette liste est préparée
avant la sérialisation depuis la configuration prévue, puis comparée au fichier
réouvert avant sa livraison. Un changement supplémentaire refuse tout l’export.
Une clé inconnue retirée ou modifiée est également refusée, même déclarée.
Un profil explicite permet de créer les métadonnées canoniques des nouveaux
corps ; le tranchage et le contrôle d’envoi restent obligatoires.

### Profils de couches

`Metadata/layer_config_ranges.xml` entre dans l’instantané : bornes Z en mm,
identifiants d’objets et chaque option typée. L’outil de couches variables
déclare les seules plages prévues avant l’écriture. Il refuse de supprimer
une option inconnue présente dans les anciennes plages. Le membre opaque
`Metadata/layer_heights_profile.txt`, non interprété, est conservé par empreinte
et signalé NON VÉRIFIÉ ; une modification, même déclarée, est refusée.
Un PASS d’intégrité atteste l’égalité avec le plan déclaré ; les clés inconnues
conservées restent listées séparément comme NON VÉRIFIÉES.

### Affectation des instances et assemblage

Le différentiel compare la présence de chaque plaque, les instances dans leur
ordre (objet, indice d’instance et métadonnées) et les liaisons et matrices du
bloc `assemble` de `Metadata/model_settings.config`. Une renumérotation
technique préserve la liaison si l’identifiant résout le même objet nommé sans
ambiguïté. Un conteneur d’assemblage vide ne décrit aucun placement.

Le différentiel de réglages et d’affectation ne compare pas la géométrie de
`3D/*.model`. Les contrôles géométriques et de disposition ont leur propre preuve.

La normalisation prépare son ZIP dans un fichier temporaire unique sur le
même volume. La relecture et le différentiel précèdent le remplacement
atomique. Une erreur d’écriture conserve l’original et toute sortie précédente.
