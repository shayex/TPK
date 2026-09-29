#!/usr/bin/env python3
"""Mémo Réunions — garder la trace des réunions de chaque projet.

Application web autonome (bibliothèque standard Python uniquement, base SQLite) :
  * compte rendu de chaque réunion rangé par projet (participants, notes, décisions) ;
  * actions à suivre avec responsable et échéance ;
  * tableau de bord : actions en retard, échéances proches, prochaines réunions ;
  * recherche plein texte, export Markdown ;
  * flux calendrier (.ics) à ajouter dans Outlook pour les rappels.

Lancement : python3 app.py --host 0.0.0.0 --port 8080
"""

import argparse
import base64
import hmac
import html
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

DB_PATH = os.environ.get("REUNIONS_DB", "reunions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projets (
    id  INTEGER PRIMARY KEY,
    nom TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS reunions (
    id           INTEGER PRIMARY KEY,
    projet_id    INTEGER NOT NULL REFERENCES projets(id) ON DELETE CASCADE,
    titre        TEXT NOT NULL,
    date         TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    decisions    TEXT NOT NULL DEFAULT '',
    prochaine    TEXT,
    cree_le      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS actions (
    id          INTEGER PRIMARY KEY,
    reunion_id  INTEGER NOT NULL REFERENCES reunions(id) ON DELETE CASCADE,
    texte       TEXT NOT NULL,
    responsable TEXT NOT NULL DEFAULT '',
    echeance    TEXT,
    faite       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reunions_projet ON reunions(projet_id, date);
CREATE INDEX IF NOT EXISTS idx_actions_reunion ON actions(reunion_id);
"""


# --------------------------------------------------------------------------
# Données
# --------------------------------------------------------------------------

def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def parse_date(value):
    """Accepte AAAA-MM-JJ ou JJ/MM/AAAA ; renvoie AAAA-MM-JJ ou None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Date invalide : {value!r} (format attendu AAAA-MM-JJ)")


_ACTION_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[(?P<etat>[ xX])\]\s*)?(?P<corps>.*?)\s*$")
_ACTION_DATE = re.compile(r"(?:^|\s)!(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})(?=\s|$)")
_ACTION_RESP = re.compile(r"(?:^|\s)@([\w.\-']+)")


def parse_actions(text):
    """Une action par ligne : ``[x] Texte @responsable !2026-10-15``.

    ``[x]`` marque l'action comme faite, ``@nom`` désigne le responsable et
    ``!date`` l'échéance ; tous sont facultatifs.
    """
    actions = []
    for line in (text or "").splitlines():
        m = _ACTION_LINE.match(line)
        corps = m.group("corps")
        if not corps:
            continue
        echeance = None
        dm = _ACTION_DATE.search(corps)
        if dm:
            echeance = parse_date(dm.group(1))
            corps = corps[: dm.start()] + corps[dm.end():]
        responsable = ""
        rm = _ACTION_RESP.search(corps)
        if rm:
            responsable = rm.group(1)
            corps = corps[: rm.start()] + corps[rm.end():]
        texte = " ".join(corps.split())
        if not texte:
            continue
        actions.append({
            "texte": texte,
            "responsable": responsable,
            "echeance": echeance,
            "faite": 1 if (m.group("etat") or " ").lower() == "x" else 0,
        })
    return actions


def format_actions(actions):
    lignes = []
    for a in actions:
        ligne = f"- [{'x' if a['faite'] else ' '}] {a['texte']}"
        if a["responsable"]:
            ligne += f" @{a['responsable']}"
        if a["echeance"]:
            ligne += f" !{a['echeance']}"
        lignes.append(ligne)
    return "\n".join(lignes)


def projet_id(conn, nom):
    nom = " ".join((nom or "").split())
    if not nom:
        raise ValueError("Le projet est obligatoire.")
    conn.execute("INSERT OR IGNORE INTO projets(nom) VALUES (?)", (nom,))
    return conn.execute("SELECT id FROM projets WHERE nom = ?", (nom,)).fetchone()["id"]


def save_reunion(conn, data, reunion_id=None):
    """Crée ou met à jour une réunion à partir d'un dictionnaire de formulaire."""
    titre = (data.get("titre") or "").strip()
    if not titre:
        raise ValueError("Le titre est obligatoire.")
    jour = parse_date(data.get("date"))
    if not jour:
        raise ValueError("La date est obligatoire.")
    champs = {
        "projet_id": projet_id(conn, data.get("projet")),
        "titre": titre,
        "date": jour,
        "participants": (data.get("participants") or "").strip(),
        "notes": (data.get("notes") or "").strip(),
        "decisions": (data.get("decisions") or "").strip(),
        "prochaine": parse_date(data.get("prochaine")),
    }
    actions = parse_actions(data.get("actions"))
    with conn:
        if reunion_id is None:
            cur = conn.execute(
                "INSERT INTO reunions(projet_id, titre, date, participants, notes, decisions, prochaine)"
                " VALUES (:projet_id, :titre, :date, :participants, :notes, :decisions, :prochaine)",
                champs,
            )
            reunion_id = cur.lastrowid
        else:
            cur = conn.execute(
                "UPDATE reunions SET projet_id=:projet_id, titre=:titre, date=:date,"
                " participants=:participants, notes=:notes, decisions=:decisions,"
                " prochaine=:prochaine WHERE id=:id",
                dict(champs, id=reunion_id),
            )
            if cur.rowcount == 0:
                raise KeyError(reunion_id)
            conn.execute("DELETE FROM actions WHERE reunion_id = ?", (reunion_id,))
        conn.executemany(
            "INSERT INTO actions(reunion_id, texte, responsable, echeance, faite)"
            " VALUES (?, ?, ?, ?, ?)",
            [(reunion_id, a["texte"], a["responsable"], a["echeance"], a["faite"]) for a in actions],
        )
    return reunion_id


def get_reunion(conn, reunion_id):
    r = conn.execute(
        "SELECT r.*, p.nom AS projet FROM reunions r JOIN projets p ON p.id = r.projet_id"
        " WHERE r.id = ?",
        (reunion_id,),
    ).fetchone()
    if r is None:
        return None
    r = dict(r)
    r["actions"] = [dict(a) for a in conn.execute(
        "SELECT * FROM actions WHERE reunion_id = ? ORDER BY id", (reunion_id,))]
    return r


_ACTIONS_SQL = (
    "SELECT a.*, r.titre AS reunion, r.date AS reunion_date, p.nom AS projet, p.id AS projet_id"
    " FROM actions a JOIN reunions r ON r.id = a.reunion_id JOIN projets p ON p.id = r.projet_id"
)


def actions_ouvertes(conn, responsable=None, projet=None):
    sql = _ACTIONS_SQL + " WHERE a.faite = 0"
    args = []
    if responsable:
        sql += " AND a.responsable = ? COLLATE NOCASE"
        args.append(responsable)
    if projet:
        sql += " AND p.id = ?"
        args.append(projet)
    sql += " ORDER BY a.echeance IS NULL, a.echeance, r.date"
    return [dict(a) for a in conn.execute(sql, args)]


def projets(conn):
    return [dict(p) for p in conn.execute(
        "SELECT p.id, p.nom, COUNT(r.id) AS nb, MAX(r.date) AS derniere,"
        " (SELECT COUNT(*) FROM actions a JOIN reunions r2 ON r2.id = a.reunion_id"
        "   WHERE r2.projet_id = p.id AND a.faite = 0) AS ouvertes"
        " FROM projets p LEFT JOIN reunions r ON r.projet_id = p.id"
        " GROUP BY p.id HAVING nb > 0 ORDER BY derniere DESC")]


def reunions(conn, projet=None, limit=None):
    sql = ("SELECT r.*, p.nom AS projet FROM reunions r JOIN projets p ON p.id = r.projet_id")
    args = []
    if projet:
        sql += " WHERE p.id = ?"
        args.append(projet)
    sql += " ORDER BY r.date DESC, r.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(sql, args)]


def prochaines_reunions(conn, depuis, jusqu_a=None):
    """Réunions de suivi annoncées (champ « prochaine ») à partir de ``depuis``."""
    sql = ("SELECT r.id, r.titre, r.prochaine, p.nom AS projet, p.id AS projet_id"
           " FROM reunions r JOIN projets p ON p.id = r.projet_id"
           " WHERE r.prochaine >= ?")
    args = [depuis]
    if jusqu_a:
        sql += " AND r.prochaine <= ?"
        args.append(jusqu_a)
    return [dict(r) for r in conn.execute(sql + " ORDER BY r.prochaine", args)]


def rechercher(conn, q):
    termes = q.split()
    if not termes:
        return []
    champs = ("r.titre || ' ' || r.participants || ' ' || r.notes || ' ' || r.decisions"
              " || ' ' || p.nom || ' ' || COALESCE((SELECT group_concat(a.texte || ' ' ||"
              " a.responsable, ' ') FROM actions a WHERE a.reunion_id = r.id), '')")
    sql = ("SELECT r.*, p.nom AS projet FROM reunions r JOIN projets p ON p.id = r.projet_id WHERE "
           + " AND ".join(f"({champs}) LIKE ? ESCAPE '\\'" for _ in termes)
           + " ORDER BY r.date DESC")
    motifs = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
              for t in termes]
    return [dict(r) for r in conn.execute(sql, motifs)]


def reunion_markdown(r):
    lignes = [f"## {r['date']} — {r['titre']}", "", f"**Projet :** {r['projet']}  "]
    if r["participants"]:
        lignes.append(f"**Participants :** {r['participants']}  ")
    if r["prochaine"]:
        lignes.append(f"**Prochaine réunion :** {r['prochaine']}  ")
    for titre, cle in (("Notes", "notes"), ("Décisions", "decisions")):
        if r[cle]:
            lignes += ["", f"### {titre}", "", r[cle]]
    if r["actions"]:
        lignes += ["", "### Actions", "", format_actions(r["actions"])]
    return "\n".join(lignes) + "\n"


def projet_markdown(conn, pid):
    p = conn.execute("SELECT nom FROM projets WHERE id = ?", (pid,)).fetchone()
    if p is None:
        return None
    parties = [f"# Projet {p['nom']}\n"]
    for r in reunions(conn, projet=pid):
        parties.append(reunion_markdown(get_reunion(conn, r["id"])))
    return "\n".join(parties)


def _ics_texte(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _ics_plier(ligne):
    """Plie les lignes à 75 octets (RFC 5545 §3.1)."""
    brut = ligne.encode("utf-8")
    if len(brut) <= 75:
        return ligne
    morceaux, courant = [], b""
    for car in ligne:
        b = car.encode("utf-8")
        if len(courant) + len(b) > (75 if not morceaux else 74):
            morceaux.append(courant.decode("utf-8"))
            courant = b""
        courant += b
    morceaux.append(courant.decode("utf-8"))
    return "\r\n ".join(morceaux)


def calendrier_ics(conn, base_url=""):
    """Échéances des actions ouvertes et prochaines réunions, en événements d'une journée."""
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lignes = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Memo Reunions//FR",
              "CALSCALE:GREGORIAN", "X-WR-CALNAME:Mémo Réunions"]

    def evenement(uid, jour, resume, description, lien):
        debut = date.fromisoformat(jour)
        lignes.extend([
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{horodatage}",
            f"DTSTART;VALUE=DATE:{debut:%Y%m%d}",
            f"DTEND;VALUE=DATE:{debut + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_texte(resume)}", f"DESCRIPTION:{_ics_texte(description)}",
        ])
        if base_url:
            lignes.append(f"URL:{base_url}{lien}")
        lignes.extend(["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_texte(resume)}",
                       "TRIGGER:-PT15H", "END:VALARM", "END:VEVENT"])

    for a in actions_ouvertes(conn):
        if a["echeance"]:
            qui = f" ({a['responsable']})" if a["responsable"] else ""
            evenement(f"action-{a['id']}@memo-reunions", a["echeance"],
                      f"[{a['projet']}] Action : {a['texte']}{qui}",
                      f"Décidée lors de « {a['reunion']} » du {a['reunion_date']}.",
                      f"/reunions/{a['reunion_id']}")
    for r in prochaines_reunions(conn, (date.today() - timedelta(days=30)).isoformat()):
        evenement(f"suivi-{r['id']}@memo-reunions", r["prochaine"],
                  f"[{r['projet']}] Réunion de suivi : {r['titre']}",
                  "Relire le compte rendu précédent avant la réunion.",
                  f"/reunions/{r['id']}")
    lignes.append("END:VCALENDAR")
    return "\r\n".join(_ics_plier(l) for l in lignes) + "\r\n"


# --------------------------------------------------------------------------
# Pages HTML
# --------------------------------------------------------------------------

e = html.escape

CSS = """
:root{--fond:#f6f7f9;--carte:#fff;--texte:#1d2330;--doux:#5d6677;--bord:#dde1e8;
--accent:#1f5fbf;--retard:#b3261e;--ok:#2e7d32;--proche:#a15c00}
@media (prefers-color-scheme:dark){:root{--fond:#14171c;--carte:#1d2129;--texte:#e6e9ef;
--doux:#9aa3b2;--bord:#2e3440;--accent:#7fb0ff;--retard:#ff8a80;--ok:#81c784;--proche:#ffb74d}}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{background:var(--carte);border-bottom:1px solid var(--bord);padding:10px 16px;
display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header a.logo{font-weight:700;color:var(--texte);text-decoration:none}
header form{margin-left:auto;display:flex;gap:6px}
main{max-width:1000px;margin:0 auto;padding:16px}
a{color:var(--accent)}h1{font-size:1.5rem;margin:.2em 0 .6em}h2{font-size:1.1rem;margin:1.4em 0 .5em}
.carte h2:first-child{margin-top:0}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:8px;padding:12px 16px;margin-bottom:12px}
.grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.doux{color:var(--doux);font-size:.9em}.retard{color:var(--retard);font-weight:600}
.proche{color:var(--proche);font-weight:600}.faite{text-decoration:line-through;color:var(--doux)}
ul.liste{list-style:none;padding:0;margin:0}ul.liste li{padding:6px 0;border-bottom:1px solid var(--bord)}
ul.liste li:last-child{border-bottom:0}
.texte{white-space:pre-wrap}
label{display:block;font-weight:600;margin-top:12px}
input,textarea{width:100%;padding:8px;border:1px solid var(--bord);border-radius:6px;
background:var(--carte);color:var(--texte);font:inherit}
textarea{min-height:110px}.ligne{display:flex;gap:12px;flex-wrap:wrap}.ligne>div{flex:1;min-width:180px}
button,.bouton{display:inline-block;padding:7px 14px;border-radius:6px;border:1px solid var(--accent);
background:var(--accent);color:var(--fond);font:inherit;cursor:pointer;text-decoration:none}
.secondaire{background:transparent;color:var(--accent)}.danger{border-color:var(--retard);
background:transparent;color:var(--retard)}
form.inline{display:inline}button.mini{padding:0 6px;font-size:.85em}
.erreur{background:#fdecea;color:#8a1c14;padding:8px 12px;border-radius:6px}
.actions-barre{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
"""


def page(titre, corps, q=""):
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titre)} · Mémo Réunions</title><style>{CSS}</style></head><body>
<header><a class="logo" href="/">📋 Mémo Réunions</a>
<a href="/reunions/nouvelle">+ Nouvelle réunion</a><a href="/actions">Actions</a>
<form action="/recherche"><input name="q" value="{e(q)}" placeholder="Rechercher…" aria-label="Rechercher">
<button>OK</button></form></header><main>{corps}</main></body></html>"""


def classe_echeance(echeance, aujourd_hui):
    if not echeance:
        return ""
    if echeance < aujourd_hui:
        return "retard"
    if echeance <= (date.fromisoformat(aujourd_hui) + timedelta(days=7)).isoformat():
        return "proche"
    return ""


def html_action(a, aujourd_hui, avec_contexte=True, retour="/"):
    cls = "faite" if a["faite"] else ""
    details = []
    if a["responsable"]:
        details.append(f"👤 {e(a['responsable'])}")
    if a["echeance"]:
        details.append(f'<span class="{classe_echeance(a["echeance"], aujourd_hui) if not a["faite"] else ""}">'
                       f"📅 {e(a['echeance'])}</span>")
    if avec_contexte:
        details.append(f'<a href="/reunions/{a["reunion_id"]}">{e(a["projet"])} · {e(a["reunion"])}</a>')
    bouton = "Rouvrir" if a["faite"] else "Fait ✓"
    return (f'<li><span class="{cls}">{e(a["texte"])}</span> '
            f'<form class="inline" method="post" action="/actions/{a["id"]}/basculer">'
            f'<input type="hidden" name="retour" value="{e(retour)}">'
            f'<button class="mini secondaire">{bouton}</button></form>'
            f'<div class="doux">{" · ".join(details)}</div></li>')


def html_liste_reunions(liste, vide="Aucune réunion."):
    if not liste:
        return f'<p class="doux">{vide}</p>'
    items = "".join(
        f'<li><a href="/reunions/{r["id"]}">{e(r["titre"])}</a> '
        f'<span class="doux">— {e(r["date"])} · <a href="/projets/{r["projet_id"]}">{e(r["projet"])}</a></span></li>'
        for r in liste)
    return f'<ul class="liste">{items}</ul>'


def page_accueil(conn):
    auj = date.today().isoformat()
    ouvertes = actions_ouvertes(conn)
    en_retard = [a for a in ouvertes if a["echeance"] and a["echeance"] < auj]
    horizon = (date.today() + timedelta(days=14)).isoformat()
    bientot = [a for a in ouvertes if a["echeance"] and auj <= a["echeance"] <= horizon]
    suivis = prochaines_reunions(conn, auj, horizon)

    def bloc(titre, contenu):
        return f'<div class="carte"><h2>{titre}</h2>{contenu}</div>'

    def liste_actions(l, vide):
        if not l:
            return f'<p class="doux">{vide}</p>'
        return '<ul class="liste">' + "".join(html_action(a, auj) for a in l) + "</ul>"

    suivis_html = ('<ul class="liste">' + "".join(
        f'<li><span class="{classe_echeance(r["prochaine"], auj)}">📅 {e(r["prochaine"])}</span> '
        f'<a href="/reunions/{r["id"]}">{e(r["projet"])} · suite de « {e(r["titre"])} »</a></li>'
        for r in suivis) + "</ul>") if suivis else '<p class="doux">Aucune réunion de suivi prévue.</p>'

    liste_projets = projets(conn)
    projets_html = ('<ul class="liste">' + "".join(
        f'<li><a href="/projets/{p["id"]}">{e(p["nom"])}</a> <span class="doux">— {p["nb"]} réunion(s),'
        f' dernière le {e(p["derniere"])}, {p["ouvertes"]} action(s) ouverte(s)</span></li>'
        for p in liste_projets) + "</ul>") if liste_projets else (
        '<p class="doux">Aucun projet pour l\'instant. '
        '<a href="/reunions/nouvelle">Saisissez votre première réunion</a>.</p>')

    corps = (
        "<h1>Tableau de bord</h1><div class='grille'>"
        + bloc("⚠️ Actions en retard", liste_actions(en_retard, "Rien en retard 👍"))
        + bloc("⏳ Échéances sous 14 jours", liste_actions(bientot, "Aucune échéance proche."))
        + bloc("🗓️ Réunions de suivi à venir", suivis_html)
        + bloc("🕘 Dernières réunions", html_liste_reunions(reunions(conn, limit=8)))
        + "</div>" + bloc("📁 Projets", projets_html)
    )
    return page("Tableau de bord", corps)


def page_projet(conn, pid):
    p = conn.execute("SELECT * FROM projets WHERE id = ?", (pid,)).fetchone()
    if p is None:
        return None
    auj = date.today().isoformat()
    ouvertes = actions_ouvertes(conn, projet=pid)
    retour = f"/projets/{pid}"
    actions_html = ('<ul class="liste">' + "".join(html_action(a, auj, retour=retour) for a in ouvertes)
                    + "</ul>") if ouvertes else '<p class="doux">Aucune action ouverte.</p>'
    chrono = []
    for r in reunions(conn, projet=pid):
        extrait = ""
        if r["decisions"]:
            extrait = f'<div class="texte doux">Décisions : {e(r["decisions"][:400])}</div>'
        chrono.append(f'<div class="carte"><a href="/reunions/{r["id"]}"><strong>{e(r["date"])} — '
                      f'{e(r["titre"])}</strong></a><div class="doux">{e(r["participants"])}</div>{extrait}</div>')
    corps = (f"<h1>Projet {e(p['nom'])}</h1><div class='actions-barre'>"
             f"<a class='bouton' href='/reunions/nouvelle?projet={quote(p['nom'])}'>+ Réunion sur ce projet</a>"
             f"<a class='bouton secondaire' href='/projets/{pid}.md'>Exporter en Markdown</a></div>"
             f"<div class='carte'><h2>Actions ouvertes</h2>{actions_html}</div>"
             f"<h2>Historique des réunions</h2>{''.join(chrono) or '<p class=doux>Aucune réunion.</p>'}")
    return page(f"Projet {p['nom']}", corps)


def page_reunion(conn, rid):
    r = get_reunion(conn, rid)
    if r is None:
        return None
    auj = date.today().isoformat()
    retour = f"/reunions/{rid}"
    meta = [f"📅 {e(r['date'])}", f"📁 <a href='/projets/{r['projet_id']}'>{e(r['projet'])}</a>"]
    if r["participants"]:
        meta.append(f"👥 {e(r['participants'])}")
    if r["prochaine"]:
        meta.append(f"🔁 Prochaine réunion : <strong>{e(r['prochaine'])}</strong>")
    sections = ""
    for titre, cle in (("Notes", "notes"), ("Décisions", "decisions")):
        if r[cle]:
            sections += f'<div class="carte"><h2>{titre}</h2><div class="texte">{e(r[cle])}</div></div>'
    if r["actions"]:
        items = "".join(html_action(dict(a, projet=r["projet"], reunion=r["titre"]), auj,
                                    avec_contexte=False, retour=retour) for a in r["actions"])
        sections += f'<div class="carte"><h2>Actions</h2><ul class="liste">{items}</ul></div>'
    corps = (f"<h1>{e(r['titre'])}</h1><div class='doux'>{' · '.join(meta)}</div>"
             f"<div class='actions-barre'><a class='bouton' href='/reunions/{rid}/modifier'>Modifier</a>"
             f"<a class='bouton secondaire' href='/reunions/nouvelle?suite={rid}'>Réunion de suivi</a>"
             f"<a class='bouton secondaire' href='/reunions/{rid}.md'>Markdown</a>"
             f"<form class='inline' method='post' action='/reunions/{rid}/supprimer' "
             f"onsubmit=\"return confirm('Supprimer définitivement cette réunion ?')\">"
             f"<button class='danger'>Supprimer</button></form></div>{sections}")
    return page(r["titre"], corps)


def page_formulaire(conn, valeurs, action, erreur=None):
    noms = [p["nom"] for p in conn.execute("SELECT nom FROM projets ORDER BY nom")]
    options = "".join(f'<option value="{e(n)}">' for n in noms)
    v = {k: e(valeurs.get(k) or "") for k in
         ("projet", "titre", "date", "participants", "notes", "decisions", "actions", "prochaine")}
    err = f'<p class="erreur">{e(erreur)}</p>' if erreur else ""
    titre = "Modifier la réunion" if action != "/reunions" else "Nouvelle réunion"
    corps = f"""<h1>{titre}</h1>{err}<form method="post" action="{e(action)}" class="carte">
<div class="ligne"><div><label for="projet">Projet *</label>
<input id="projet" name="projet" list="projets" required value="{v['projet']}" placeholder="ex. Migration AD">
<datalist id="projets">{options}</datalist></div>
<div><label for="date">Date *</label><input id="date" type="date" name="date" required value="{v['date']}"></div></div>
<label for="titre">Titre *</label><input id="titre" name="titre" required value="{v['titre']}" placeholder="ex. Point d'avancement hebdo">
<label for="participants">Participants</label><input id="participants" name="participants" value="{v['participants']}" placeholder="ex. J. Martin, équipe réseau, prestataire X">
<label for="notes">Notes / points abordés</label><textarea id="notes" name="notes">{v['notes']}</textarea>
<label for="decisions">Décisions prises</label><textarea id="decisions" name="decisions">{v['decisions']}</textarea>
<label for="actions">Actions à suivre <span class="doux">— une par ligne : <code>Texte @responsable !AAAA-MM-JJ</code>, préfixer par <code>[x]</code> si faite</span></label>
<textarea id="actions" name="actions" placeholder="Commander les switchs @moi !2026-10-15&#10;Valider le plan d'adressage @jdupont">{v['actions']}</textarea>
<label for="prochaine">Date de la prochaine réunion</label><input id="prochaine" type="date" name="prochaine" value="{v['prochaine']}">
<div class="actions-barre"><button>Enregistrer</button><a class="bouton secondaire" href="/">Annuler</a></div></form>"""
    return page(titre, corps)


def page_actions(conn, responsable=None):
    auj = date.today().isoformat()
    liste = actions_ouvertes(conn, responsable=responsable)
    retour = "/actions" + (f"?responsable={quote(responsable)}" if responsable else "")
    gens = sorted({a["responsable"] for a in actions_ouvertes(conn) if a["responsable"]}, key=str.lower)
    filtres = " · ".join([f'<a href="/actions">Tous</a>'] + [
        f'<a href="/actions?responsable={quote(g)}">{e(g)}</a>' for g in gens])
    contenu = ('<ul class="liste">' + "".join(html_action(a, auj, retour=retour) for a in liste)
               + "</ul>") if liste else '<p class="doux">Aucune action ouverte.</p>'
    titre = f"Actions ouvertes — {responsable}" if responsable else "Actions ouvertes"
    corps = (f"<h1>{e(titre)}</h1><p class='doux'>Filtrer : {filtres}</p>"
             f"<div class='carte'>{contenu}</div>"
             f"<p class='doux'>Astuce : abonnez Outlook au flux <a href='/actions.ics'>/actions.ics</a> "
             f"pour voir les échéances et réunions de suivi dans votre calendrier.</p>")
    return page(titre, corps)


def page_recherche(conn, q):
    resultats = rechercher(conn, q)
    corps = (f"<h1>Recherche : {e(q)}</h1><p class='doux'>{len(resultats)} réunion(s) trouvée(s).</p>"
             f"<div class='carte'>{html_liste_reunions(resultats, 'Aucun résultat.')}</div>")
    return page("Recherche", corps, q=q)


# --------------------------------------------------------------------------
# Serveur HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "MemoReunions/1.0"
    db_path = None

    def log_message(self, fmt, *args):
        if os.environ.get("REUNIONS_LOG", "1") != "0":
            super().log_message(fmt, *args)

    # -- réponses --
    def envoyer(self, statut, corps, type_="text/html; charset=utf-8", entetes=None):
        donnees = corps.encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", type_)
        self.send_header("Content-Length", str(len(donnees)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (entetes or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(donnees)

    def rediriger(self, lieu):
        self.send_response(303)
        self.send_header("Location", lieu)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def introuvable(self):
        self.envoyer(404, page("Introuvable", "<h1>Page introuvable</h1><p><a href='/'>Retour</a></p>"))

    # -- sécurité --
    def autorise(self, chemin, requete):
        utilisateur = os.environ.get("REUNIONS_UTILISATEUR")
        mot_de_passe = os.environ.get("REUNIONS_MOT_DE_PASSE")
        if not (utilisateur and mot_de_passe):
            return True
        jeton = os.environ.get("REUNIONS_JETON_ICS")
        if chemin == "/actions.ics" and jeton and hmac.compare_digest(
                (requete.get("jeton") or [""])[0].encode(), jeton.encode()):
            return True
        entete = self.headers.get("Authorization", "")
        if entete.startswith("Basic "):
            try:
                u, _, m = base64.b64decode(entete[6:]).decode("utf-8").partition(":")
            except ValueError:
                return False
            return (hmac.compare_digest(u.encode(), utilisateur.encode())
                    and hmac.compare_digest(m.encode(), mot_de_passe.encode()))
        return False

    def meme_origine(self):
        origine = self.headers.get("Origin")
        if not origine or origine == "null":
            return origine is None
        return urlparse(origine).netloc == self.headers.get("Host")

    # -- routage --
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.traiter("GET")

    def do_POST(self):
        self.traiter("POST")

    def traiter(self, methode):
        url = urlparse(self.path)
        chemin = url.path.rstrip("/") or "/"
        requete = parse_qs(url.query)
        if not self.autorise(chemin, requete):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Memo Reunions", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        formulaire = {}
        if methode == "POST":
            if not self.meme_origine():
                self.envoyer(403, "Requête d'une autre origine refusée.", "text/plain; charset=utf-8")
                return
            taille = int(self.headers.get("Content-Length") or 0)
            if taille > 2_000_000:
                self.envoyer(413, "Contenu trop volumineux.", "text/plain; charset=utf-8")
                return
            brut = self.rfile.read(taille).decode("utf-8", "replace")
            formulaire = {k: v[0] for k, v in parse_qs(brut, keep_blank_values=True).items()}
        conn = connect(self.db_path)
        try:
            for m, motif, fonction in ROUTES:
                if m != methode:
                    continue
                trouve = re.fullmatch(motif, chemin)
                if trouve:
                    fonction(self, conn, requete, formulaire, *trouve.groups())
                    return
            self.introuvable()
        finally:
            conn.close()

    # -- vues --
    def v_accueil(self, conn, requete, formulaire):
        self.envoyer(200, page_accueil(conn))

    def v_projet(self, conn, requete, formulaire, pid):
        contenu = page_projet(conn, int(pid))
        self.envoyer(200, contenu) if contenu else self.introuvable()

    def v_projet_md(self, conn, requete, formulaire, pid):
        md = projet_markdown(conn, int(pid))
        if md is None:
            return self.introuvable()
        self.envoyer(200, md, "text/markdown; charset=utf-8",
                     {"Content-Disposition": f'inline; filename="projet-{pid}.md"'})

    def v_reunion(self, conn, requete, formulaire, rid):
        contenu = page_reunion(conn, int(rid))
        self.envoyer(200, contenu) if contenu else self.introuvable()

    def v_reunion_md(self, conn, requete, formulaire, rid):
        r = get_reunion(conn, int(rid))
        if r is None:
            return self.introuvable()
        self.envoyer(200, reunion_markdown(r), "text/markdown; charset=utf-8",
                     {"Content-Disposition": f'inline; filename="reunion-{rid}.md"'})

    def v_nouvelle(self, conn, requete, formulaire):
        valeurs = {"date": date.today().isoformat(), "projet": (requete.get("projet") or [""])[0]}
        suite = (requete.get("suite") or [""])[0]
        if suite.isdigit():
            precedente = get_reunion(conn, int(suite))
            if precedente:
                valeurs.update(
                    projet=precedente["projet"], titre=precedente["titre"],
                    participants=precedente["participants"],
                    date=precedente["prochaine"] or date.today().isoformat(),
                    notes=f"Suite de la réunion du {precedente['date']}.",
                    actions=format_actions([a for a in precedente["actions"] if not a["faite"]]))
        self.envoyer(200, page_formulaire(conn, valeurs, "/reunions"))

    def v_creer(self, conn, requete, formulaire):
        try:
            rid = save_reunion(conn, formulaire)
        except ValueError as exc:
            return self.envoyer(400, page_formulaire(conn, formulaire, "/reunions", str(exc)))
        self.rediriger(f"/reunions/{rid}")

    def v_modifier(self, conn, requete, formulaire, rid):
        r = get_reunion(conn, int(rid))
        if r is None:
            return self.introuvable()
        valeurs = dict(r, actions=format_actions(r["actions"]))
        self.envoyer(200, page_formulaire(conn, valeurs, f"/reunions/{rid}"))

    def v_enregistrer(self, conn, requete, formulaire, rid):
        try:
            save_reunion(conn, formulaire, int(rid))
        except KeyError:
            return self.introuvable()
        except ValueError as exc:
            return self.envoyer(400, page_formulaire(conn, formulaire, f"/reunions/{rid}", str(exc)))
        self.rediriger(f"/reunions/{rid}")

    def v_supprimer(self, conn, requete, formulaire, rid):
        with conn:
            conn.execute("DELETE FROM reunions WHERE id = ?", (int(rid),))
        self.rediriger("/")

    def v_basculer(self, conn, requete, formulaire, aid):
        with conn:
            conn.execute("UPDATE actions SET faite = 1 - faite WHERE id = ?", (int(aid),))
        retour = formulaire.get("retour") or "/"
        # N'accepter que des chemins locaux pour éviter les redirections ouvertes.
        if not retour.startswith("/") or retour.startswith("//"):
            retour = "/"
        self.rediriger(retour)

    def v_actions(self, conn, requete, formulaire):
        self.envoyer(200, page_actions(conn, (requete.get("responsable") or [""])[0] or None))

    def v_ics(self, conn, requete, formulaire):
        base = os.environ.get("REUNIONS_URL", f"http://{self.headers.get('Host', 'localhost')}")
        self.envoyer(200, calendrier_ics(conn, base.rstrip("/")), "text/calendar; charset=utf-8",
                     {"Content-Disposition": 'inline; filename="memo-reunions.ics"'})

    def v_recherche(self, conn, requete, formulaire):
        self.envoyer(200, page_recherche(conn, (requete.get("q") or [""])[0].strip()))


ROUTES = [
    ("GET", r"/", Handler.v_accueil),
    ("GET", r"/actions", Handler.v_actions),
    ("GET", r"/actions\.ics", Handler.v_ics),
    ("GET", r"/recherche", Handler.v_recherche),
    ("GET", r"/projets/(\d+)", Handler.v_projet),
    ("GET", r"/projets/(\d+)\.md", Handler.v_projet_md),
    ("GET", r"/reunions/nouvelle", Handler.v_nouvelle),
    ("GET", r"/reunions/(\d+)", Handler.v_reunion),
    ("GET", r"/reunions/(\d+)\.md", Handler.v_reunion_md),
    ("GET", r"/reunions/(\d+)/modifier", Handler.v_modifier),
    ("POST", r"/reunions", Handler.v_creer),
    ("POST", r"/reunions/(\d+)", Handler.v_enregistrer),
    ("POST", r"/reunions/(\d+)/supprimer", Handler.v_supprimer),
    ("POST", r"/actions/(\d+)/basculer", Handler.v_basculer),
]


def creer_serveur(host, port, db_path=None):
    handler = type("HandlerConfigure", (Handler,), {"db_path": db_path or DB_PATH})
    connect(db_path or DB_PATH).close()
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description="Mémo Réunions — serveur web")
    parser.add_argument("--host", default=os.environ.get("REUNIONS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("REUNIONS_PORT", "8080")))
    parser.add_argument("--db", default=DB_PATH, help="chemin de la base SQLite")
    args = parser.parse_args()
    serveur = creer_serveur(args.host, args.port, args.db)
    print(f"Mémo Réunions disponible sur http://{args.host}:{args.port} (base : {args.db})")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
