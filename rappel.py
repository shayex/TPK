#!/usr/bin/env python3
"""Récapitulatif par e-mail des réunions et actions de Mémo Réunions.

À planifier chaque matin (cron ou timer systemd), par exemple :
    0 8 * * 1-5  cd /opt/memo-reunions && python3 rappel.py

Configuration par variables d'environnement :
    REUNIONS_DB        chemin de la base SQLite (défaut : reunions.db)
    REUNIONS_URL       adresse de l'application, pour les liens (ex. http://srv-memo:8080)
    SMTP_HOST          serveur SMTP (ex. relais interne)      [obligatoire sauf --stdout]
    SMTP_PORT          port (défaut 25)
    SMTP_STARTTLS      1 pour activer STARTTLS
    SMTP_UTILISATEUR / SMTP_MOT_DE_PASSE   authentification facultative
    RAPPEL_DE          expéditeur (ex. memo-reunions@tipiak.fr)
    RAPPEL_A           destinataire(s), séparés par des virgules
"""

import argparse
import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage

import app


def construire_rappel(conn, aujourd_hui=None, jours=7, base_url=""):
    """Renvoie (sujet, texte) ou None s'il n'y a rien à signaler."""
    aujourd_hui = aujourd_hui or date.today()
    auj = aujourd_hui.isoformat()
    horizon = (aujourd_hui + timedelta(days=jours)).isoformat()
    depuis = (aujourd_hui - timedelta(days=jours)).isoformat()

    ouvertes = app.actions_ouvertes(conn)
    en_retard = [a for a in ouvertes if a["echeance"] and a["echeance"] < auj]
    a_venir = [a for a in ouvertes if a["echeance"] and auj <= a["echeance"] <= horizon]
    suivis = app.prochaines_reunions(conn, auj, horizon)
    recentes = [r for r in app.reunions(conn) if depuis <= r["date"] <= auj]

    if not (en_retard or a_venir or suivis or recentes):
        return None

    def lien(chemin):
        return f"  {base_url}{chemin}" if base_url else ""

    def ligne_action(a):
        qui = f" — {a['responsable']}" if a["responsable"] else ""
        return (f"  • [{a['projet']}] {a['texte']}{qui} (échéance {a['echeance']})"
                + lien(f"/reunions/{a['reunion_id']}"))

    parties = [f"Mémo Réunions — point du {aujourd_hui:%d/%m/%Y}", ""]
    if en_retard:
        parties += [f"⚠ ACTIONS EN RETARD ({len(en_retard)})"] + [ligne_action(a) for a in en_retard] + [""]
    if a_venir:
        parties += [f"ÉCHÉANCES DANS LES {jours} PROCHAINS JOURS ({len(a_venir)})"]
        parties += [ligne_action(a) for a in a_venir] + [""]
    if suivis:
        parties += [f"RÉUNIONS DE SUIVI PRÉVUES ({len(suivis)})"]
        parties += [f"  • {r['prochaine']} [{r['projet']}] suite de « {r['titre']} »"
                    + lien(f"/reunions/{r['id']}") for r in suivis] + [""]
    if recentes:
        parties += [f"RÉUNIONS DES {jours} DERNIERS JOURS ({len(recentes)})"]
        for r in recentes:
            parties.append(f"  • {r['date']} [{r['projet']}] {r['titre']}" + lien(f"/reunions/{r['id']}"))
            if r["decisions"]:
                for d in r["decisions"].splitlines():
                    if d.strip():
                        parties.append(f"      décision : {d.strip().lstrip('-* ')}")
        parties.append("")

    sujet = "Mémo Réunions : "
    sujet += f"{len(en_retard)} action(s) en retard, " if en_retard else ""
    sujet += f"{len(a_venir)} échéance(s) proche(s), {len(suivis)} réunion(s) de suivi"
    return sujet, "\n".join(parties)


def envoyer(sujet, texte):
    hote = os.environ.get("SMTP_HOST")
    destinataires = [d.strip() for d in os.environ.get("RAPPEL_A", "").split(",") if d.strip()]
    if not hote or not destinataires:
        sys.exit("SMTP_HOST et RAPPEL_A doivent être définis (ou utilisez --stdout).")
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = os.environ.get("RAPPEL_DE", destinataires[0])
    msg["To"] = ", ".join(destinataires)
    msg.set_content(texte)
    with smtplib.SMTP(hote, int(os.environ.get("SMTP_PORT", "25")), timeout=30) as smtp:
        if os.environ.get("SMTP_STARTTLS") == "1":
            smtp.starttls()
        if os.environ.get("SMTP_UTILISATEUR"):
            smtp.login(os.environ["SMTP_UTILISATEUR"], os.environ.get("SMTP_MOT_DE_PASSE", ""))
        smtp.send_message(msg)


def main():
    parser = argparse.ArgumentParser(description="Envoie le récapitulatif Mémo Réunions")
    parser.add_argument("--db", default=app.DB_PATH)
    parser.add_argument("--jours", type=int, default=7, help="horizon en jours (défaut 7)")
    parser.add_argument("--stdout", action="store_true", help="afficher au lieu d'envoyer")
    parser.add_argument("--toujours", action="store_true", help="envoyer même si rien à signaler")
    args = parser.parse_args()

    conn = app.connect(args.db)
    rappel = construire_rappel(conn, jours=args.jours,
                               base_url=os.environ.get("REUNIONS_URL", "").rstrip("/"))
    if rappel is None:
        if not args.toujours:
            print("Rien à signaler.")
            return
        rappel = ("Mémo Réunions : rien à signaler", "Aucune action ni réunion à signaler.")
    sujet, texte = rappel
    if args.stdout:
        print(sujet, "", texte, sep="\n")
    else:
        envoyer(sujet, texte)
        print(f"Récapitulatif envoyé : {sujet}")


if __name__ == "__main__":
    main()
