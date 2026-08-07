"""Mise à jour : comparaison de versions et lecture d'une release GitHub.

Rien ici ne touche le réseau — la réponse de GitHub est fournie en dur.
"""

from __future__ import annotations

import io
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
        maj = updater.MiseAJour(version=publiee, url_exe="http://x/y.exe", notes="")
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
                    "tag_name": "v1.2.0",
                    "body": "Corrige le bouton Quitter",
                    "assets": [
                        {"name": "notes.txt", "browser_download_url": "http://x/n.txt"},
                        {
                            "name": "LamapixUploader.exe",
                            "browser_download_url": "http://x/LamapixUploader.exe",
                        },
                    ],
                }
            ),
        )
        trouvee = updater.chercher("compte/depot")
        assert trouvee is not None
        assert trouvee.version == "1.2.0"          # le « v » du tag est retiré
        assert trouvee.url_exe.endswith("LamapixUploader.exe")
        assert "Quitter" in trouvee.notes

    def test_release_sans_exe_est_ignoree(self, monkeypatch):
        """Une release sans binaire n'est pas installable : autant l'ignorer."""
        monkeypatch.setattr(
            updater.urllib.request,
            "urlopen",
            _reponse({"tag_name": "v9.9.9", "assets": [{"name": "src.zip"}]}),
        )
        assert updater.chercher("compte/depot") is None

    def test_reponse_sans_tag(self, monkeypatch):
        monkeypatch.setattr(updater.urllib.request, "urlopen", _reponse({"assets": []}))
        assert updater.chercher("compte/depot") is None

    def test_reseau_coupe_ne_leve_pas(self, monkeypatch):
        """Hors ligne sur un site de concours : ça ne doit surtout rien casser."""

        def tombe(*_a, **_k):
            raise OSError("getaddrinfo failed")

        monkeypatch.setattr(updater.urllib.request, "urlopen", tombe)
        assert updater.chercher("compte/depot") is None

    def test_json_invalide_ne_leve_pas(self, monkeypatch):
        class Fausse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        monkeypatch.setattr(
            updater.urllib.request, "urlopen", lambda *_a, **_k: Fausse(b"pas du json")
        )
        assert updater.chercher("compte/depot") is None


class TestGardeFous:
    def test_appliquer_refuse_hors_exe(self, monkeypatch, tmp_path):
        """En mode source il n'y a pas d'exe à remplacer : on ne bricole pas."""
        monkeypatch.setattr(updater.paths, "est_gele", lambda: False)
        with pytest.raises(RuntimeError):
            updater.appliquer(tmp_path / "LamapixUploader.exe")
