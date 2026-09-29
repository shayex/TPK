import base64
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["REUNIONS_LOG"] = "0"

import app  # noqa: E402
import rappel  # noqa: E402


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TestDonnees(unittest.TestCase):
    def setUp(self):
        self.conn = app.connect(":memory:")

    def test_parse_actions(self):
        actions = app.parse_actions(
            "- [ ] Commander les switchs @moi !2026-10-15\n"
            "\n"
            "[x] Valider le plan d'adressage @j.dupont\n"
            "Mettre à jour la doc !01/11/2026\n")
        self.assertEqual(actions, [
            {"texte": "Commander les switchs", "responsable": "moi", "echeance": "2026-10-15", "faite": 0},
            {"texte": "Valider le plan d'adressage", "responsable": "j.dupont", "echeance": None, "faite": 1},
            {"texte": "Mettre à jour la doc", "responsable": "", "echeance": "2026-11-01", "faite": 0},
        ])

    def test_aller_retour_actions(self):
        texte = "- [ ] Tâche A @bob !2026-10-15\n- [x] Tâche B"
        self.assertEqual(app.format_actions(app.parse_actions(texte)), texte)

    def test_date_invalide(self):
        with self.assertRaises(ValueError):
            app.parse_actions("Tâche !2026-13-45")

    def test_creation_modification_recherche(self):
        rid = app.save_reunion(self.conn, {
            "projet": "Migration AD", "titre": "Kick-off", "date": "2026-09-01",
            "participants": "DSI, prestataire", "decisions": "Bascule en novembre",
            "actions": "Inventaire des GPO @moi !2026-09-15", "prochaine": "2026-09-20"})
        r = app.get_reunion(self.conn, rid)
        self.assertEqual(r["projet"], "Migration AD")
        self.assertEqual(len(r["actions"]), 1)

        # Le nom du projet n'est pas sensible à la casse.
        app.save_reunion(self.conn, {"projet": "migration ad", "titre": "Point 2", "date": "2026-09-20"})
        self.assertEqual(len(app.projets(self.conn)), 1)

        app.save_reunion(self.conn, dict(r, actions="[x] Inventaire des GPO @moi"), rid)
        self.assertEqual(app.actions_ouvertes(self.conn), [])

        self.assertEqual([x["id"] for x in app.rechercher(self.conn, "novembre bascule")], [rid])
        self.assertEqual(app.rechercher(self.conn, "GPO kick")[0]["id"], rid)
        self.assertEqual(app.rechercher(self.conn, "100%"), [])

    def test_champs_obligatoires(self):
        with self.assertRaises(ValueError):
            app.save_reunion(self.conn, {"projet": "", "titre": "x", "date": "2026-01-01"})
        with self.assertRaises(ValueError):
            app.save_reunion(self.conn, {"projet": "P", "titre": "", "date": "2026-01-01"})

    def test_ics(self):
        demain = (date.today() + timedelta(days=1)).isoformat()
        app.save_reunion(self.conn, {
            "projet": "Wi-Fi usine", "titre": "Audit radio, bâtiment B", "date": date.today().isoformat(),
            "actions": f"Poser les bornes {'x' * 80} @moi !{demain}", "prochaine": demain})
        ics = app.calendrier_ics(self.conn, "http://memo")
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 2)
        self.assertIn("Audit radio\\, bâtiment B", ics)
        for ligne in ics.split("\r\n"):
            self.assertLessEqual(len(ligne.encode("utf-8")), 75)

    def test_rappel(self):
        self.assertIsNone(rappel.construire_rappel(self.conn))
        hier = (date.today() - timedelta(days=1)).isoformat()
        app.save_reunion(self.conn, {
            "projet": "Sauvegardes", "titre": "Revue PRA", "date": hier, "decisions": "- Tester la restauration",
            "actions": f"Test de restauration @moi !{hier}"})
        sujet, texte = rappel.construire_rappel(self.conn, base_url="http://memo")
        self.assertIn("1 action(s) en retard", sujet)
        self.assertIn("Test de restauration", texte)
        self.assertIn("décision : Tester la restauration", texte)
        self.assertIn("http://memo/reunions/", texte)


class TestServeur(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.serveur = app.creer_serveur("127.0.0.1", 0, os.path.join(self.dossier.name, "t.db"))
        self.base = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.ouvreur = urllib.request.build_opener(_SansRedirection)

    def tearDown(self):
        self.serveur.shutdown()
        self.serveur.server_close()
        self.dossier.cleanup()
        for cle in ("REUNIONS_UTILISATEUR", "REUNIONS_MOT_DE_PASSE", "REUNIONS_JETON_ICS"):
            os.environ.pop(cle, None)

    def requete(self, chemin, donnees=None, entetes=None):
        corps = urllib.parse.urlencode(donnees).encode() if donnees is not None else None
        req = urllib.request.Request(self.base + chemin, data=corps, headers=entetes or {})
        try:
            with self.ouvreur.open(req) as rep:
                return rep.status, rep.headers, rep.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read().decode("utf-8")

    def test_parcours_complet(self):
        statut, _, corps = self.requete("/")
        self.assertEqual(statut, 200)
        self.assertIn("Aucun projet", corps)

        statut, entetes, _ = self.requete("/reunions", {
            "projet": "Firewall <script>", "titre": "Choix du pare-feu", "date": "2026-09-28",
            "actions": f"Demander les devis @moi !{date.today().isoformat()}", "prochaine": "2026-10-05"})
        self.assertEqual(statut, 303)
        chemin = entetes["Location"]

        statut, _, corps = self.requete(chemin)
        self.assertEqual(statut, 200)
        self.assertIn("Firewall &lt;script&gt;", corps)
        self.assertNotIn("<script>", corps)

        statut, _, corps = self.requete("/")
        self.assertIn("Demander les devis", corps)

        statut, _, corps = self.requete(chemin + "/modifier")
        self.assertIn("- [ ] Demander les devis @moi", corps)

        statut, _, corps = self.requete("/reunions/nouvelle?suite=" + chemin.rsplit("/", 1)[1])
        self.assertIn('value="2026-10-05"', corps)
        self.assertIn("Demander les devis", corps)

        statut, _, corps = self.requete("/recherche?q=devis")
        self.assertIn("Choix du pare-feu", corps)

        statut, entetes, corps = self.requete("/actions.ics")
        self.assertEqual(statut, 200)
        self.assertIn("text/calendar", entetes["Content-Type"])

        statut, _, corps = self.requete(chemin + ".md")
        self.assertIn("## 2026-09-28 — Choix du pare-feu", corps)

        statut, entetes, _ = self.requete("/actions/1/basculer", {"retour": "//exemple.com"})
        self.assertEqual(entetes["Location"], "/")
        statut, _, corps = self.requete("/actions")
        self.assertIn("Aucune action ouverte", corps)

        statut, _, _ = self.requete(chemin + "/supprimer", {})
        self.assertEqual(statut, 303)
        statut, _, _ = self.requete(chemin)
        self.assertEqual(statut, 404)

    def test_formulaire_invalide(self):
        statut, _, corps = self.requete("/reunions", {"projet": "P", "titre": "T", "date": "pas une date"})
        self.assertEqual(statut, 400)
        self.assertIn("Date invalide", corps)

    def test_autre_origine_refusee(self):
        statut, _, _ = self.requete("/reunions", {"projet": "P", "titre": "T", "date": "2026-01-01"},
                                    {"Origin": "http://malveillant.example"})
        self.assertEqual(statut, 403)

    def test_authentification(self):
        os.environ.update(REUNIONS_UTILISATEUR="admin", REUNIONS_MOT_DE_PASSE="secret",
                          REUNIONS_JETON_ICS="jeton123")
        self.assertEqual(self.requete("/")[0], 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
        self.assertEqual(self.requete("/", entetes=auth)[0], 200)
        self.assertEqual(self.requete("/actions.ics")[0], 401)
        self.assertEqual(self.requete("/actions.ics?jeton=jeton123")[0], 200)


if __name__ == "__main__":
    unittest.main()
