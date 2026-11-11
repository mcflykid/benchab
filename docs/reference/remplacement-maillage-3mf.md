# Remplacement ciblé d'un maillage 3MF

`benchab fabrication remplacer-maillage SOURCE.3mf CANDIDAT.npz --sortie
prive/RESULTAT.3mf --objet-id ID --source-sha256 SHA256 --repere
local_objet_mm --decalage-mm X Y Z` produit une copie privée neuve. Le NPZ
doit être un maillage indexé BENCHAB en mm ; la cible est l'objet direct désigné
par son `objectid` dans le modèle principal de la source. Le SHA-256 attendu
relie le choix à une archive exacte. Le repère et le décalage XYZ en mm sont
obligatoires ; aucun recalage ni transformation n'est deviné.

Le sous-cas qualifié est volontairement restreint : StartPart OPC unique,
modèle unique, un maillage direct, une seule instance build et aucune référence
de composant. Les références `p:path`, les composants, relations OPC de modèle,
attributs de triangle (matériau/volume), métadonnées de mesh inconnues,
identifiants absents ou ambigus et G-code embarqué font refuser l'opération.
Les candidats doivent être des volumes fermés orientés vers l'extérieur. Les
transforms de l'unique instance restent inchangés ; le décalage s'applique dans
le repère local de l'objet avant ces transforms.

Le modèle doit utiliser le namespace Core par défaut et être encodé en UTF-8.
Les commentaires XML, instructions de traitement, namespaces préfixés ou
extensions inconnues sont refusés afin qu'une réécriture XML ne les supprime pas.
Le sous-cas n'accepte que les nœuds Core explicitement compris (`model`,
`resources`, `object`, `mesh`, `vertices`, `vertex`, `triangles`, `triangle`,
`build`, `item` et `metadata`) et leurs attributs géométriques/structurels connus.
`requiredextensions`, les propriétés de matériaux et tout autre nœud ou attribut
Core sont refusés, même si leur contenu pourrait être recopié sans changement.
`slice_info.config` entraîne également un refus, même si aucun G-code n'est
présent. La lecture est bornée à 64 Mio par membre XML et 128 Mio au total ;
chaque maillage est limité à 1 000 000 triangles. Ces plafonds bornent le coût
mémoire et de calcul sur une machine locale courante ; les archives au-delà ne
sont pas qualifiées par cette première étape.

Seule la géométrie de l'objet demandé peut différer dans le `.model` principal.
Les membres restants conservent leur ordre et leur SHA-256 ; le XML hors
maillage ciblé est comparé après relecture, les réglages sont contrôlés par
`integrite_3mf.verifier(..., permis=())`, puis toute la copie est relue avec
`Archive3MF` et ses géométries revalidées. La source et le candidat sont
empreintés avant et après l'opération. Une sortie existante ou liée est refusée.

`PASS` ne porte que sur ces preuves de copie et de géométrie. Le rapport garde
`validation_slicer` et `validation_physique` à `NON VÉRIFIÉ`. Aucun moteur de
tranchage, profil, projet ou commande machine n'est utilisé. Le G-code inclus
entraîne un refus afin de ne pas laisser un résultat embarqué périmé. Un
3MF absent de G-code n'est pas qualifié « non tranché » par cette opération.

Cette première étape ne couvre ni plusieurs modèles liés, ni les volumes
matérialisés via les conventions Anycubic de `model_settings.config`, ni les
matériaux/facettes, ni le recalage de l'espace candidat. Ces cas restent refusés
ou nécessitent une preuve spécifique avant extension.

Même une sortie inédite est refusée dans une préparation figée : préparer
une nouvelle version dans un dossier distinct.
