# Contrôle numérique des plages Z

`benchab controle plages-z modele.3mf intention.json` compare les plages
`layer_height` déjà présentes dans un 3MF à une intention déclarée. Il ne modifie
ni l’archive ni les réglages, ne lance pas le slicer et ne valide pas une pièce
physiquement. Les unités du rapport sont les millimètres.

Le fichier JSON utilise le schéma 1 et un identifiant 3MF explicite :

```json
{"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}
```

Le champ `type` vaut `totale`, `partielle_volontaire` ou `zone_fine`. Pour les
deux dernières intentions, ajouter `min_z_mm` et `max_z_mm`. `zone_fine` exige
également `hauteur_max_mm`. Une seule intention est contrôlée par archive ;
relancer avec une déclaration différente pour une autre intention.

- `totale` exige des plages explicites continues entre le bas et le sommet du
  maillage.
- `partielle_volontaire` vérifie uniquement l’intervalle intérieur déclaré ;
  une plage partielle n’est pas un défaut lorsque cette intention est choisie.
- `zone_fine` exige la couverture complète de l’intervalle et que chaque plage
  traversant cette zone respecte `hauteur_max_mm`.

Le contrôle qualifie seulement les 3MF à un objet, un corps et une instance
imprimable, dont l’identifiant de plage correspond à l’objet géométrique.
L’instance peut être transformée en X/Y si les coefficients de sa coordonnée Z
sont exactement `0, 0, 1, 0` ; même une inclinaison minime est NON VÉRIFIÉE. Une
échelle, translation ou inclinaison en Z, plusieurs objets/instances, un
assemblage ou une liaison ambiguë donnent **NON VÉRIFIÉ**. Une intention absente,
un format de plage inconnu ou une géométrie illisible ne vaut pas PASS.

Les intervalles sont les plages variables explicitement stockées dans le 3MF.
Le contrôle ne reconstitue pas la hauteur de chaque couche après traitement par
le slicer, ni le résultat du G-code. La tolérance de comparaison vaut 0,001 mm
par défaut et peut être réglée jusqu’à 0,1 mm avec `--tolerance-mm`. Le code de
sortie est 0 pour PASS, 2 pour FAIL et NON VÉRIFIÉ. Voir
[l’issue #110](https://github.com/mcflykid/benchab/issues/110).

Quand `Metadata/project_settings.config` contient `layer_height`, le rapport
restitue cette hauteur de base issue des réglages projet via le dictionnaire
typé. Elle est informative et distincte des plages variables ; une valeur
manquante ou inconnue reste NON VÉRIFIÉE. Les métadonnées propres à un objet ne
remplacent pas ce réglage projet.
