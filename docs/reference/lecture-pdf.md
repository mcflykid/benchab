# Lecture textuelle des PDF

La commande `benchab connaissances pdf FICHIER.pdf` extrait le texte embarqué,
page par page, puis imprime le rapport JSON sur stdout. Les numéros de pages
sont ceux du PDF, à partir de 1 ; `--page N` peut être répété pour choisir des
pages. Le rapport contient le chemin local, la taille, le SHA-256 et la version
du lecteur. Le texte privé apparaît donc dans stdout ; la commande ne crée pas
de fichier de sortie, ne publie pas de document et laisse la source intacte.

Cette fonction utilise l’extra optionnel `pypdf` :

```sh
uv sync --locked --extra pdf
uv run --locked --extra pdf benchab connaissances pdf prive/guides/guide.pdf --page 2 --page 3
```

Le paquet n’est pas nécessaire aux autres commandes BENCHAB. Si l’extra manque,
la commande explique comment l’installer. Il est épinglé à `pypdf 7.0.1`, sous
licence BSD-3-Clause ; voir le [dépôt et la licence du projet](https://github.com/py-pdf/pypdf)
et sa [documentation d’extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).

Les limites actuelles sont 16 Mio par fichier, 200 pages, 200 000 caractères
par page et 1 000 000 caractères au total. Un dépassement, un fichier non
ordinaire, un PDF chiffré, une page répétée ou un numéro de page invalide donne
NON VÉRIFIÉ avec un motif précis. La taille d’entrée et le texte restitué sont
bornés ; la bibliothèque peut toutefois développer des flux PDF pendant le
parsing. N’utiliser cette lecture que sur des guides locaux de confiance.

Aucun rendu de page n’est produit. Aucun OCR n’est exécuté. Si une page ne
contient pas de texte extractible, son état reste NON VÉRIFIÉ et le rapport
indique qu’une lecture OCR serait nécessaire ; le texte d’une couche OCR déjà
présente peut être extrait comme du texte embarqué. Les tableaux, colonnes,
formules et espacements peuvent ne pas ressortir dans leur ordre visuel : le PDF
ne fournit pas toujours une structure sémantique.
