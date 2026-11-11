# Bilan d'entretien conditionnel

`benchab controle entretien --historique prive/historique-entretien.json`
relit uniquement le fichier privé indiqué. `--sortie chemin/rapport.json` publie
en plus le rapport complet dans une destination privée neuve ; une destination
existante, un lien ou un chemin hors atelier sont refusés. La commande ne crée
pas d'atelier et n'ouvre ni SQLite ni les projets.

Le JSON d'entrée utilise `schema: 1`, une `couverture` égale à `COMPLETE` pour
une période attestée complète, `INCOMPLETE` ou `INCONNUE` autrement, une liste
`evenements` et une liste `seuils`. Chaque événement porte un identifiant
stable, une date ISO avec fuseau, un organe, un compteur, une unité, une source
et une preuve (`MESURE`, `DECLARATION_HUMAINE` ou `SOURCE_DOCUMENTEE`). Les
seuils doivent être positifs, sourcés et employer la même unité que les
événements. Les valeurs et seuils sont ceux du fichier fourni ; BENCHAB n'en
crée aucun et ne déduit pas des heures ou des grammes d'un temps estimé du
trancheur, d'un résultat d'impression ou d'un G-code.

Les lignes identiques partageant un identifiant sont comptées une fois. Un
même identifiant avec un contenu différent rend le bilan NON VÉRIFIÉ. La
couverture incomplète/inconnue, les seuils absents ou ambigus, les unités
incompatibles, les dates manquantes et une remise à zéro ambiguë restent NON
VÉRIFIÉS. `PASS` signifie uniquement que le cumul explicite est sous le seuil
fourni ; `WARN` signifie qu'il l'atteint ou le dépasse. Le seuil est comparé
aux quantités décimales sans erreur d’addition binaire. Cela ne décrit pas
l'usure ni l'état physique.

Si le cumul exact est trop grand pour être rendu dans le champ numérique du
rapport, le dépassement du seuil fini donne toujours `WARN`. La `valeur` est
`null` et le rapport indique cette limite de représentation.

Le rapport relie l'analyse au chemin relatif privé et au SHA-256 exact du JSON
lu. Les sources et preuves déclarées pour chaque événement et seuil restent
visibles. `validation_physique: NON FOURNIE` rappelle que ce bilan ne valide
pas une intervention ou l'état d'un organe. Les codes CLI suivent la table
commune : PASS 0, WARN 1, NON VÉRIFIÉ 2 et erreur technique 2.

L'issue #73 reste ouverte : aucun historique d'exploitation réel, aucun lien
de provenance spécifique aux compteurs et aucun intervalle fabricant par
modèle/firmware n'ont encore été qualifiés. Les recommandations de maintenance
ne sont donc pas intégrées comme seuils par défaut.
