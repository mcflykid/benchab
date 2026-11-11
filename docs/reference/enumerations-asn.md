# Énumérations d’Anycubic Slicer Next

`benchab controle enumerations-asn -- ARCHIVE.3mf` compare les valeurs
énumérées présentes aux observations connues pour le binaire configuré.
La commande relève sa version et son SHA-256 sans démarrer le moteur, lit le
3MF sans écrire, et n’attribue pas au moteur la production de cette archive.
Le dictionnaire typé conserve les valeurs exactes, casse et espaces compris.

- **PASS** : la valeur a été conservée dans le 3MF tranché et un effet a été
  mesuré dans le rôle G-code concerné, dans les conditions du témoin.
- **FAIL** : la valeur contredit le type ou un remplacement a été observé.
- **NON VÉRIFIÉ** : binaire, clé ou valeur sans observation suffisante ; l’absence
  du catalogue ne prouve pas qu’une valeur est refusée.

## Binaire observé

ASN 2.0.0.3, SHA-256
`618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d`.
Le témoin S a été copié avant chaque variante et tranché par le lanceur commun.
Les valeurs ont été relues dans le 3MF et les dépôts comparés par rôle. La source
et les empreintes de 646 profils installés ou utilisateur sont restées intactes.
Les rapports et fichiers de recette restent dans l’atelier privé.

| Réglage | Valeurs conservées | Mesure numérique |
| --- | --- | --- |
| `sparse_infill_pattern` | `gyroid`, `grid`, `rectilinear`, `zigzag`, `crosshatch` | Trajectoires distinctes ; 5 502, 468, 456, 456 et 1 602 segments déposés dans cet ordre. |
| `internal_solid_infill_pattern` | `rectilinear`, `monotonic` | Trajectoires de remplissage plein interne distinctes. |
| `top_surface_pattern` | `monotonicline`, `rectilinear` | Trajectoires de surface supérieure distinctes. |
| `bottom_surface_pattern` | `monotonic`, `rectilinear` | Trajectoires de surface inférieure distinctes. |
| `wall_sequence` | `inner wall/outer wall`, `outer wall/inner wall`, `inner-outer-inner wall` | Ordres de dépôt des parois distincts. |

Avec `zig-zag`, le moteur a sorti `zigzag` et les mêmes trajectoires qu’avec
`zigzag` explicite. Une valeur fictive a donné `crosshatch`. Dans les deux cas,
le moteur a terminé normalement et BENCHAB a refusé l’intégrité du résultat.
Une variante explicite `crosshatch` a conservé la valeur et produit les mêmes
trajectoires que le repli de la valeur fictive. Cette variante est aussi la
valeur de remplissage clairsemé dans les défauts exportés par ce binaire.

Lors des essais de peau et squelette verrouillés (motif clairsemé
`lockedzag`, valeur `rectilinear`), les champs
`locked_skin_infill_pattern` et `locked_skeleton_infill_pattern` étaient
absents des sorties et l’intégrité a refusé chacune. Ils sont également absents
des défauts exportés. Leur effet reste NON VÉRIFIÉ ; cela ne prouve pas une
absence dans toutes les configurations.

Le [code source publié d’Anycubic](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L2182)
fournit des noms candidats. Aucun lien n’est établi entre cette révision et
le binaire mesuré ; seuls le 3MF natif et le G-code tranché appuient les
observations du tableau. Rien ici ne valide une vitesse ou une tenue physique.

## Interface de support

Le même binaire ASN a tranché un coupon synthétique composé d’un pilier de
12 × 14 × 15 mm et d’une tablette de 28 × 14 × 3 mm, avec un recouvrement de
0,2 mm. Les supports étaient de type arbre automatique, style organique,
sur le plateau seulement, avec deux couches d’interface en haut et en bas.
Les empreintes des 646 profils relus et du coupon sont restées identiques.

Les profils système Kobra S1 pour buse de 0,4 mm déclarent `auto`. La valeur
candidate `concentric` figure dans le [code source publié d’Anycubic](https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/blob/6103ed8b511609658d00d0538cc7f0609cdb57da/src/libslic3r/PrintConfig.cpp#L4664-L4682).
Ce lien fournit un nom candidat ; il n’identifie pas la révision du binaire.

| Valeur demandée | Valeur embarquée | Segments d’interface | Longueur nominale | Temps et masse estimés du 3MF |
| --- | --- | --- | --- | --- |
| `auto` | `auto` | 100 | 525,374 mm | 989 s ; 3,28 g |
| `concentric` | `concentric` | 64 | 523,232 mm | 990 s ; 3,28 g |
| `concentric`, répétition | `concentric` | 64 | 523,232 mm | 990 s ; 3,28 g |

La relecture des paquets confirme que seule `support_interface_pattern`
diffère dans leurs réglages projet, et que les empreintes des trajectoires
du rôle `Support interface` diffèrent du témoin. Les deux essais
`concentric` donnent la même empreinte de trajectoires. Les autres valeurs,
styles, matériaux, la mesure d’écart Z et le retrait physique restent hors
de ce résultat (#80 et #89). Aucun réglage n’est adopté automatiquement.
