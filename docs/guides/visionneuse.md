# Examiner le modèle et le G-code

```sh
uv run benchab visualisation visionneuse prive/projets/ma-piece/sources/modele.stl --unites mm --sortie prive/projets/ma-piece/preuves/visualisation/modele.html
uv run benchab visualisation serveur
```

Le serveur écoute uniquement l’interface locale, sur le port du profil machine.
Il sert l’atelier privé ; ne pas le publier sur le réseau. La visionneuse commune
permet rotation, coupe, comparaison de candidates, inspection des contacts et
des anomalies. Une simplification d’affichage ne remplace pas le modèle de calcul.

La page d’accueil du serveur présente le catalogue. Les pages relient
projet, préparation, contrôles et preuves par empreintes. Les commentaires sont
stockés privés et associés au contenu observé ; ils ne valident pas une impression.
L’aperçu G-code permet les modes 2D/3D et la navigation par couche ; vérifier
parois, supports, coutures et zones visibles avant le vote. Voir
`uv run benchab visualisation apercu-gcode -- --help` pour ses entrées.
Le [parcours de découpe](decoupe.md) utilise cette même visionneuse.

## Rendu de photo sur le Mac M4

Blender 5.3.0 avec Cycles a produit un PNG de 96 × 96 pixels sur le seul
périphérique Metal Apple M4, avec quatre échantillons. Ce premier rendu
a pris environ quatre minutes, compilation comprise. Le parcours
`benchab visualisation photo` a également publié son PNG de 96 × 72 pixels
et son rapport pour un coupon synthétique après cette sonde. Le worker
peut se replier sur le CPU. Le rapport courant ne certifie pas le
périphérique utilisé par chaque rendu. La qualification de BENCHAB reste
sur macOS, et un portage de l’atelier demanderait de qualifier séparément
ses dépendances natives, les écritures exclusives et le slicer (#24).
