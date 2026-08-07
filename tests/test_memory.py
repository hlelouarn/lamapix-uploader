"""Mémoire persistante (§5) — c'est elle qui garantit « jamais deux fois »."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from lamapix_uploader.memory import MemoireEvenement


def _memoire(tmp_path):
    return MemoireEvenement.charger(tmp_path / "_memoire.json")


class TestAllerRetour:
    def test_relecture_apres_sauvegarde(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer(r"C:\src\a.jpg", "NOM/a.jpg", 1234)
        memoire.marquer_envoyee(r"C:\src\a.jpg")
        memoire.sauver()

        relue = _memoire(tmp_path)
        assert relue.entrees[r"C:\src\a.jpg"].rel == "NOM/a.jpg"
        assert relue.entrees[r"C:\src\a.jpg"].envoyee is True
        assert relue.rels_utilises["NOM/a.jpg"] == r"C:\src\a.jpg"

    def test_fichier_absent_donne_memoire_vide(self, tmp_path):
        assert _memoire(tmp_path).entrees == {}

    def test_json_corrompu_ne_fait_pas_planter(self, tmp_path):
        """Au pire on renvoie des photos ; jamais on n'empêche l'outil de tourner."""
        (tmp_path / "_memoire.json").write_text("{ceci n'est pas du JSON", encoding="utf-8")
        assert _memoire(tmp_path).entrees == {}

    def test_entree_incomplete_est_ignoree_sans_perdre_les_autres(self, tmp_path):
        (tmp_path / "_memoire.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "files": {
                        "C:/bonne.jpg": {"rel": "NOM/a.jpg", "size": 10, "sent": True},
                        "C:/cassee.jpg": {"size": 10},
                    },
                }
            ),
            encoding="utf-8",
        )
        memoire = _memoire(tmp_path)
        assert list(memoire.entrees) == ["C:/bonne.jpg"]

    def test_ecriture_atomique_ne_laisse_pas_de_temporaire(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.sauver()
        assert not (tmp_path / "_memoire.json.tmp").exists()

    def test_accents_conserves(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer(r"C:\src\é.jpg", "CRITÉRIUM_CHEVAL/é.jpg", 1)
        memoire.sauver()
        assert _memoire(tmp_path).entrees[r"C:\src\é.jpg"].rel == "CRITÉRIUM_CHEVAL/é.jpg"


class TestNouveaute:
    def test_photo_inconnue_est_nouvelle(self, tmp_path):
        assert _memoire(tmp_path).est_nouvelle("C:/a.jpg", 10) is True

    def test_photo_connue_de_meme_taille_ne_lest_pas(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 10)
        assert memoire.est_nouvelle("C:/a.jpg", 10) is False

    def test_photo_retouchee_redevient_nouvelle(self, tmp_path):
        """Taille changée = retouche : on renvoie sous le même nom, qui écrase."""
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 10)
        memoire.marquer_envoyee("C:/a.jpg")
        assert memoire.est_nouvelle("C:/a.jpg", 4096) is True


class TestBoutons:
    def test_initialiser_marque_tout_sans_rien_envoyer(self, tmp_path):
        memoire = _memoire(tmp_path)
        nombre = memoire.marquer_tout_envoye(
            [("C:/a.jpg", "NOM/a.jpg", 1), ("C:/b.jpg", "NOM/b.jpg", 2)]
        )
        assert nombre == 2
        assert memoire.en_attente() == []
        assert memoire.nombre_envoyees == 2

    def test_initialiser_avale_aussi_ce_qui_attendait(self, tmp_path):
        """Une photo repérée mais pas encore partie doit être avalée : sinon le
        bouton ne marquerait plus rien dès que le premier scan est passé."""
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        nombre = memoire.marquer_tout_envoye([("C:/a.jpg", "NOM/a.jpg", 1)])
        assert nombre == 1
        assert memoire.entrees["C:/a.jpg"].envoyee is True
        assert memoire.entrees["C:/a.jpg"].initialisee is True

    def test_initialiser_ne_recrit_pas_lhistoire_des_envois_reels(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.marquer_envoyee("C:/a.jpg")  # envoi RÉEL
        nombre = memoire.marquer_tout_envoye([("C:/a.jpg", "NOM/autre.jpg", 1)])
        assert nombre == 0
        assert memoire.entrees["C:/a.jpg"].rel == "NOM/a.jpg"
        assert memoire.entrees["C:/a.jpg"].initialisee is False

    def test_annuler_ne_libere_que_les_declarees(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/reelle.jpg", "NOM/reelle.jpg", 1)
        memoire.marquer_envoyee("C:/reelle.jpg")
        memoire.marquer_tout_envoye([("C:/declaree.jpg", "NOM/declaree.jpg", 1)])

        assert memoire.nombre_initialisees == 1
        assert memoire.annuler_initialisation() == 1
        assert memoire.en_attente() == ["C:/declaree.jpg"]
        assert memoire.entrees["C:/reelle.jpg"].envoyee is True
        assert memoire.nombre_initialisees == 0

    def test_annulation_sans_rien_a_annuler(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.marquer_envoyee("C:/a.jpg")
        assert memoire.annuler_initialisation() == 0

    def test_le_drapeau_est_persiste(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.marquer_tout_envoye([("C:/a.jpg", "NOM/a.jpg", 1)])
        assert _memoire(tmp_path).nombre_initialisees == 1

    def test_reinitialiser_vide_tout_et_supprime_le_fichier(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.sauver()
        memoire.effacer()
        assert memoire.entrees == {}
        assert memoire.rels_utilises == {}
        assert not (tmp_path / "_memoire.json").exists()


class TestFile:
    def test_en_attente_triee_par_chemin_distant(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/z.jpg", "ZOE_CHEVAL/z.jpg", 1)
        memoire.enregistrer("C:/a.jpg", "ALICE_CHEVAL/a.jpg", 1)
        memoire.enregistrer("C:/m.jpg", "ALICE_CHEVAL/b.jpg", 1)
        assert memoire.en_attente() == ["C:/a.jpg", "C:/m.jpg", "C:/z.jpg"]

    def test_les_envoyees_sortent_de_la_file(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.enregistrer("C:/b.jpg", "NOM/b.jpg", 1)
        memoire.marquer_envoyee("C:/a.jpg")
        assert memoire.en_attente() == ["C:/b.jpg"]
        assert (memoire.nombre_envoyees, memoire.nombre_en_attente) == (1, 1)

    def test_sauvegarde_periodique_apres_le_seuil(self, tmp_path):
        """Un crash à la 19e photo ne doit coûter que 19 doublons, pas l'événement."""
        memoire = _memoire(tmp_path)
        for index in range(3):
            memoire.enregistrer(f"C:/{index}.jpg", f"NOM/{index}.jpg", 1)
            memoire.marquer_envoyee(f"C:/{index}.jpg")
        assert memoire.sauver_si_necessaire(seuil=5) is False
        assert memoire.sauver_si_necessaire(seuil=3) is True
        assert (tmp_path / "_memoire.json").exists()

    def test_horodatage_envoi_est_iso_avec_fuseau(self, tmp_path):
        memoire = _memoire(tmp_path)
        memoire.enregistrer("C:/a.jpg", "NOM/a.jpg", 1)
        memoire.marquer_envoyee("C:/a.jpg", datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc))
        assert memoire.entrees["C:/a.jpg"].envoyee_le == "2026-08-07T10:00:00+00:00"
