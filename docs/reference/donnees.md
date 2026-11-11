# Données de l'atelier

SQLite dans l'espace privé est la source des vues et de l'export JSONL. Chaque
transaction valide toujours les données, puis compare les treize exports à leur
sérialisation canonique. Si les octets et le SHA réels concordent, le JSONL garde
son inode ; un fichier divergent est reconstruit depuis SQLite. Le manifeste est
publié en dernier et n'est jamais utilisé seul pour déclarer les JSONL valides
(première étape #19). La validation et la comparaison parcourent encore la base
entière : leur coût reste proportionnel à sa taille.
Les projets, leurs fichiers, les observations et les préférences restent privés.
Le savoir général publiable est sélectionné séparément dans `benchab/reference/`.

```sh
benchab donnees impressions --resultat reussie --matiere PLA+ --ponts
benchab donnees vues
benchab donnees vues --racine-publique .
benchab donnees reconstruire prive/export --destination prive/reconstruit
```

La recherche utilise des champs structurés de l'impression observée. Le filtre
matière exige `filaments: [{"matiere": "PLA+"}]`. Le filtre ponts exige
`ponts: {"present": true, "source": "preuve"}`. Un mot dans une note, un réglage
de préparation ou un fichier envoyé ne prouve aucune impression réussie. Si une
matière ou la présence de ponts n'est pas documentée, la ligne ne satisfait pas
ces filtres. La portée, les pièces et les limites restent dans les données du
résultat ; une réussite partielle ne devient pas une réussite de l'ensemble.
Une entrée explicitement qualifiée de profil ou portant
`confirmation_bobine_physique: false` est exclue du filtre matière physique.
Les observations physiques structurées d'un projet sans préparation identifiable
restent recherchables avec `preparation_id: null`, sans créer de Pnn fictif.

Les vues privées sont `vues/INDEX.md`, `vues/etats/`, `README.md` et `CHANGELOG.md`
sous l'espace privé. Elles sont régénérées à la fin de séance ou explicitement.
Les états et l'index viennent des projets ; les changements viennent de `versions`.
L'option `--racine-publique` met à jour uniquement la section `BENCHAB:BASE` du
README avec la version du paquet. Les numéros enregistrés dans SQLite ne sont pas
un registre de versions publiques. Le `CHANGELOG.md` public est éditorial et reste
intact ; seul le CHANGELOG dans l'espace privé est régénéré depuis la base. Aucune
donnée projet, changement privé, source ou date privée n'est transféré. Les vues
privées sont remplacées fichier par fichier ; une interruption demande une nouvelle
génération.

Le manifeste privé des vues permet au contrôle de cohérence de repérer une vue
absente, périmée ou altérée. Le contrôle ne régénère rien : la génération est
une opération explicite, distincte de la vérification.
La commande `benchab controle plan-reparation` compare les rendus SQLite aux
vues privées en lecture seule et produit des cibles/SHA avec préconditions de
sauvegarde et de contrôle futur. Elle ne répare rien ; voir
[le plan de régénération](plan-reparation-vues.md). Une divergence n'est pas
une preuve de falsification.

Une reprise historique peut conserver une préparation sous forme de résumé.
Son JSON figé garde son empreinte originale ; son état reste « historique résumé ;
réimpression non établie ». Le contrat privé de reprise garde une décision pour
chaque référence Source, les raisons des exclusions et les fichiers encore actifs.
Les références physiques et les copies protégées restent obligatoires, même
historiques. Un fichier actif absent ou altéré donne FAIL. Une cohérence active
réussie ne prouve ni une reprise exhaustive ni la possibilité de réimprimer une
préparation résumée.

La reconstruction refuse une base contenant déjà des lignes. Elle valide le
schéma, les colonnes, toutes les empreintes, les relations et la représentation
canonique avant de conserver la transaction. Les tables et les exports reconstruits
sont identiques à l'export fourni. La mesure de la commande est enregistrée dans
l'atelier d'origine, afin de préserver cette identité dans la destination.
Les fichiers 3D et les documents des projets ne sont pas dans l'export relationnel :
ils doivent être conservés avec le reste de l'espace privé. Les limites de lecture
sont 64 Mio par table et 256 Mio pour l'export complet.

`benchab init` importe le savoir général livré sans remplacer un enregistrement
local différent. La mémoire et les nouvelles bobines utilisent ensuite SQLite.
Un ancien registre privé, présent avant le premier appel mémoire, est importé
une fois en conservant ses entrées brutes et son index. Ce JSON reste une archive :
le modifier ensuite ne change pas les connaissances canoniques. Les conditions,
exclusions et niveaux de preuve historiques restent accessibles ; une provenance
ou une applicabilité inconnue ne devient pas une validation physique.

## Sauvegarde des fichiers et de la base

L’export JSONL ne remplace pas une sauvegarde des modèles et documents. Le module
de sauvegarde reçoit une source explicitement située dans l’atelier privé et une
archive neuve dans l’atelier. Une sauvegarde de l’atelier entier peut être
écrite directement à sa racine. Pour un export d’essai destiné à être jeté,
utiliser explicitement `--jetable` dans le temporaire système.

```sh
uv run benchab donnees sauvegarder --source . --sortie benchab-atelier.tar
uv run benchab donnees sauvegarder --source . --sortie /tmp/benchab-essai.tar --jetable
uv run benchab donnees restaurer /tmp/benchab-essai.tar --destination restauree-v01
```

Le parent de sortie doit exister, sans lien symbolique. La restauration reste dans
l’atelier privé, dans un dossier neuf ; elle ne remplace jamais l’atelier courant.
Les fichiers et SHA sont lus en flux de 1 Mio ; les budgets actuels sont 4 Gio par
fichier et 64 Gio par archive. Une limite atteinte est un refus, pas une sauvegarde
partielle annoncée réussie. Aucune sauvegarde complète des données personnelles
n’a été lancée pour les tests : les essais utilisent des fixtures.

La base canonique est copiée par l’API SQLite backup, avec intégrité vérifiée ;
ses sidecars WAL/SHM ne sont pas archivés séparément. Un changement de l’inventaire
ou des fichiers pendant la copie empêche la publication. Les sources contenant
des liens symboliques sont refusées, sans collecte de leur cible. Aucun dossier
HOME ni profil installé n’est collecté implicitement.

La restauration valide manifeste, tailles, SHA et graphe des liens avant de
publier un dossier préparé à part. Elle accepte les liens internes vers des
fichiers réguliers, refuse sorties de périmètre, cycles, doublons, conflits de
parents et écrasements. Elle ne lance aucune impression et ne déclare aucune
validation physique. Après restauration, contrôler la base et ses références ;
le dossier temporaire de l’exemple n’est pas une sauvegarde durable.

### Sources historiques converties en faits

Une synthèse conservée peut remplacer la livraison d’un journal brut uniquement
avec un contrat privé explicite : source, révision et empreinte historiques,
liste exhaustive des faits et empreinte de leur représentation (texte, preuve,
conditions et provenance), reçu identifié par SHA-256. Un fait manquant, modifié
ou rattaché à une autre source fait échouer ce contrôle. Les index historiques
restent intacts ; un index sans fait nécessite sa propre décision d’écartement.

Pour une archive tierce non distribuée, une citation peut identifier un membre
par son chemin et son SHA-256 historique. Cette identité reste distincte du SHA
de l’archive entière. Le contrat ne distribue aucun code tiers et ne transforme
pas une lecture documentaire en validation physique. Une empreinte relevée
aujourd’hui n’est jamais attribuée rétroactivement à une source historique.

Le contrôle `connaissances memoire verifier` sépare l’intégrité des extraits de
leur fidélité historique. Cette dernière reste **NON VÉRIFIÉE** quand les
originaux ne sont pas livrés. Il ne réimporte pas automatiquement les données et
ne réclame pas de journal brut pour une conversion explicitement validée.

### Limites de sauvegarde et comparaison

La création d'une sauvegarde refuse tous les liens source, y compris internes :
il faut sélectionner un arbre privé de fichiers et dossiers réguliers. La
restauration accepte les liens internes vers des fichiers seulement après validation
du graphe et des empreintes ; ces deux contrats sont distincts. Aucun dossier de
profils installé n'est collecté implicitement.

Au départ, le seuil d'espace libre vaut la taille inventoriée multipliée par 1,25,
plus un plancher de 10 Gio. Le calcul compte le TAR et la copie de travail
ensemble si les deux occupent le même volume. La restauration utilise la taille
déclarée et vérifiée dans l'archive. Le plancher est surveillé pendant la copie
et l'écriture du TAR. Ces deux valeurs se règlent dans `prive/sauvegarde.toml` :

```toml
facteur_espace = 1.25
plancher_libre_octets = 10737418240
```
Un manque d'espace donne FAIL et aucune archive n'est publiée. Les temporaires
créés par l'opération sont nettoyés ; une ancienne archive existante reste intacte.

`benchab donnees comparer-arbres ATTENDU OBTENU` compare deux dossiers privés
(chemins, types, droits et SHA-256) sans les modifier et signale chaque écart.
Cette comparaison d'octets ne compare pas sémantiquement une base SQLite active
à son instantané : utiliser les contrôles de restauration pour ce dernier cas.

### Reprise d'une publication privée

Une fiche de bobine ou de vote est publiée après le commit SQLite et la mise à
jour de l'export. Un reçu privé identifie l'opération inachevée. La même entrée
reprend le même enregistrement et termine l'export et la fiche. Le reçu est
retiré quand la publication est complète. Un nouveau vote après succès garde
son propre enregistrement. Le registre historique des bobines est sérialisé
par un verrou et remplacé atomiquement.

Une mesure reprise conserve le même identifiant et les durées déjà inscrites.
Son code et l'export sont mis à jour ensemble par le chemin commun d'écriture.

Les sorties jetables sont permises dans le temporaire système hors partie
publique du checkout. Une preuve durable ou une référence enregistrée ne
sort pas de la racine privée de son atelier. Voir
[la décision sur la portée](../decisions/2026-11-06-portee-controles-et-ecritures.md).

Le contrôle de cohérence compare chaque export au registre en lecture seule.
Un manifeste absent ou mal formé ne masque pas une table absente, liée,
illisible ou différente. Même sans manifeste utilisable, les tables lisibles
restent comparées au contenu SQLite attendu et les vues sont contrôlées.
Un écart décrit l’état des octets ; il ne détermine pas sa cause et ne lance
aucune régénération ni restauration.
