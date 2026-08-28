# Climatologie mensuelle Météo-France — tableaux WordPress

Ce dépôt construit un tableau de **climatologie mensuelle par station** (relevés jour par jour + statistiques du mois), sur le modèle des pages « Climatologie mensuelle » des sites météo grand public, mais sourcé exclusivement en **données publiques Météo-France**.

## Production

- source : Météo-France, jeu de données « [Données climatologiques de base - quotidiennes](https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes) » (data.gouv.fr, Licence Ouverte / Etalab 2.0) ;
- aucune clé API nécessaire — fichiers CSV compressés téléchargés directement, par département ;
- **historique complet** : toutes les périodes publiées par Météo-France sont récupérées pour chaque département (« avant-1949 », « 1950-2024 », « 2025-2026 », etc. — les bornes glissent chaque année et sont résolues dynamiquement via l'API data.gouv.fr, jamais codées en dur). Certaines stations parisiennes remontent à 1816 ;
- ~1,5 Go de CSV compressés à télécharger par exécution complète (95 départements). Pour rester publiable et léger côté visiteur, chaque station est éclatée en un fichier JSON **par année** (`stations/<num_poste>/<année>.json`) plutôt qu'un fichier unique avec tout l'historique : le site ne télécharge que l'année réellement consultée ;
- comparaison aux normales 1991-2020 pas encore incluse ;
- couverture : France métropolitaine (le jeu de données Météo-France regroupe la Corse sous un code unique « 20 ») ;
- pour chaque station : température maxi./mini. quotidiennes, précipitations 24h, durée d'ensoleillement (quand la station la mesure) ;
- statistiques du mois : jours de chaleur (Tmax ≥ 25°C), forte chaleur (≥ 30°C), très forte chaleur (≥ 35°C), nuit tropicale (Tmin ≥ 20°C), gelée (≤ 0°C), forte gelée (≤ -5°C), très forte gelée (≤ -10°C), jours sans dégel (Tmax ≤ 0°C), jours de pluie (RR ≥ 1 mm).

## Installation GitHub

1. Copiez tout le contenu de cette archive à la racine du dépôt `alertesmeteo-hub/climato`.
2. Dans **Settings → Actions → General → Workflow permissions**, activez **Read and write permissions**.
3. Lancez **Actions → Mise à jour Climatologie France → Run workflow**.
4. Vérifiez ensuite la branche `data` et son fichier `index.json`.

Le workflow automatique est lancé une seule fois par jour à 07 h 15 UTC. Compte tenu du volume (~1,5 Go à télécharger et des dizaines de millions de relevés à fusionner), une exécution complète peut prendre significativement plus longtemps qu'une mise à jour cep/gfs ; le timeout du workflow est fixé à 3 h par prudence.

Commande locale équivalente :

```bash
python -m pip install -r requirements.txt
python scripts/update_climato_france.py --output-dir build/national
```

## Installation WordPress

Installez le ZIP séparé `climato-meteofrance-france-v1.1.0.zip`, activez-le, puis utilisez :

```text
[climato_meteo]
```

Exemple :

```text
[climato_meteo departement="28" station="28198001"]
```

L'URL de données par défaut est :

```text
https://raw.githubusercontent.com/alertesmeteo-hub/climato/data
```

## Sources

- [Données climatologiques de base - quotidiennes — data.gouv.fr](https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes) (Météo-France, Licence Ouverte / Etalab 2.0)

Site : [www.alertes-meteo.com](https://www.alertes-meteo.com/) — module v1.1.0.
