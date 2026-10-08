from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Achat, Emprunt, Exemplaire, Livre, Utilisateur

PY, WEB, BDD, MATHS, SVT = ("Introduction à Python", "Développement Web moderne", "Bases de données relationnelles",
                            "Mathématiques pour tous", "Sciences de la vie")
MDP = "Bibliotheque2026!"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def user(self, username):
        return Utilisateur.objects.get(username=username)

    def login(self, username):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=MDP))

    def livre(self, titre):
        return Livre.objects.get(titre=titre)


class ModeleTests(BaseTest):
    def test_modele_reduit_a_six_entites(self):
        noms = {m.__name__ for m in apps.get_app_config("bibliotheque").get_models()}
        self.assertEqual(noms, {"Utilisateur", "Livre", "Exemplaire", "Emprunt", "Achat", "LigneAchat"})

    def test_roles_et_sanction_dans_emprunt(self):
        self.assertEqual(self.user("admin").role, "administrateur")
        self.assertEqual(self.user("bibliothecaire").role, "bibliothecaire")
        self.assertTrue(self.user("etudiant").numero_carte.startswith("ADH-"))
        self.assertIsNone(self.user("bibliothecaire").numero_carte)
        self.assertEqual(str(self.user("etudiant")), "Vianney Ngueteu")


class SiteAdherentTests(BaseTest):
    def test_catalogue_public_sans_connexion(self):
        self.assertContains(self.client.get(reverse("catalogue")), PY)
        self.assertEqual(self.client.get(reverse("accueil")).status_code, 200)
        self.assertEqual(self.client.get(reverse("livre_detail", args=[self.livre(BDD).pk])).status_code, 200)

    def test_recherche_et_filtres(self):
        r = self.client.get(reverse("catalogue"), {"q": "python"})
        self.assertContains(r, PY)
        self.assertNotContains(r, SVT)
        r = self.client.get(reverse("catalogue"), {"cat": "Science"})
        self.assertContains(r, MATHS)
        self.assertNotContains(r, PY)

    def test_pages_adherent_exigent_connexion(self):
        for nom in ("mon_compte", "mes_commandes", "commander"):
            self.assertEqual(self.client.get(reverse(nom)).status_code, 302)

    def test_adherent_ne_peut_pas_acceder_a_la_gestion(self):
        self.login("etudiant")
        for nom in ("tableau_bord", "emprunts", "sanctions", "adherents", "livres_gestion", "commandes", "achats"):
            r = self.client.get(reverse(nom))
            self.assertEqual((r.status_code, r.url), (302, reverse("accueil")), nom)

    def test_inscription_avec_validation(self):
        r = self.client.post(reverse("inscription"), {
            "prenom": "Jean", "nom": "Test", "telephone": "699", "username": "jtest",
            "password1": "Xk93!pqLmz", "password2": "Xk93!pqLmz"})
        self.assertRedirects(r, reverse("mon_compte"))
        a = self.user("jtest")
        self.assertEqual((a.role, a.statut), ("adherent", "en_attente"))
        self.assertTrue(a.numero_carte.startswith("ADH-"))
        with self.assertRaisesMessage(ValueError, "pas encore été validée"):
            services.verifier_adherent(a)

    def test_inscription_mot_de_passe_different(self):
        r = self.client.post(reverse("inscription"), {
            "prenom": "A", "nom": "B", "telephone": "6", "username": "zz",
            "password1": "Xk93!pqLmz", "password2": "autre"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Utilisateur.objects.filter(username="zz").exists())

    def test_login_redirige_selon_role(self):
        r = self.client.post(reverse("login"), {"username": "etudiant", "password": MDP}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("mon_compte"))
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "bibliothecaire", "password": MDP}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], reverse("tableau_bord"))


class CommandeTests(BaseTest):
    def commander(self, mode="retrait", **extra):
        data = {"mode": mode, "telephone": "690", "adresse": ""}
        data.update(extra)
        return self.client.post(reverse("commander"), data)

    def ajouter(self, livre, type_="emprunt", qte=1):
        return self.client.post(reverse("panier_action", args=["ajouter"]), {"livre": livre.pk, "type": type_, "quantite": qte})

    def demandes(self, username="etudiant"):
        return Emprunt.objects.filter(adherent=self.user(username), statut="demande")

    def test_panier_anonyme_puis_demande_d_emprunt(self):
        l = self.livre(BDD)
        self.ajouter(l)  # sans être connecté
        self.assertEqual(self.client.session["panier"], {f"{l.pk}:emprunt": 1})
        self.client.login(username="etudiant", password=MDP)
        self.assertRedirects(self.commander(), reverse("mes_commandes"))
        e = self.demandes().get()
        self.assertEqual(e.exemplaire.livre, l)
        self.assertEqual(e.exemplaire.statut, "reserve")  # mis de côté
        self.assertIsNone(e.date_retour_prevue)
        self.assertEqual(self.client.session.get("panier"), {})

    def test_cycle_complet_emprunt_en_livraison(self):
        self.login("etudiant")
        self.ajouter(self.livre(BDD))
        self.commander("livraison", adresse="Akwa, Douala")
        e = self.demandes().get()
        self.assertEqual(e.adresse_livraison, "Akwa, Douala")
        self.login("bibliothecaire")
        self.client.post(reverse("emprunt_action", args=[e.pk, "preparer"]))
        e.refresh_from_db()
        self.assertEqual(e.statut, "prete")
        self.client.post(reverse("emprunt_action", args=[e.pk, "remettre"]))
        e.refresh_from_db()
        self.assertEqual(e.statut, "en_cours")
        self.assertEqual((e.date_retour_prevue - timezone.localdate()).days, 14)
        self.assertEqual(e.personnel, self.user("bibliothecaire"))
        self.assertEqual(e.exemplaire.statut, "emprunte")

    def test_livraison_sans_adresse_refusee(self):
        self.login("etudiant")
        self.ajouter(self.livre(BDD))
        self.commander("livraison")
        self.assertEqual(self.demandes().count(), 0)

    def test_annulation_remet_l_exemplaire_en_rayon(self):
        self.login("etudiant")
        self.ajouter(self.livre(BDD))
        self.commander()
        e = self.demandes().get()
        self.client.post(reverse("demande_annuler", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual((e.statut, e.exemplaire.statut), ("annule", "disponible"))

    def test_emprunt_refuse_si_aucun_exemplaire_disponible(self):
        l = self.livre(BDD)
        Exemplaire.objects.filter(livre=l).update(statut="emprunte")
        self.login("etudiant")
        self.ajouter(l)
        self.assertEqual(self.client.session.get("panier", {}), {})

    def test_limite_emprunts_simultanes(self):
        livres = [self.livre(t) for t in (BDD, MATHS, SVT)]  # l'étudiant a déjà 1 prêt ; limite = 3
        self.login("etudiant")
        for l in livres:
            self.ajouter(l)
        self.commander()
        self.assertEqual(self.demandes().count(), 0)  # 1 + 3 > 3
        self.client.post(reverse("panier_action", args=["retirer"]), {"cle": f"{livres[2].pk}:emprunt"})
        self.commander()
        self.assertEqual(self.demandes().count(), 2)  # 1 + 2 = 3
        self.ajouter(self.livre("Réussir son projet académique"))
        self.commander()
        self.assertEqual(self.demandes().count(), 2)

    def test_adherent_avec_sanction_impayee_ne_peut_pas_emprunter(self):
        self.login("lecteur2")
        self.ajouter(self.livre(BDD))
        self.commander()
        self.assertEqual(self.demandes("lecteur2").count(), 0)

    def test_achat_invite_sans_creation_de_compte(self):
        l = self.livre(MATHS)
        stock = l.stock_vente
        r = self.ajouter(l, "achat", 2)
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse("commander"), {
            "prenom": "Client", "nom": "Passage", "email": "client@example.com",
            "telephone": "690000000", "mode": "retrait", "adresse": ""
        })
        self.assertEqual(r.status_code, 302)
        a = Achat.objects.order_by("-id").first()
        self.assertIsNone(a.adherent_id)
        self.assertEqual(a.client_nom_complet, "Client Passage")
        self.assertEqual(a.mode_reception, "retrait")
        l.refresh_from_db()
        self.assertEqual(l.stock_vente, stock - 2)
        self.assertTrue(a.reference.startswith("BF-"))

    def test_achat_en_ligne_stock_reserve_et_remise(self):
        l = self.livre(MATHS)
        stock = l.stock_vente
        self.login("etudiant")
        self.ajouter(l, "achat", 2)
        self.commander()
        l.refresh_from_db()
        self.assertEqual(l.stock_vente, stock - 2)
        a = Achat.objects.get(adherent=self.user("etudiant"))
        self.assertEqual((a.canal, a.statut, a.total), ("en_ligne", "en_attente", l.prix * 2))
        self.login("bibliothecaire")
        url = reverse("achat_detail", args=[a.pk])
        self.client.post(url, {"action": "preparer"})
        self.client.post(url, {"action": "remettre"})  # sans mode de paiement : refusé
        a.refresh_from_db()
        self.assertEqual(a.statut, "prete")
        self.client.post(url, {"action": "remettre", "mode_paiement": "especes"})
        a.refresh_from_db()
        self.assertEqual((a.statut, a.mode_paiement), ("terminee", "especes"))

    def test_commande_mixte_cree_demande_et_achat(self):
        self.login("etudiant")
        self.ajouter(self.livre(BDD))
        self.ajouter(self.livre(MATHS), "achat", 1)
        self.commander()
        self.assertEqual(self.demandes().count(), 1)
        self.assertEqual(Achat.objects.filter(adherent=self.user("etudiant")).count(), 1)


    def test_achat_invite_expire_et_restitue_le_stock(self):
        l = self.livre(MATHS)
        stock = l.stock_vente
        self.ajouter(l, "achat", 2)
        self.client.post(reverse("commander"), {"prenom":"Client", "nom":"Test", "email":"expire@example.com", "telephone":"690111111", "mode":"retrait", "adresse":""})
        a = Achat.objects.get(client_email="expire@example.com")
        self.assertEqual(a.date_limite_retrait, timezone.localdate() + timedelta(days=3))
        Achat.objects.filter(pk=a.pk).update(date_limite_retrait=timezone.localdate() - timedelta(days=1))
        services.maj_quotidienne()
        a.refresh_from_db(); l.refresh_from_db()
        self.assertEqual((a.statut, a.statut_paiement, l.stock_vente), ("annulee", "annule", stock))

    def test_personnel_peut_acheter_en_ligne(self):
        self.login("bibliothecaire")
        self.ajouter(self.livre(MATHS), "achat", 1)
        r = self.commander()
        self.assertRedirects(r, reverse("mes_commandes"))
        self.assertTrue(Achat.objects.filter(adherent=self.user("bibliothecaire")).exists())

    def test_annulation_achat_restaure_le_stock(self):
        l = self.livre(MATHS)
        stock = l.stock_vente
        self.login("etudiant")
        self.ajouter(l, "achat", 3)
        self.commander()
        self.client.post(reverse("achat_annuler", args=[Achat.objects.get().pk]))
        l.refresh_from_db()
        self.assertEqual(l.stock_vente, stock)

    def test_demande_prete_non_retiree_est_annulee(self):
        self.login("etudiant")
        self.ajouter(self.livre(BDD))
        self.commander()
        e = self.demandes().get()
        services.preparer_emprunt(e, self.user("bibliothecaire"))
        Emprunt.objects.filter(pk=e.pk).update(date_limite_retrait=timezone.localdate() - timedelta(days=1))
        services.maj_quotidienne()
        e.refresh_from_db()
        self.assertEqual((e.statut, e.exemplaire.statut), ("annule", "disponible"))


class SanctionsTests(BaseTest):
    def setUp(self):
        self.retard = Emprunt.objects.get(adherent=self.user("lecteur2"))

    def test_sanction_de_retard_automatique(self):
        self.assertEqual(self.retard.statut_paiement, "impayee")
        jours = self.retard.jours_retard
        self.assertEqual(self.retard.montant_sanction, Decimal(jours * 100))
        self.assertIn(f"{jours} jour", self.retard.motif_sanction)

    def test_sanction_grandit_chaque_jour_sans_doublon(self):
        Emprunt.objects.filter(pk=self.retard.pk).update(date_retour_prevue=timezone.localdate() - timedelta(days=12))
        services.maj_quotidienne()
        services.maj_quotidienne()
        self.retard.refresh_from_db()
        self.assertEqual(self.retard.montant_sanction, Decimal("1200"))

    def test_plafond_de_la_sanction(self):
        self.assertEqual(services.montant_amende(500), Decimal("5000"))

    def test_adherent_en_retard_ne_peut_pas_emprunter(self):
        with self.assertRaises(ValueError):
            services.verifier_adherent(self.user("lecteur2"))

    def test_retour_en_retard_suspend_et_garde_la_sanction(self):
        self.login("bibliothecaire")
        self.client.post(reverse("emprunt_action", args=[self.retard.pk, "retour"]), {"etat": "bon"})
        self.retard.refresh_from_db()
        a = self.user("lecteur2")
        self.assertEqual(self.retard.statut, "rendu")
        self.assertEqual((a.statut, a.suspendu_jusqu), ("suspendu", timezone.localdate() + timedelta(days=self.retard.jours_retard)))
        self.assertEqual(self.retard.exemplaire.statut, "disponible")
        self.assertEqual(services.montant_impaye(a), self.retard.montant_sanction)

    def test_fin_de_suspension_automatique(self):
        services.enregistrer_retour(self.retard)
        Utilisateur.objects.filter(username="lecteur2").update(suspendu_jusqu=timezone.localdate() - timedelta(days=1))
        services.maj_quotidienne()
        self.assertEqual(self.user("lecteur2").statut, "actif")

    def test_encaissement_debloque_l_adherent(self):
        services.enregistrer_retour(self.retard)
        Utilisateur.objects.filter(username="lecteur2").update(statut="actif", suspendu_jusqu=None)
        a = self.user("lecteur2")
        with self.assertRaisesMessage(ValueError, "sanctions impayées"):
            services.verifier_adherent(a)
        self.login("bibliothecaire")
        self.client.post(reverse("sanction_action", args=[self.retard.pk, "encaisser"]))
        self.retard.refresh_from_db()
        self.assertEqual(self.retard.statut_paiement, "payee")
        services.verifier_adherent(a)  # plus d'exception

    def test_on_ne_peut_pas_encaisser_un_retard_avant_le_retour(self):
        with self.assertRaisesMessage(ValueError, "retour du livre"):
            services.encaisser_sanction(self.retard)

    def test_seul_admin_annule_une_sanction(self):
        services.enregistrer_retour(self.retard)
        self.login("bibliothecaire")
        self.client.post(reverse("sanction_action", args=[self.retard.pk, "annuler"]))
        self.retard.refresh_from_db()
        self.assertEqual(self.retard.statut_paiement, "impayee")
        self.login("admin")
        self.client.post(reverse("sanction_action", args=[self.retard.pk, "annuler"]))
        self.retard.refresh_from_db()
        self.assertEqual(self.retard.statut_paiement, "annulee")

    def test_livre_perdu_sanction_prix_plus_frais(self):
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        services.declarer_perdu(e)
        e.refresh_from_db()
        self.assertEqual(e.montant_sanction, self.livre(WEB).prix + Decimal("500"))
        self.assertEqual((e.statut, e.exemplaire.statut), ("perdu", "perdu"))

    def test_retour_abime_avec_frais_de_degats(self):
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        services.enregistrer_retour(e, "abime", 1500)
        e.refresh_from_db()
        self.assertEqual((e.montant_sanction, e.statut_paiement), (Decimal("1500"), "impayee"))
        self.assertIn("abîmé", e.motif_sanction)
        self.assertEqual((e.exemplaire.etat_physique, e.exemplaire.statut), ("abime", "disponible"))

    def test_sanction_annulee_puis_perte_cree_une_nouvelle_sanction(self):
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        e.statut_paiement = "annulee"; e.montant_sanction = Decimal("900"); e.save(update_fields=["statut_paiement", "montant_sanction"])
        services.declarer_perdu(e)
        e.refresh_from_db()
        self.assertEqual(e.statut_paiement, "impayee")
        self.assertEqual(e.montant_sanction, self.livre(WEB).prix + Decimal("500"))

    def test_sanction_annulee_puis_retour_avec_degats_cree_une_sanction(self):
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        e.statut_paiement = "annulee"; e.save(update_fields=["statut_paiement"])
        services.enregistrer_retour(e, "abime", 3000)
        e.refresh_from_db()
        self.assertEqual((e.montant_sanction, e.statut_paiement), (Decimal("3000"), "impayee"))

    def test_retard_et_degats_se_cumulent(self):
        services.enregistrer_retour(self.retard, "abime", 500)
        self.retard.refresh_from_db()
        self.assertEqual(self.retard.montant_sanction, Decimal(self.retard.jours_retard * 100) + Decimal("500"))
        self.assertIn("Retard", self.retard.motif_sanction)
        self.assertIn("abîmé", self.retard.motif_sanction)


class PretsTests(BaseTest):
    def code(self, titre):
        return Exemplaire.objects.filter(livre__titre=titre, statut="disponible").first().code_barres

    def test_pret_comptoir_et_refus_exemplaire_indisponible(self):
        self.login("bibliothecaire")
        a, code = self.user("etudiant"), self.code(MATHS)
        self.client.post(reverse("emprunts"), {"carte": a.numero_carte, "code_barres": code})
        e = Emprunt.objects.get(exemplaire__code_barres=code)
        self.assertEqual((e.statut, e.personnel, e.exemplaire.statut), ("en_cours", self.user("bibliothecaire"), "emprunte"))
        self.client.post(reverse("emprunts"), {"carte": self.user("lecteur2").numero_carte, "code_barres": code})
        self.assertEqual(Emprunt.objects.filter(exemplaire__code_barres=code).count(), 1)

    def test_exemplaire_mis_de_cote_retire_par_son_demandeur(self):
        e = services.passer_commande(self.user("etudiant"), [(self.livre(BDD).pk, "emprunt", 1)], "retrait")[0][0]
        code = e.exemplaire.code_barres
        self.login("bibliothecaire")
        self.client.post(reverse("emprunts"), {"carte": self.user("lecteur2").numero_carte, "code_barres": code})
        e.refresh_from_db()
        self.assertEqual(e.statut, "demande")  # lecteur2 ne peut pas le prendre
        self.client.post(reverse("emprunts"), {"carte": self.user("etudiant").numero_carte, "code_barres": code})
        e.refresh_from_db()
        self.assertEqual(e.statut, "en_cours")

    def test_prolongation_et_limite(self):
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        avant = e.date_retour_prevue
        services.prolonger(e)
        self.assertEqual(e.date_retour_prevue, avant + timedelta(days=7))
        with self.assertRaisesMessage(ValueError, "Limite"):
            services.prolonger(e)

    def test_prolongation_refusee_en_retard(self):
        with self.assertRaisesMessage(ValueError, "en retard"):
            services.prolonger(Emprunt.objects.get(adherent=self.user("lecteur2")))

    def test_prolongation_par_l_adherent(self):
        self.login("etudiant")
        e = Emprunt.objects.get(adherent=self.user("etudiant"))
        self.client.post(reverse("emprunt_prolonger", args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.nb_prolongations, 1)

    def test_adhesion_expiree_refusee(self):
        a = self.user("etudiant")
        a.date_expiration = timezone.localdate() - timedelta(days=1)
        a.save()
        with self.assertRaisesMessage(ValueError, "expiré"):
            services.verifier_adherent(a)


class GestionTests(BaseTest):
    def test_toutes_les_pages_de_gestion_s_affichent(self):
        self.login("admin")
        e, l = self.user("etudiant"), self.livre(PY)
        for nom, args in [("tableau_bord", []), ("commandes", []), ("emprunts", []), ("sanctions", []),
                          ("adherents", []), ("adherent_nouveau", []), ("adherent_detail", [e.pk]),
                          ("adherent_modifier", [e.pk]), ("livres_gestion", []), ("livre_nouveau", []),
                          ("livre_modifier", [l.pk]), ("livre_exemplaires", [l.pk]), ("achats", []), ("achat_nouveau", [])]:
            self.assertEqual(self.client.get(reverse(nom, args=args)).status_code, 200, nom)
        for f in ("en_cours", "retards", "tous"):
            self.assertEqual(self.client.get(reverse("emprunts"), {"vue": f}).status_code, 200)
        for f in ("a_valider", "expires", "suspendus", "dettes"):
            self.assertEqual(self.client.get(reverse("adherents"), {"filtre": f}).status_code, 200)
        for f in ("impayee", "payee", "annulee", "toutes"):
            self.assertEqual(self.client.get(reverse("sanctions"), {"statut": f}).status_code, 200)
        for f in ("actives", "terminee", "annulee", "toutes"):
            self.assertEqual(self.client.get(reverse("achats"), {"statut": f}).status_code, 200)

    def test_pages_adherent_s_affichent(self):
        self.login("lecteur2")
        for nom in ("mon_compte", "mes_commandes", "panier"):
            self.assertEqual(self.client.get(reverse(nom)).status_code, 200, nom)
        r = self.client.get(reverse("mon_compte"))
        self.assertContains(r, f"En retard de {Emprunt.objects.get(adherent=self.user('lecteur2')).jours_retard} j")
        self.assertContains(r, f"{Emprunt.objects.get(adherent=self.user('lecteur2')).montant_sanction:.0f} FCFA")

    def test_creation_adherent_au_comptoir(self):
        self.login("bibliothecaire")
        r = self.client.post(reverse("adherent_nouveau"), {
            "prenom": "Ali", "nom": "Bello", "telephone": "677", "username": "abello", "mot_de_passe": "Xk93!pqLmz"})
        a = self.user("abello")
        self.assertRedirects(r, reverse("adherent_detail", args=[a.pk]))
        self.assertEqual((a.role, a.statut), ("adherent", "actif"))
        self.assertTrue(a.numero_carte and a.date_expiration)

    def test_validation_et_renouvellement_adhesion(self):
        a = self.user("etudiant")
        Utilisateur.objects.filter(pk=a.pk).update(statut="en_attente", date_expiration=None)
        self.login("bibliothecaire")
        self.client.post(reverse("adherent_detail", args=[a.pk]), {"action": "valider"})
        a.refresh_from_db()
        self.assertEqual(a.statut, "actif")
        self.assertEqual((a.date_expiration - timezone.localdate()).days, 365)
        self.client.post(reverse("adherent_detail", args=[a.pk]), {"action": "renouveler"})
        a.refresh_from_db()
        self.assertEqual((a.date_expiration - timezone.localdate()).days, 730)

    def test_creation_livre_avec_exemplaires_et_rayon(self):
        self.login("bibliothecaire")
        self.client.post(reverse("livre_nouveau"), {
            "titre": "Nouveau titre", "auteur": "Nouvel Auteur", "categorie": "Poésie", "langue": "Français",
            "prix": "5000", "stock_vente": "2", "nb_exemplaires": "3", "etagere": "Étagère Z", "code_rayon": "POE-1"})
        l = Livre.objects.get(titre="Nouveau titre")
        self.assertEqual(l.exemplaires.count(), 3)
        ex = l.exemplaires.first()
        self.assertEqual(ex.emplacement, "Étagère Z / POE-1")
        url = reverse("livre_exemplaires", args=[l.pk])
        self.client.post(url, {"action": "modifier", "ex": ex.pk, "etat_physique": "abime", "etagere": "Étagère Y",
                               "code_rayon": "POE-2", "statut": "perdu"})
        ex.refresh_from_db()
        self.assertEqual((ex.statut, ex.etat_physique, ex.etagere), ("perdu", "abime", "Étagère Y"))
        self.client.post(url, {"action": "modifier", "ex": ex.pk, "etat_physique": "bon", "statut": "disponible"})
        ex.refresh_from_db()
        self.assertEqual(ex.statut, "disponible")
        # un exemplaire emprunté ne peut pas être "retiré" à la main
        pris = Exemplaire.objects.filter(statut="emprunte").first()
        self.client.post(reverse("livre_exemplaires", args=[pris.livre.pk]), {
            "action": "modifier", "ex": pris.pk, "etat_physique": "bon", "statut": "retire"})
        pris.refresh_from_db()
        self.assertEqual(pris.statut, "emprunte")

    def test_achat_au_comptoir(self):
        l = self.livre(MATHS)
        stock = l.stock_vente
        self.login("bibliothecaire")
        r = self.client.post(reverse("achat_nouveau"), {
            "carte": self.user("etudiant").numero_carte, "livre": [l.pk, "", "", ""], "quantite": ["2", "1", "1", "1"],
            "mode_paiement": "mobile", "reference_paiement": "MM-TEST-001"})
        a = Achat.objects.get(canal="comptoir")
        self.assertRedirects(r, reverse("achat_detail", args=[a.pk]))
        self.assertEqual((a.statut, a.mode_paiement, a.total), ("terminee", "mobile", l.prix * 2))
        l.refresh_from_db()
        self.assertEqual(l.stock_vente, stock - 2)

    def test_achat_au_comptoir_stock_insuffisant(self):
        l = self.livre(MATHS)
        self.login("bibliothecaire")
        self.client.post(reverse("achat_nouveau"), {
            "carte": self.user("etudiant").numero_carte, "livre": [l.pk], "quantite": [str(l.stock_vente + 1)],
            "mode_paiement": "especes"})
        self.assertEqual(Achat.objects.count(), 0)
