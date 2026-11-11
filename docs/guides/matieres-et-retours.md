# Matières, jeux et retours physiques

Choisir le filament selon usage, température, rigidité, aspect, abrasion et
compatibilité buse, plutôt que d’inférer sa tenue de son nom commercial.
Les plafonds de débit viennent du profil déclaré ; aucune vitesse universelle.
Une bobine ajoutée n’est pas automatiquement calibrée.

```sh
uv run benchab connaissances bobine -- --help
uv run benchab connaissances memoire -- --help
uv run benchab fabrication retour -- --help
uv run benchab donnees impressions --resultat reussie
```

Relier chaque retour à sa préparation et à sa plaque : résultat global, défauts
par zone, fait déclaré, preuve disponible et hypothèses séparées. Une réussite
avec réserves conserve ses défauts ; une photo non fournie reste non consultée.
Les images et réglages personnels restent privés. Les jeux mesurés de l’utilisateur
sont prioritaires ; à défaut, utiliser le haut de la plage et écrire « non mesuré ».
Une référence de réglages peut partager machine et filament sans partager la
source du modèle : ne pas en déduire une équivalence géométrique.

Les filetages sur mesure et leurs contrôles sont accessibles par
`uv run benchab conception vissage -- --help`. Les éprouvettes de trous servent à
mesurer l’ajustement, pas à déclarer un jeu réussi avant impression.

Pour une bobine neuve, le [parcours des premiers essais](premiers-essais.md)
explique comment relier témoin, températures, jeux et retour.
