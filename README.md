# Lamapix Uploader

Envoi automatique des photos produites par **Kadra** vers la plateforme de vente
**Lamapix**, en FTPS. Un exécutable portable, une fenêtre de contrôle, et une
mémoire locale qui garantit que rien ne part deux fois — ni ne se perd.

Reprend la logique d'un script PowerShell validé sur deux événements réels. Le
cahier des charges d'origine et ce script restent hors du dépôt (`reference/`,
non versionné) : ils citent des noms de cavaliers et une topologie réseau
interne qui n'ont rien à faire dans un dépôt public.

---

## Installer sur un PC (utilisateur)

1. Télécharger `LamapixUploader-vX.Y.Z.zip` depuis la
   [dernière release](https://github.com/hlelouarn/lamapix-uploader/releases/latest).
2. **Extraire le dossier** où vous voulez (`C:\Outils\`, une clé…) — pas besoin de
   droits administrateur. Faire un raccourci vers `LamapixUploader.exe` si utile.
3. Double-cliquer. Au premier lancement, il demande le **dossier des événements
   Kadra**, puis l'**identifiant et le mot de passe Lamapix**. Le mot de passe est
   mémorisé **chiffré** sur cette machine (DPAPI), jamais en clair.
4. Choisir l'événement à surveiller, puis laisser tourner.

> **Garder le dossier entier.** L'exe seul ne démarre pas : les bibliothèques Qt
> vivent à côté de lui, dans `_internal\`.

### Déployer sur les autres PC sans tout retaper

Configurer **un** poste, puis copier le dossier **avec** son
`donnees\config.json` sur les suivants : hôte, identifiant, dossier Kadra et
réglages arrivent déjà remplis. Seul le mot de passe est redemandé — DPAPI le
rend volontairement illisible sur une autre machine.

Rien de tout cela n'est en dur dans l'exécutable : le dépôt étant public, il ne
contient **ni identifiant, ni nom de serveur interne, ni secret**.

Pas d'installation, pas de droits administrateur. L'outil crée son sous-dossier
`donnees\` au premier lancement :

```
LamapixUploader\
├── LamapixUploader.exe
├── _internal\               bibliothèques Qt — ne pas séparer de l'exe
└── donnees\
    ├── config.json          réglages de ce PC
    ├── motdepasse.bin       mot de passe chiffré (illisible ailleurs)
    ├── tampon\<EVENEMENT>\  copie locale, structurée EXACTEMENT comme le FTP
    │   └── _memoire.json    ce qui est parti, et quand
    └── journaux\            journal horodaté, avec rotation
```

> **Un événement = une machine = un chemin.** La mémoire est locale et liée au
> chemin exact du dossier surveillé. Deux PC qui envoient le même événement ne se
> connaissent pas et enverront chacun leur lot.

### Le tampon est le plan B

Il reproduit à l'identique l'arborescence attendue par Lamapix. Si l'outil ne
peut plus rien envoyer, on glisse le contenu de `tampon\<EVENEMENT>\` dans
FileZilla et on obtient exactement le même résultat.

## Les boutons qui comptent

| Bouton | Effet |
|---|---|
| **Pause** | Suspend les envois. Le scan continue, rien n'est perdu. |
| **Initialiser la mémoire** | Déclare l'existant comme déjà envoyé, **sans rien envoyer**. À utiliser quand Kadra a déjà uploadé une partie de l'événement. Lire l'encadré ci-dessous. |
| **Annuler l'initialisation** | N'apparaît que s'il y a matière : remet en file les photos déclarées envoyées sans l'avoir été. Les envois réels ne bougent pas. |
| **Réinitialiser** | Efface la mémoire : **tout repart** sur Lamapix. Avec confirmation. |

### « Initialiser » est une déclaration, pas un constat

C'est le seul geste de l'outil qui repose sur une croyance. **Rien ne permet de
savoir ce que Lamapix a reçu** : il aspire les fichiers déposés, le dossier
distant est vide la plupart du temps, et un listing qui ne montre rien ne
distingue pas « jamais envoyé » de « envoyé puis ingéré ».

Le bouton pose donc une **frontière dans le temps** : « tout ce qui est là, je
le déclare parti ; ce qui arrivera ensuite, tu l'envoies ». Le pari est que
Kadra avait fini d'uploader au moment du clic.

**Si Kadra s'est arrêté en cours d'événement**, les photos produites mais jamais
uploadées sont sur le disque. Les avaler d'un bloc les enterre : plus rien ne les
enverra, sans erreur ni compteur rouge. D'où deux garde-fous :

- la frontière est **choisie** — « seulement les photos antérieures à \<heure\> »,
  avec le nombre exact affiché avant de valider ;
- le geste est **annulable** — les photos déclarées restent marquées comme telles,
  comptées à part dans la fenêtre (bandeau ambre), et un bouton les remet en file.

Dans le doute, ne pas cliquer : on paie des doublons, mais on ne perd rien.

Fermer la fenêtre **n'arrête pas les envois** : l'outil continue près de
l'horloge. Pour l'arrêter vraiment : clic droit sur l'icône → *Quitter*.

## Mises à jour

L'outil interroge les *releases* GitHub au démarrage. S'il en trouve une plus
récente, il **propose de l'installer** : il se ferme, se remplace et se relance
seul. La mémoire et la config sont conservées — les envois reprennent où ils en
étaient. Le bouton **Mise à jour** force la vérification à tout moment ; il
répond toujours quelque chose, y compris « vous êtes à jour ».

Hors ligne, la vérification échoue en silence et n'empêche rien.

## Si un antivirus supprime l'outil

Ça arrive avec tout exécutable Python non signé. Deux précautions sont déjà
prises côté build :

- **livraison en dossier, pas en fichier unique.** Un exe PyInstaller « onefile »
  se décompresse dans `%TEMP%` à chaque lancement puis exécute le résultat —
  comportement d'un *dropper*, que les heuristiques (`Wacatac`, `Sabsik`,
  `Zusy`…) suppriment régulièrement. Le build « onedir » évite ce schéma ;
- **pas de compression UPX**, qui aggrave nettement le problème.

Il reste que le binaire n'est **pas signé**. Si un poste le supprime malgré
tout :

1. identifier la détection, sur le poste concerné :
   `Get-MpThreat | Select-Object ThreatName, IsActive` ;
2. si le nom se termine par `!ml` ou contient `Wacatac`/`Sabsik`, c'est une
   heuristique générique — donc un faux positif. Le signaler à Microsoft
   (<https://www.microsoft.com/en-us/wdsi/filesubmission>) le corrige pour tout
   le monde en quelques jours ;
3. en attendant, exclure le dossier, **en administrateur** :
   `Add-MpPreference -ExclusionPath "C:\Outils\LamapixUploader"`.

Le remède définitif serait un **certificat de signature de code** (~200-500 €/an,
HSM obligatoire depuis 2023) : il supprime aussi l'avertissement SmartScreen au
téléchargement. Non fait à ce jour.

---

## Ce que fait l'outil, dans l'ordre

1. **Scan** du dossier événement (toutes les 30 s par défaut). Les fichiers
   modifiés il y a moins de 15 s sont ignorés — Kadra est peut-être encore en
   train de les écrire.
2. **Restructuration** vers l'arborescence Lamapix :

   | Source (Kadra) | Destination (Lamapix) |
   |---|---|
   | `<EPREUVE>\<NUM>_<NOM>_<CHEVAL>\x.jpg` | `/<EVENEMENT>/<NOM>_<CHEVAL>/x.jpg` |
   | `0_AMBIANCE\**\x.jpg` | `/<EVENEMENT>/AMBIANCE/x.jpg` (à plat) |
   | `x.jpg` (racine) | `/<EVENEMENT>/x.jpg` |

   L'épreuve et le dossard disparaissent : un cavalier engagé sur plusieurs
   épreuves **fusionne** dans un seul dossier distant. Les JPEG seulement ; tout
   chemin passant par un dossier `webp` est ignoré.
3. **Copie dans le tampon**, puis **envoi FTPS** (1 à 3 connexions parallèles).
4. **Purge du tampon** des photos envoyées depuis plus de 24 h. Jamais la source,
   jamais le FTP, jamais la mémoire.

## Pourquoi la mémoire locale fait seule autorité

Lamapix **aspire** ce qu'on dépose : fichiers et dossiers disparaissent après
ingestion. Un FTP vide ne prouve donc rien. Conséquences, toutes implémentées :

- ce qui a été envoyé est su **localement**, dans `_memoire.json` (écriture
  atomique, sauvegarde tous les 20 envois → un crash ne fait pas tout renvoyer) ;
- « ce dossier existe » n'est **jamais** mis en cache définitivement : sur échec
  (typiquement **550**), on invalide, on recrée l'arborescence, et on réessaie
  sur une **connexion neuve** ;
- la racine de l'événement est **créée par l'outil** — Kadra ne le fait plus ;
- 3 tentatives par photo, puis mise en attente 4 min et **la file continue** :
  un fichier en erreur ne bloque jamais les autres. 3 échecs d'affilée dans un
  dossier → tout le dossier attend 4 min ;
- **rien n'est jamais supprimé côté FTP**.

---

## Développement

```powershell
cd C:\dev\lamapix-uploader
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pyinstaller pillow
```

### Tests

```powershell
$env:PYTHONUTF8 = 1
.\.venv\Scripts\python.exe -m pytest
```

112 tests. Le moteur est testé de bout en bout contre un **faux serveur Lamapix**
(`tests/conftest.py`) qui rejoue les pièges du terrain : dossiers consommés en
cours de route, 550 passagers, pannes durables, identifiants refusés. Aucun test
ne touche le vrai serveur.

### Lancer depuis les sources

```powershell
.\.venv\Scripts\python.exe app.py
```

### Construire l'exe

```powershell
.\packaging\construire.ps1            # tests + icône + PyInstaller + ZIP → dist\
.\packaging\construire.ps1 -Publier   # + release GitHub (updater)
```

Le script refuse de construire si les tests ne passent pas. La version publiée
est celle de `lamapix_uploader/__init__.py` — seule source de vérité, c'est elle
que l'updater compare au tag de la release.

### Architecture

Le cœur ne connaît **ni Qt ni le réseau**, ce qui le rend testable :

| Module | Rôle |
|---|---|
| `mapping.py` | Règles Kadra → Lamapix, unicité des noms distants |
| `memory.py` | Mémoire persistante par événement |
| `scanner.py` | Scan, stabilité, exclusions |
| `ftp.py` | Client FTPS explicite, MKD par niveaux, cache invalidable |
| `engine.py` | Pipeline, parallélisme, reprises, cooldowns, purge |
| `journal.py` / `config.py` / `secrets_win.py` / `paths.py` | Journal tournant, réglages, DPAPI, portabilité |
|  `ui/` | Fenêtre Qt, réglages, zone de notification |

`engine._un_tour()` exécute un cycle complet **sans jamais dormir** : c'est le
point d'entrée des tests.

### Sécurité

Aucun secret dans le code ni dans Git. Le mot de passe vit uniquement dans
`donnees\motdepasse.bin`, chiffré par DPAPI, et `donnees/` est dans le
`.gitignore`.
