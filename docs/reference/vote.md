# Vote du conseil

`benchab vote` lit les sept questions de `benchab/reference/organisation/conseil.json`.
A2, A3, A4 et A6 sont calculés depuis les rapports : PASS donne « pour », FAIL donne
« contre », WARN ou NON VÉRIFIÉ donne « abstention ». A4 rassemble les points non
vérifiés, les zones sans revue et les risques non traités. Un rôle sans contrôle
s'abstient. Les rapports doivent avoir une empreinte courante et identifier une
entrée de la préparation figée.

A1, A5 et A7 reçoivent des avis IA courts et motivés. Le programme ne contacte aucun
modèle et ne présume aucune indépendance entre ces avis. Un dossier neuf contient
trois abstentions à compléter.

Chaque vote de préparation exige une relecture critique externe d'un sous-agent de
classe `GROS`, avec historique désactivé. Le programme valide le fichier critique,
sa provenance documentaire et ses empreintes ; il ne vérifie pas l'identité réelle
de l'agent et ne lance aucun modèle. Le dossier vote doit référencer
`relecture_critique: {"fichier": "...", "sha256": "..."}`. Le fichier référencé
est un JSON schema 1 contenant `preparation_sha256`, `entrees` (mapping exact des
chemins privés relatifs vers les SHA courants, incluant fiche, maillages, rapports
de contrôle et preuves D2), et `provenance` avec `type_sous_agent`, `classe` égale
à `GROS`, `modele`, `taskid`, `mode` égal à `sans_historique`,
`sans_historique: true` et une `date` ISO avec fuseau. Le `type_sous_agent` réel
attendu est `sous-agent`. Les tests peuvent utiliser `simulation explicite` seulement
avec `scenario_test: true` ; cette provenance est conservée comme simulation.
Un modèle explicitement connu comme léger, tel que `gpt-6-luna`, ne satisfait pas
une déclaration de classe `GROS`. La provenance reste documentaire : l'orchestration
doit vérifier l'appel réel du sous-agent.

Sans critique valide, le calcul enregistre une demande en attente : les préavis sont
conservés séparément, les sept votes formels sont des abstentions et aucun quorum
n'est actif. Le rejeu de la préparation demande une nouvelle critique liée aux
empreintes courantes.

Le dixième homme est requis dans cette même critique si les sept préavis sont pour
ou si au moins un contrôle existe et que tous sont `PASS`. `dixieme_homme` doit alors
contenir le mode `premortem_echec_impression` et exactement trois scénarios. Chaque
scénario déclare un identifiant et une couverture : un contrôle existant doit être
`PASS`, ou un risque nouveau doit être lié à un objet `risques_nouveaux` avec id,
constat et source courante. Il déclare aussi une cause plausible, un signe précoce
avec hauteur/couche, zone et élément à surveiller, ainsi que les réglages de secours
et le plan de réimpression. Une couverture absente ou invalide maintient le vote en
attente.
Ces scénarios restent des hypothèses ; ils ne créent aucun échec ou vote contre.
Les points de surveillance sont enregistrés avec le vote et publiés dans un nouveau
Markdown privé sous `projets/<projet>/envois/`. Un nouveau risque constaté doit
décrire le constat et citer un fichier d'entrée courant par chemin et SHA ; il est
ajouté à A4 avec statut `NON VÉRIFIÉ`, séparément des scénarios hypothétiques.

```sh
benchab vote --preparer prive/projets/exemple/preparations/P01/PREPARATION.json --sortie prive/VOTE.json --objet "Évaluer la préparation"
benchab vote prive/VOTE.json
benchab vote --etat
```

Dans le JSON, les chemins sont relatifs à l'espace privé. `controles` contient des
objets `id`, `fichier`, `sha256`, `obligatoire` et, facultativement,
`administrateurs`. `avis_ia` accepte exclusivement A1, A5 et A7. `risques` et
`inconnues` sont des listes de descriptions. Les décisions significatives B/C
conservent le dossier de preuves qualifié : entrées, preuves, comparaisons,
objections et critères. Une preuve indispensable manquante ou en échec bloque
le dossier même avec sept voix favorables.

Il faut sept voix favorables sur sept. Une abstention ne compte jamais pour.
Le vote ne donne aucun droit d'exécution ; la portée des autorisations vient de
la demande de l'utilisateur. Le programme ne commande jamais l'imprimante.

Chaque calcul est ajouté à `votes` dans SQLite puis exporté en JSONL. Les anciens
avis restent intacts. Le rejeu produit les mêmes voix et la même `empreinte_vote` ;
son identifiant et sa date identifient une nouvelle consultation.

`benchab vote --comparer prive/QUESTION.json` compare au moins deux dossiers d'un
même projet. Le JSON contient `question` et `options`, un objet associant chaque
nom d'option à son dossier de vote, ainsi que le dossier de preuves requis.
Les pourcentages indiquent l'approbation de chaque option, sans scrutin exclusif.
Même une recommandation unanime reste un choix de l'utilisateur.

### Contributions contradictoires avant le dossier

`benchab vote --contribution FICHIER.json` importe une proposition privée en
mode OBSERVATION, sans produire de voix. `--mode-contribution` borne les
participants et révisions ; `--etat-contribution ACTIF` signifie seulement
« à soumettre au Conseil », sur choix explicite. Les versions sont conservées
dans SQLite et ne peuvent pas réécrire l'historique. Les déclarations
indépendantes restent déclaratives et les scénarios de test ne sont pas repris.

`benchab vote --anonymiser-contribution FICHIER.json` fournit une vue sans
identités déclarées. `benchab vote --derniere-contribution OBJET --id-contribution ID`
relit les empreintes. Pour joindre une contribution précise au dossier,
ajouter `--id-contribution ID` à `vote --preparer` avec son objet exact. Les
risques et preuves manquantes restent NON VÉRIFIÉS pour A4. Cette contribution
ne remplace jamais la relecture critique externe ni le dixième homme.

Pour conserver un choix effectivement exprimé, `benchab vote --arbitrer CHOIX
--objet OBJET` ajoute une décision déclarée au journal privé. Elle ne crée aucune
voix et ne dispense jamais des contrôles, d'une relecture ou d'une autorisation
humaine nécessaire pour agir.
