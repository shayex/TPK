# Mémo Réunions

Petit outil interne pour **garder la trace de toutes les réunions, projet par projet**,
et **être relancé** sur ce qui a été décidé et ce qu'il reste à faire.

- 📁 **Un historique par projet** : date, participants, notes, décisions.
- ✅ **Des actions à suivre** avec responsable et échéance, cochables en un clic.
- ⚠️ **Un tableau de bord** : actions en retard, échéances sous 14 jours,
  réunions de suivi à venir, dernières réunions.
- 🔁 **« Réunion de suivi »** : reprend le projet, les participants et les actions
  encore ouvertes de la réunion précédente.
- 🔎 **Recherche** dans tous les comptes rendus (« qu'avait-on décidé sur le VLAN invité ? »).
- 📅 **Flux calendrier** `/actions.ics` à ajouter dans Outlook : échéances et réunions
  de suivi apparaissent dans l'agenda, avec rappel la veille.
- ✉️ **Récapitulatif e-mail** chaque matin (`rappel.py`) : retards, échéances proches,
  décisions de la semaine.
- 📝 **Export Markdown** d'une réunion ou d'un projet complet.

Aucune dépendance : Python 3.9+ et sa bibliothèque standard, données dans un seul
fichier SQLite (facile à sauvegarder).

## Démarrage rapide

```bash
python3 app.py            # http://127.0.0.1:8080
```

Options : `--host 0.0.0.0` pour l'ouvrir au réseau, `--port`, `--db chemin/reunions.db`.

## Saisir les actions

Dans le champ « Actions à suivre », une action par ligne :

```
Commander les switchs du bâtiment B @moi !2026-10-15
Valider le plan d'adressage @j.dupont !15/10/2026
[x] Envoyer le compte rendu au prestataire
```

- `@nom` : responsable (sans espace, ex. `@j.dupont`) ;
- `!date` : échéance (`AAAA-MM-JJ` ou `JJ/MM/AAAA`) ;
- `[x]` en début de ligne : action terminée.

La page **Actions** liste toutes les actions ouvertes, filtrables par responsable.

## Être rappelé

### Dans Outlook (flux calendrier)

Outlook → *Ajouter un calendrier* → *À partir d'Internet* →
`http://srv-memo:8080/actions.ics` (ou `…/actions.ics?jeton=VOTRE_JETON` si
l'authentification est activée). Chaque échéance d'action et chaque réunion de
suivi devient un événement d'une journée avec alarme.

### Par e-mail

```bash
python3 rappel.py --stdout        # aperçu dans le terminal
python3 rappel.py                 # envoi via SMTP (voir variables ci-dessous)
```

Aucun e-mail n'est envoyé s'il n'y a rien à signaler (sauf `--toujours`).
Planification : timer systemd fourni (lun.–ven. 8 h) ou cron
`0 8 * * 1-5 cd /opt/memo-reunions && python3 rappel.py`.

## Configuration (variables d'environnement)

| Variable | Rôle |
|---|---|
| `REUNIONS_DB` | Chemin de la base SQLite (défaut `reunions.db`) |
| `REUNIONS_HOST` / `REUNIONS_PORT` | Écoute du serveur (défaut `127.0.0.1:8080`) |
| `REUNIONS_URL` | URL publique, utilisée pour les liens de l'e-mail et du calendrier |
| `REUNIONS_UTILISATEUR` / `REUNIONS_MOT_DE_PASSE` | Active l'authentification HTTP Basic |
| `REUNIONS_JETON_ICS` | Jeton pour lire `/actions.ics?jeton=…` sans mot de passe (Outlook) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_UTILISATEUR`, `SMTP_MOT_DE_PASSE` | Envoi du récapitulatif |
| `RAPPEL_DE` / `RAPPEL_A` | Expéditeur / destinataires (séparés par des virgules) |

Exemple complet : [`deploy/memo-reunions.env.exemple`](deploy/memo-reunions.env.exemple).

## Déploiement sur un serveur Linux (systemd)

```bash
sudo mkdir -p /opt/memo-reunions && sudo cp app.py rappel.py /opt/memo-reunions/
sudo install -m 600 deploy/memo-reunions.env.exemple /etc/memo-reunions.env   # puis éditer
sudo cp deploy/memo-reunions*.service deploy/memo-reunions-rappel.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memo-reunions.service memo-reunions-rappel.timer
```

La base est alors dans `/var/lib/memo-reunions/reunions.db` : pensez à l'inclure
dans vos sauvegardes (`sqlite3 reunions.db ".backup copie.db"` pour une copie à chaud).

> ⚠️ L'authentification Basic n'est pas chiffrée : si l'outil est accessible au-delà
> de votre poste, placez-le derrière un reverse proxy HTTPS (nginx, IIS, Traefik…).

## Docker

```bash
docker build -t memo-reunions .
docker run -d --name memo-reunions -p 8080:8080 -v memo-reunions:/data \
  -e REUNIONS_UTILISATEUR=admin -e REUNIONS_MOT_DE_PASSE=changez-moi memo-reunions
docker exec memo-reunions python3 rappel.py --stdout
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```
