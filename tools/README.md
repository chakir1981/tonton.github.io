# Outils de maintenance

Scripts facilitant la mise à jour et la publication des add-ons de ce dépôt.
Tous les scripts utilisent uniquement la bibliothèque standard Python 3 (aucun `pip install` nécessaire).

## release.py

Incrémente la version, construit le zip, met à jour le fichier `addons.xml` à la racine,
régénère `addons.xml.md5`, copie les ressources dans `_zips/<id-de-l-addon>`. Fonctionne pour
n'importe quel add-on du dépôt.

```bash
# Simulation d'abord (recommandé)
python3 tools/release.py --addon plugin.video.sporthdme --version 0.1.85 --dry-run

# Publication réelle
python3 tools/release.py --addon plugin.video.sporthdme --version 0.1.85

# Avec une entrée de journal (news)
python3 tools/release.py --addon plugin.video.sporthdme --version 0.1.85 \
    --news "- fix Live_url to super.league.st\n- update s2watch resolver"

# Simplement régénérer le fichier md5 (après une modification manuelle de addons.xml)
python3 tools/release.py --refresh-md5
```

Le script **ne commit pas**. Il affiche les commandes git suggérées à la fin.

### Wrapper Bash

```bash
./tools/release.sh plugin.video.sporthdme 0.1.85
./tools/release.sh plugin.video.sporthdme 0.1.85 "- ligne de journal 1\n- ligne 2"
./tools/release.sh plugin.video.sporthdme 0.1.85 --dry-run
```

## diagnose.py

Sondages HTTP en direct contre les sites amont utilisés par `plugin.video.sporthdme`.
Signale quels motifs de scraping/résolution fonctionnent encore. À utiliser avant et
après les corrections.

```bash
# Interroger super.league.st/events et vérifier les deux regex d'extraction d'événements
python3 tools/diagnose.py events

# Interroger le point d'accès tRPC de one.sporthd.me pour la liste des chaînes 24/7
python3 tools/diagnose.py channels

# Tester une URL de flux individuelle et voir quelle signature de lecteur correspond
python3 tools/diagnose.py resolve "https://example.host/embed/stream-1"

# Événements + chaînes en une seule exécution
python3 tools/diagnose.py all

# Remplacer les valeurs par défaut
python3 tools/diagnose.py events --live-url https://super.league.st
python3 tools/diagnose.py channels --base-url https://one.sporthd.me/
```

Lorsque la page des événements ne peut pas être analysée, son contenu est enregistré dans
`/tmp/sporthd_events_body.html` afin que vous puissiez examiner ce qui a changé.

## Publication CI (GitHub Actions)

`.github/workflows/release.yml` exécute le même flux que `release.py` en CI. Deux
options de déclenchement :

1. **Pousser un tag** comme `plugin.video.sporthdme-0.1.85` :

   ```bash
   git tag plugin.video.sporthdme-0.1.85
   git push origin plugin.video.sporthdme-0.1.85
   ```

   Le workflow analyse le tag, exécute le pipeline de publication, committe les
   fichiers mis à jour vers `master` et téléverse le nouveau zip comme
   asset d'une GitHub Release.

2. **Déclenchement manuel** depuis l'onglet Actions : choisissez l'id de l'add-on,
   la version et éventuellement le texte du journal.

Pour un fonctionnement strictement local (par exemple si vous ne voulez pas que la CI
pousse des commits), continuez simplement à utiliser `tools/release.py` et faites
vous-même le commit/push.

## Cycle de maintenance type pour `plugin.video.sporthdme`

1. `python3 tools/diagnose.py all` pour voir l'état actuel des sites amont.
2. Corriger `plugin.video.sporthdme/default.py` (ou les modules sous `resources/`).
3. Relancer `python3 tools/diagnose.py events` pour confirmer que l'analyseur
   accepte toujours le HTML en ligne.
4. Tester quelques URL de flux avec `python3 tools/diagnose.py resolve <url>`
   pour détecter les résolveurs cassés.
5. `python3 tools/release.py --addon plugin.video.sporthdme --version <suivante>`.
6. Vérifier avec `git diff` et pousser.
