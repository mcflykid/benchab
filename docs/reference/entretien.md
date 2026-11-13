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

Lorsque les données n’ont pas la même couverture pour tous les compteurs,
ajouter une liste facultative `couvertures` :

```json
[
  {
    "organe": "coupe-fil",
    "compteur": "bobines",
    "statut": "COMPLETE",
    "debut": "2026-10-01T08:00:00+02:00",
    "fin": "2026-10-31T18:00:00+01:00",
    "source": "Relevé fictif des bobines sur cette période"
  }
]
```

Une seule déclaration est admise par paire organe/compteur. Elle remplace la
couverture globale pour cette paire, même si elle est incomplète ou invalide.
Une période complète doit inclure le dernier entretien et tous les usages
retenus ensuite, bornes incluses. Le rapport conserve la période et sa source
et indique le motif si la couverture reste à établir. Les compteurs sans
déclaration locale conservent la couverture globale du fichier.

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

### Recommandations documentées pour la Kobra S1 Combo

| Élément | Indication du fabricant | Unité ou condition |
| --- | --- | --- |
| Coupe-fil | Inspecter après trois à cinq bobines | Bobines utilisées. La source ne donne pas de masse de référence par bobine. |
| Vis de l’axe Z | Entretenir environ tous les trois mois ou en cas de bruit anormal | Temps calendaire ou observation. |
| Tiges de l’axe Y | Inspecter chaque mois | Temps calendaire. |
| Compensation des vibrations | Vérifier après plus de 300 heures d’impression ou après déplacement de la machine | Heures d’impression ou événement. |

Sources et portée : [recommandations de maintenance Kobra S1](https://wiki.anycubic.com/en/fdm-3d-printer/kobra-s1-combo/maintenance-recommendations)
pour les trois organes, et [manuel Kobra S1 Combo v1.3](https://wiki.anycubic.com/kobra-s1/anycubic_kobra_s1_combo_user_manual-en-v1.3.pdf)
pour la compensation. Ces indications sont vérifiées dans la documentation
fabricant et restent des hypothèses de seuil pour BENCHAB. Leur portée par
version de firmware et la couverture de l’historique d’exploitation restent
à établir (#73). Aucun seuil n’est chargé par défaut. Le bilan utilise une
unité déclarée et ne convertit pas les mois, les grammes ou les estimations
du slicer en heures d’impression ou en bobines consommées.

Avec `--resume --sortie prive/rapports/entretien.json`, la commande enregistre
d’abord le bilan complet, puis affiche le statut et le nombre de compteurs
par verdict. Les événements, seuils, unités, identifiants et motifs détaillés
restent dans le JSON privé. Le résumé garde le code de sortie habituel ;
sans option, le bilan complet est affiché. La destination doit être neuve.
