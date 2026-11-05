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
