# Découper et assembler un modèle

La découpe sert à améliorer orientation, supports et assemblage. Elle ne prouve
ni la résistance physique ni le jeu réel. La source reste intacte et liée par SHA-256.

```sh
uv run benchab conception decoupe proposer prive/projets/ma-piece/sources/modele.stl --unites mm --pieces 2 --sortie prive/projets/ma-piece/candidats/plan.json
uv run benchab conception decoupe voir prive/projets/ma-piece/candidats/plan.json --sortie prive/projets/ma-piece/preuves/visualisation/decoupe.html
uv run benchab visualisation serveur
```

Ouvrir la page depuis le serveur local pour recalculer et exporter. Le fichier HTML
hors serveur montre la proposition mais ne peut pas appeler l’API locale.
Choisir un plan ou une courbe fermée ; les points de courbe se placent sur la source
et se déplacent dans la vue. Préciser le nombre de pièces, ajouter ou retirer une
coupe, puis recalculer. Une modification non recalculée interdit l’export.
Éclater et réassembler les pièces pour voir les joints et les accessoires.

Le catalogue (`uv run benchab conception decoupe catalogue`) contient les goupilles
coniques ronde, rectangulaire, D et queue d’aronde, rotule simple ou double,
douille avec goupille, canal de tige et absence de connecteur. `--mobile` et
`--renfort` expriment un besoin conservé lors des nouvelles propositions.
Le moteur explique son choix ; un connecteur automatique qui ne tient pas est
écarté avec sa raison. Un choix manuel impossible est refusé, jamais remplacé.

Pour séparer des volumes déjà présents : `--volumes-source`. Des volumes qui
se chevauchent exigent `--priorite` explicite, dans l’ordre des noms affichés.
Aucune répartition de matière commune n’est devinée.

Le calcul vérifie solides fermés, volumes, intersections, réassemblage et parois
au droit des connecteurs. La garde du canal suit toute la traversée de la tige.
Les jeux non mesurés utilisent le haut de la plage existante, marqué NON MESURÉ.
Sur une coupe courbe, certaines vérifications de paroi restent NON VÉRIFIÉES.
Les métriques de supports distinguent source brute, meilleure pose monobloc,
coupe brute et pièces finales avec connecteurs ; elles ne sont pas des durées mesurées.

Exporter en STL ou 3MF pour le parcours d’impression, ou en NPZ pour la relecture géométrique :

```sh
uv run benchab conception decoupe calculer prive/projets/ma-piece/candidats/plan.json --format 3mf --sortie prive/projets/ma-piece/candidats/decoupe-v01
```

La destination doit être neuve et privée (ou dans `/tmp`). Le moteur refuse liens,
écrasements et chemins de préparation figée Pnn. Les pièces sont posées à plat,
les transformations inverses et les SHA sont conservés dans le rapport. Le 3MF
est rouvert et vérifié ; il reste NON TRANCHÉ. Aucun profil d’impression n’est
transféré implicitement. Avec `--format npz`, les positions en mm
(`float64`) et les indices des triangles (`int64`) sont conservés sans fusion.
Le rapport compare avant export et après relecture brute : sommets, faces,
composantes, volume signé, orientation, fermeture et bornes. Il conserve les
transformations de pose et de réassemblage. Le NPZ n’est pas un fichier à
trancher et ne reçoit aucun profil. Le STL ne garantit pas l’indexation et sa
relecture peut fusionner des sommets ; choisir NPZ pour examiner les écarts
sur une découpe fine. La relecture Python
`benchab.imports3d.maillage_indexe.lire` refuse tableaux, indices ou tailles
invalides sans corriger la géométrie.

Pour les formats destinés au slicer, continuer par
[fabrication](../reference/fabrication.md), contrôle d’envoi, relecture et vote. Les accessoires ne gonflent pas le nombre de
pièces demandé. Les modèles sans géométrie dans leur 3MF ne sont pas reconstruits
depuis le G-code. Le catalogue de fixations reste suivi dans #27.

Les choix `clip-languette` et `clip-double` ajoutent, sur une coupe plane,
une languette à rampe ou deux languettes opposées et leurs logements.
Le plan doit choisir la forme explicitement. Le contrôle compare
l’encombrement fermé, le jeu latéral par face, la course nominale des
languettes et la matière autour de la gorge. Il ne valide ni la force
d’insertion, ni la retenue, ni la fatigue (#29).

Pour un jeu issu d’un témoin, déclarer dans `mesure_jeu` la convention
`lateral_par_face`, `valeur_mm`, `reference`, le même `connecteur` et
la même `bobine` que dans les options du connecteur. Sinon, le haut de la
plage de l’éprouvette ou de la plage explicitement sourcée reste NON MESURÉ.
Une mesure sur une cheville ronde ne qualifie pas un clip. Le placement
interactif des clips reste suivi dans #31.
