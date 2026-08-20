"""Mise à jour : comparaison de versions et lecture d'une release GitHub.

Rien ici ne touche le réseau — la réponse de GitHub est fournie en dur.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
import json

import pytest

from lamapix_uploader import updater


class TestComparaison:
    @pytest.mark.parametrize(
        "publiee, actuelle, plus_recente",
        [
            ("1.1.0", "1.0.0", True),
            ("1.0.1", "1.0.0", True),
            ("2.0.0", "1.9.9", True),
            ("1.0.0", "1.0.0", False),
            ("1.0.0", "1.1.0", False),   # le pire cas : ne JAMAIS rétrograder
            ("1.0", "1.0.0", False),     # « 1.0 » et « 1.0.0 » sont la même chose
            ("1.10.0", "1.9.0", True),   # comparaison numérique, pas alphabétique
        ],
    )
    def test_ordre(self, publiee, actuelle, plus_recente, monkeypatch):
        monkeypatch.setattr(updater, "VERSION", actuelle)
        maj = updater.MiseAJour(
            version=publiee, url_paquet="http://x/y.zip", nom_paquet="y.zip", notes=""
        )
        assert maj.plus_recente is plus_recente


def _reponse(charge: dict):
    """Simule urlopen : un objet-contexte qui rend le JSON encodé."""

    class Fausse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    return lambda *_a, **_k: Fausse(json.dumps(charge).encode("utf-8"))


class TestLectureRelease:
    def test_release_normale(self, monkeypatch):
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse(
                {
                    "tag_name": "v1.3.0",
                    "body": "Corrige le bouton Quitter",
                    "assets": [
                        {"name": "notes.txt", "browser_download_url": "http://x/n.txt"},
                        {
                            "name": "LamapixUploader-v1.3.0.zip",
                            "browser_download_url": "http://x/LamapixUploader.zip",
                        },
                    ],
                }
            ),
        )
        trouvee = updater.chercher("compte/depot")
        assert trouvee is not None
        assert trouvee.version == "1.3.0"          # le « v » du tag est retiré
        assert trouvee.est_archive is True
        assert "Quitter" in trouvee.notes

    def test_le_zip_prime_sur_lexe(self, monkeypatch):
        """Si une release porte les deux, on prend le dossier (moins de faux
        positifs antivirus que l'exe autoextractible)."""
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse(
                {
                    "tag_name": "v2.0.0",
                    "assets": [
                        {"name": "LamapixUploader.exe", "browser_download_url": "http://x/a.exe"},
                        {"name": "LamapixUploader.zip", "browser_download_url": "http://x/a.zip"},
                    ],
                }
            ),
        )
        trouvee = updater.chercher("compte/depot")
        assert trouvee is not None and trouvee.est_archive is True

    def test_une_release_en_exe_reste_installable(self, monkeypatch):
        """Un poste peut être en retard : les anciennes releases doivent marcher."""
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse(
                {
                    "tag_name": "v1.2.0",
                    "assets": [
                        {
                            "name": "LamapixUploader.exe",
                            "browser_download_url": "http://x/a.exe",
                        }
                    ],
                }
            ),
        )
        trouvee = updater.chercher("compte/depot")
        assert trouvee is not None and trouvee.est_archive is False

    def test_release_sans_binaire_est_ignoree(self, monkeypatch):
        """Rien d'installable : autant considérer qu'il n'y a pas de mise à jour."""
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse({"tag_name": "v9.9.9", "assets": [{"name": "NOTES.md"}]}),
        )
        assert updater.chercher("compte/depot") is None

    def test_reponse_sans_tag(self, monkeypatch):
        monkeypatch.setattr(updater.urllib.request, "urlopen", _reponse({"assets": []}))
        assert updater.chercher("compte/depot") is None

    def test_reseau_coupe_est_signale_comme_tel(self, monkeypatch):
        """Distinguer « injoignable » de « rien à installer » : sans ça, un poste
        en retard d'une version se voit annoncer une panne de réseau inexistante,
        et on part chercher un problème qui n'existe pas."""

        def tombe(*_a, **_k):
            raise OSError("getaddrinfo failed")

        monkeypatch.setattr(updater.urllib.request, "urlopen", tombe)
        with pytest.raises(updater.ErreurReseau, match="getaddrinfo"):
            updater.chercher("compte/depot")

    def test_quota_github_est_signale_avec_son_code(self, monkeypatch):
        def refuse(*_a, **_k):
            raise updater.urllib.error.HTTPError(
                "http://x", 403, "rate limit exceeded", {}, None
            )

        monkeypatch.setattr(updater.urllib.request, "urlopen", refuse)
        with pytest.raises(updater.ErreurReseau, match="403"):
            updater.chercher("compte/depot")

    def test_json_invalide_est_une_erreur_reseau(self, monkeypatch):
        class Fausse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        monkeypatch.setattr(
            updater.urllib.request, "urlopen", lambda *_a, **_k: Fausse(b"pas du json")
        )
        with pytest.raises(updater.ErreurReseau):
            updater.chercher("compte/depot")

    def test_pas_de_paquet_utilisable_nest_pas_une_panne_reseau(self, monkeypatch):
        """Le cas réellement rencontré : un poste ancien ne sait lire que les
        assets .exe, la release n'en publie plus. GitHub répond très bien."""
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse(
                {
                    "tag_name": "v9.9.9",
                    "assets": [
                        {
                            "name": "LamapixUploader-v9.9.9.tar.gz",
                            "browser_download_url": "http://x/a.tar.gz",
                        }
                    ],
                }
            ),
        )
        assert updater.chercher("compte/depot") is None   # et surtout : pas d'exception


class TestArchive:
    def _publier(self, tmp_path, monkeypatch, avec_dossier_racine: bool) -> Path:
        """Fabrique un ZIP de release et le sert à `telecharger`."""
        source = tmp_path / "build" / "LamapixUploader"
        (source / "_internal").mkdir(parents=True)
        (source / "LamapixUploader.exe").write_bytes(b"MZ nouvelle version")
        (source / "_internal" / "PySide6.dll").write_bytes(b"dll")

        archive = tmp_path / "paquet.zip"
        with zipfile.ZipFile(archive, "w") as zip_:
            for fichier in source.rglob("*"):
                if not fichier.is_file():
                    continue
                interne = fichier.relative_to(source.parent if avec_dossier_racine else source)
                zip_.write(fichier, interne)

        octets = archive.read_bytes()

        class Fausse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        monkeypatch.setattr(
            updater.urllib.request, "urlopen", lambda *_a, **_k: Fausse(octets)
        )
        return archive

    @pytest.mark.parametrize("avec_dossier_racine", [True, False])
    def test_extraction(self, tmp_path, monkeypatch, avec_dossier_racine):
        """Le ZIP peut porter un dossier racine ou non : on retrouve l'exe."""
        self._publier(tmp_path, monkeypatch, avec_dossier_racine)
        maj = updater.MiseAJour(
            version="9.0.0",
            url_paquet="http://x/p.zip",
            nom_paquet="LamapixUploader-v9.0.0.zip",
            notes="",
        )
        dossier = updater.telecharger(maj)
        assert (dossier / "LamapixUploader.exe").read_bytes() == b"MZ nouvelle version"
        assert (dossier / "_internal" / "PySide6.dll").exists()

    def test_archive_sans_exe_est_refusee(self, tmp_path, monkeypatch):
        archive = tmp_path / "vide.zip"
        with zipfile.ZipFile(archive, "w") as zip_:
            zip_.writestr("LISEZMOI.txt", "rien d'utile")
        octets = archive.read_bytes()

        class Fausse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        monkeypatch.setattr(
            updater.urllib.request, "urlopen", lambda *_a, **_k: Fausse(octets)
        )
        maj = updater.MiseAJour(
            version="9.0.0", url_paquet="http://x/p.zip", nom_paquet="p.zip", notes=""
        )
        with pytest.raises(RuntimeError):
            updater.telecharger(maj)


class TestGardeFous:
    def test_appliquer_refuse_hors_exe(self, monkeypatch, tmp_path):
        """En mode source il n'y a pas d'application gelée à remplacer."""
        monkeypatch.setattr(updater.paths, "est_gele", lambda: False)
        with pytest.raises(RuntimeError):
            updater.appliquer(tmp_path / "LamapixUploader.exe")

    def test_le_script_preserve_les_donnees(self, monkeypatch, tmp_path):
        """Le point critique : `donnees\\` porte la mémoire des envois. La perdre
        ferait tout renvoyer sur Lamapix, en doublon."""
        application = tmp_path / "app"
        application.mkdir()
        faux_exe = application / "LamapixUploader.exe"
        faux_exe.write_bytes(b"MZ")
        nouvelle = tmp_path / "nouvelle"
        nouvelle.mkdir()

        monkeypatch.setattr(updater.paths, "est_gele", lambda: True)
        monkeypatch.setattr(updater.sys, "executable", str(faux_exe))
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        updater.appliquer(nouvelle)

        script = (application / "_mise_a_jour.bat").read_text(encoding="utf-8")
        assert "robocopy" in script
        assert "/MIR" not in script   # /MIR purgerait `donnees\`
        assert str(nouvelle) in script
