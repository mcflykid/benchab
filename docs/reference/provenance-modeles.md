# Provenance des modèles importés

Pour un STL, OBJ, GLB ou 3MF, garder l’original et le rapport dans `prive/`.
Le rapport d’audit contient le SHA-256 mesuré de l’original. Quand une fiche
est disponible, renseigner les cinq champs :

- `--provenance-source` : référence de l’entrée d’origine ;
- `--provenance-auteur` : attribution telle qu’elle est indiquée à la source ;
- `--provenance-licence` : nom ou référence de la licence déclarée ;
- `--provenance-droits` : conditions déclarées pour l’usage envisagé ;
- `--provenance-sha256` : empreinte de l’original auquel ces champs se rapportent.

Les options s’appliquent à `benchab imports importer -- audit` et
`benchab imports importer -- preparer`, en plus de `--unites` et `--rapport`.
`preparer` demande aussi `--sortie` pour la copie STL. Le 3MF est pris en
charge en audit avec `--unites mm` (conversion des unités déclarées dans
l’archive) ; les modificateurs exclus du calcul figurent dans le rapport et
font remonter WARN. Sa préparation reste réservée au parcours qui gère les rôles. La note libre
`--provenance` reste lisible et ne remplit pas la fiche à elle seule.

Le rapport donne la fiche dans `provenance_modele.fiche` et liste les champs
manquants. Pour reprendre une préparation, réutiliser les valeurs de cette
fiche avec le fichier original. Les réglages d’échelle ou de nettoyage
explicites n’affectent pas cette attribution ; le rapport distingue le
SHA-256 original du SHA-256 de la copie. Une autre empreinte interrompt la
préparation avant toute écriture.

Le statut documentaire RENSEIGNÉE signifie que les champs déclarés sont présents et
que l’empreinte correspond. Une fiche partielle est NON VÉRIFIÉE. Ces statuts
sont indépendants du contrôle géométrique et ne vérifient ni l’identité du
déclarant, ni la licence auprès de sa source. La frontière publique continue de
refuser les modèles tiers, quelle que soit leur fiche.
