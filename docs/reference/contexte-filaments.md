# Contexte typé des filaments

`benchab.reglages.contexte_filaments(donnees, consommateur=None)` construit
un contexte indépendant sans modifier les réglages. Les identités proviennent
de `filament_settings_id`, dans leur ordre exact ; plusieurs slots peuvent
utiliser le même profil. Une identité absente, vide ou de type inconnu ne
qualifie aucune correspondance.

Chaque clé typée `liste_filament` reçoit une correspondance seulement lorsque
sa liste possède exactement la cardinalité des identités déclarées. Les listes
résiduelles, notamment les couleurs, restent intactes et NON VÉRIFIÉES. Aucun
repli sur la première valeur, aucune réindexation et aucune déduction à partir
de la seule taille d'une liste n'ont lieu. Une clé suffixée typée possède sa
correspondance propre et ne qualifie pas implicitement la clé sans suffixe.

Les diagnostics donnent la clé, les cardinalités observée et déclarée et le
statut PASS/NON VÉRIFIÉ du seul appariement. Ils ne certifient ni la matière,
ni une bobine, ni une compatibilité physique. Une clé inconnue reste sous le
contrat d'immutabilité du dictionnaire typé.

Le consommateur `cli` ou `preset_bundle` ne se déclare que lorsque le parcours
est identifié ; sinon les métadonnées discordantes restent ambiguës selon
`nombre_filaments_metadata`. Le gabarit BENCHAB déclare son parcours CLI à
partir des filaments explicitement choisis ; l'optimisation d'un fichier importé
construit les correspondances connues sans supposer le consommateur de sa palette.

La construction du contexte ne donne aucun droit de transformation d'un 3MF :
les différences permises et l'intégrité avant/après restent obligatoires.
