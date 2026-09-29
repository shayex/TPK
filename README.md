# Mémo Réunions

Une **simple page HTML** pour garder la trace de toutes les réunions, projet par
projet, et se rappeler ce qui a été décidé et ce qu'il reste à faire.

Aucune installation, aucun serveur : on ouvre `index.html` dans le navigateur
(double-clic) et c'est prêt.

## Fonctionnalités

- 📁 **Historique par projet** : date, participants, notes, décisions, date de la prochaine réunion.
- ✅ **Actions à suivre** avec responsable et échéance, cochables en un clic.
- ⚠️ **Tableau de bord** : actions en retard, échéances sous 14 jours, réunions de suivi
  à venir, dernières réunions.
- 🔁 **« Réunion de suivi »** : reprend le projet, les participants et les actions encore
  ouvertes de la réunion précédente.
- 🔎 **Recherche** dans tous les comptes rendus (sans tenir compte des accents).
- 📅 **Export calendrier (.ics)** à ouvrir dans Outlook : échéances et réunions de suivi
  ajoutées à l'agenda, avec alerte la veille.
- 📝 **Export Markdown** d'une réunion, d'un projet ou de tout.

## Saisir les actions

Une action par ligne dans le champ « Actions à suivre » :

```
Commander les switchs du bâtiment B @moi !15/10/2026
Valider le plan d'adressage @j.dupont !2026-10-20
[x] Envoyer le compte rendu au prestataire
```

- `@nom` : responsable (sans espace, ex. `@j.dupont`) ;
- `!date` : échéance (`JJ/MM/AAAA` ou `AAAA-MM-JJ`) ;
- `[x]` en début de ligne : action terminée.

## ⚠️ Où sont stockées les données ?

Dans le **navigateur** (localStorage), sur le poste où la page est ouverte :

- elles ne sont pas partagées entre postes ni entre navigateurs ;
- vider les données de navigation les efface.

👉 Utilisez la page **Sauvegarde** pour exporter régulièrement un fichier `.json`
dans un dossier sauvegardé (partage réseau, OneDrive…). Ce fichier se réimporte
sur n'importe quel poste. Un bandeau le rappelle si le dernier export date de plus
d'une semaine.

Conseil : ouvrez toujours la page depuis le même emplacement (ex.
`C:\Outils\memo-reunions\index.html`) et avec le même navigateur, car le stockage
est lié au chemin du fichier.
