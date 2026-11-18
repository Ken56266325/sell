#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Console Instagram WebCAA — création simple, automatique (SMSBower) et export de session."""

from __future__ import annotations

import base64
from html import unescape as html_unescape
import json
import os
import random
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import unicodedata
import shutil
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

try:
    import requests
except ImportError:
    requests = None

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None


ROOT = Path(__file__).resolve().parent
COMPTE_PROBLEME = ROOT / "COMPTE_PROBLEME"
LIBRARY_ROOT = ROOT / "insta_creator_v2"
LIBRARY_REPOSITORY = "https://github.com/Ken56266325/insta_creator_v2.git"
GITHUB_TOKEN = "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"
WHATSAPP_CREATION_STATS_FILE = ROOT / "whatsapp_creation_stats.json"
CREATION_STATS = {"success": 0, "problem": 0}


def _load_creator_library():
    """Charge la bibliothèque locale ou l'installe depuis GitHub si absente."""
    global requests, curl_requests

    if LIBRARY_ROOT.is_dir() and str(LIBRARY_ROOT) not in sys.path:
        sys.path.insert(0, str(LIBRARY_ROOT))

    try:
        from insta_creator import InstagramSignup, create_secondary_mobile_profile
        if requests is None:
            import requests as _requests
            requests = _requests
        if curl_requests is None:
            try:
                from curl_cffi import requests as _curl_requests
                curl_requests = _curl_requests
            except ImportError:
                pass
        return InstagramSignup, create_secondary_mobile_profile
    except (ImportError, ModuleNotFoundError) as first_error:
        print("[INFO] Bibliothèque insta_creator absente ou incomplète : installation pip...")

    token = (GITHUB_TOKEN.strip() or os.environ.get("INSTA_CREATOR_GITHUB_TOKEN", "").strip())
    if not token:
        raise RuntimeError(
            "insta_creator n'est pas installé. Pour un dépôt GitHub privé, "
            "remplissez GITHUB_TOKEN avec un PAT GitHub en lecture du dépôt, "
            "puis relancez le script."
        ) from first_error

    encoded_token = quote(token, safe="")
    authenticated_repository = (
        "git+https://x-access-token:"
        + encoded_token
        + "@"
        + LIBRARY_REPOSITORY.removeprefix("https://")
    )
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        authenticated_repository,
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        safe_error = (completed.stderr or completed.stdout or "échec pip").replace(token, "***")
        raise RuntimeError(f"Installation de insta_creator échouée : {safe_error[-1500:]}")

    import importlib
    importlib.invalidate_caches()
    sys.modules.pop("insta_creator", None)
    from insta_creator import InstagramSignup, create_secondary_mobile_profile
    if requests is None:
        import requests as _requests
        requests = _requests
    if curl_requests is None:
        try:
            from curl_cffi import requests as _curl_requests
            curl_requests = _curl_requests
        except ImportError:
            pass
    print("[OK] Bibliothèque insta_creator installée.")
    return InstagramSignup, create_secondary_mobile_profile


InstagramSignup, create_secondary_mobile_profile = _load_creator_library()


# ---------------------------------------------------------------------------
# Palette console
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"

# --- NOUVEAU : URL du service WhatsApp ---
WA_SERVICE_URL = os.environ.get("WA_SERVICE_URL", "http://localhost:3000")
_WA_SERVER_PROCESS = None


def proxy_for_account(proxy: str | bool | None, country: str) -> str | bool | None:
    """Transmet le choix au client WebCAA, qui génère le proxy Capsolver."""
    if proxy is True:
        return True
    if isinstance(proxy, str) and proxy.strip():
        return proxy.strip()
    return None

FIRST_NAMES = (
    "Kenny", "Mila", "Emma", "Chloe", "Nora", "Lina", "Sofia", "Mason",
    "Liam", "Noah", "Lucas", "Ethan", "Ava", "Maya", "Léa", "Zoé",
)
LAST_NAMES = (
    "Allen", "Jackson", "Campbell", "Martin", "Brown", "Miller", "Smith",
    "Wilson", "Taylor", "Thomas", "Anderson", "Clark", "Walker", "Hall",
)


def ok(message: str) -> None:
    print(f"{GREEN}{BOLD}  ✔  {RESET}{message}")


def error(message: str) -> None:
    print(f"{RED}{BOLD}  ✘  {RESET}{message}")


def info(message: str) -> None:
    print(f"{CYAN}  ●  {RESET}{message}")


def warning(message: str) -> None:
    print(f"{YELLOW}  ⚠  {RESET}{message}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" (défaut={default})" if default else ""
    try:
        value = input(f"{YELLOW}  ➤  {RESET}{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return value or default


# ---------------------------------------------------------------------------
# Sélection proxy (utilisée pour TOUT : manuel + auto)
# ---------------------------------------------------------------------------
def ask_proxy_settings() -> tuple[str | bool | None, str, str, int]:
    """Retourne (proxy, portée, pays Capsolver, retries réseau)."""
    print(f"\n{CYAN}{BOLD}Connexion inscription{RESET}")
    print("  [1] Directe (défaut)")
    print("  [2] Proxy personnalisé")
    print("  [3] Proxy Capsolver")
    choice = ask("Choix", "1")
    if choice == "1":
        return None, "none", "any", 1
    if choice == "2":
        proxy: str | bool | None = ask("URL proxy")
        if not proxy:
            warning("URL absente : connexion directe utilisée.")
            return None, "none", "any", 1
        proxy_country = "any"
        proxy_retries = 1
    elif choice == "3":
        proxy = True
        proxy_retries = 3

        print("\nPays Capsolver :")
        print("  [1] Any — pays quelconque")
        print("  [2] USA — IP résidentielle américaine")
        country_choice = ask("Pays", "1")
        countries = {"1": "any", "2": "us"}
        proxy_country = countries.get(country_choice, "any")
        if country_choice not in countries:
            warning("Pays invalide : any sera utilisé.")
    else:
        warning("Choix invalide : connexion directe utilisée.")
        return None, "none", "any", 1

    print("\nPortée du proxy :")
    print("  [1] Toute la session, du lancement à la création finale")
    print("  [2] Jusqu'à la soumission du code, puis direct pour la finalisation")
    print("  [3] Seulement envoi / renvoi du code (validation du code en direct)")
    print("  [4] Seulement la soumission finale de création")
    print("  [5] Proxy jusqu'au code reçu, puis direct pour soumission et suite")
    scope = ask("Choix", "1")
    modes = {
        "1": "all", "2": "pre_final", "3": "code", "4": "final",
        "5": "before_code",
    }
    mode = modes.get(scope, "all")
    return proxy, mode, proxy_country, proxy_retries


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------
class Spinner:
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            frame = self.FRAMES[index % len(self.FRAMES)]
            print(f"\r{CYAN}{BOLD}{frame}{RESET}  {self.label}   ", end="", flush=True)
            index += 1
            time.sleep(0.08)

    def start(self, label: str | None = None) -> None:
        if label:
            self.label = label
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        print("\r" + " " * 86 + "\r", end="", flush=True)
        self._thread = None


# ---------------------------------------------------------------------------
# Générateurs identité
# ---------------------------------------------------------------------------
def ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    plain = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", plain)


def generate_identity() -> tuple[str, str, str, str]:
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    full_name = f"{first} {last}"
    base = (ascii_slug(first) + ascii_slug(last))[:18] or "instagram"
    username = f"{base}{secrets.randbelow(90000) + 10000}"
    return first, last, full_name, username


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%_-"
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    random.SystemRandom().shuffle(chars)
    return "".join(chars)


def generate_birthday() -> tuple[int, int, int]:
    today = date.today()
    youngest = date(today.year - 18, today.month, today.day)
    oldest = date(today.year - 35, today.month, today.day)
    chosen = oldest + timedelta(days=random.randint(0, (youngest - oldest).days))
    return chosen.day, chosen.month, chosen.year


def safe_username(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "")).strip("._")
    return cleaned[:80] or "instagram_account"


# ---------------------------------------------------------------------------
# Sauvegarde compte
# ---------------------------------------------------------------------------
def organize_account_batches(root: Path | None = None, batch_size: int = 50) -> list[dict[str, Any]]:
    """Déplace uniquement les lots complets de 50 JSON et les archive."""
    folder = Path(root or (ROOT / "COMPTE"))
    if batch_size <= 0:
        raise ValueError("La taille d'un lot doit être positive.")
    if not folder.exists():
        return []
    for old_dir in sorted(folder.glob("comptes_50_*")):
        if not old_dir.is_dir():
            continue
        old_files = sorted(old_dir.glob("*.json"), key=lambda p: p.name.casefold())
        if len(old_files) == batch_size:
            old_archive = folder / f"{old_dir.name}.zip"
            for old_file in old_files:
                duplicate = folder / old_file.name
                if old_archive.exists() and duplicate.exists():
                    duplicate.unlink()
        if 0 < len(old_files) < batch_size:
            for old_file in old_files:
                target = folder / old_file.name
                if not target.exists():
                    shutil.copy2(old_file, target)
                old_file.unlink()
            old_archive = folder / f"{old_dir.name}.zip"
            if old_archive.exists():
                old_archive.unlink()
            try:
                old_dir.rmdir()
            except OSError:
                pass

    account_files = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json"),
        key=lambda p: p.name.casefold(),
    )
    complete_count = (len(account_files) // batch_size) * batch_size
    if complete_count == 0:
        return []

    existing_indexes = []
    for candidate in folder.glob("comptes_50_*"):
        match = re.fullmatch(r"comptes_50_(\d+)", candidate.name)
        if match and candidate.is_dir():
            existing_indexes.append(int(match.group(1)))
    next_index = max(existing_indexes, default=0) + 1
    batches: list[dict[str, Any]] = []
    for offset in range(0, complete_count, batch_size):
        index = next_index + offset // batch_size
        files = account_files[offset:offset + batch_size]
        batch_dir = folder / f"comptes_50_{index}"
        archive = folder / f"comptes_50_{index}.zip"
        batch_dir.mkdir(parents=True, exist_ok=True)
        for stale in batch_dir.glob("*.json"):
            stale.unlink()
        for source in files:
            shutil.copy2(source, batch_dir / source.name)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for source in files:
                zipped.write(batch_dir / source.name, arcname=source.name)
        with zipfile.ZipFile(archive, "r") as zipped:
            if len([name for name in zipped.namelist() if name.lower().endswith(".json")]) != batch_size:
                raise RuntimeError(f"Archive incomplète pour le lot {index}.")
        for source in files:
            source.unlink()
        batches.append({
            "index": index,
            "count": len(files),
            "folder": str(batch_dir),
            "zip": str(archive),
        })
    return batches


def save_account(result: dict, password: str) -> Path:
    """Vérifie la session avant de classer le JSON.

    Un compte valide va dans ``COMPTE``. Tout autre résultat est conservé
    dans ``COMPTE_PROBLEME`` afin de ne jamais mélanger les sessions douteuses
    avec les comptes prêts à l'emploi.
    """
    global CREATION_STATS
    username = str(result.get("username") or "").strip()
    if not username:
        raise RuntimeError("Instagram n'a pas retourné de username.")
    cookie_header = result.get("cookie_header")
    if not cookie_header:
        cookies = result.get("cookies")
        if isinstance(cookies, dict):
            cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        else:
            cookie_header = ""

    if cookie_header:
        verification, verification_detail = _check_instagram_account(username, str(cookie_header))
    else:
        verification, verification_detail = "ERROR", "cookies absents"

    is_valid = verification == "LIVE"
    folder = ROOT / "COMPTE" if is_valid else COMPTE_PROBLEME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_username(username)}.json"
    payload = {
        "username": username,
        "password": password,
        "cookies": str(cookie_header),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if is_valid:
        CREATION_STATS["success"] += 1
        try:
            organize_account_batches(folder)
            moved_copy = next(
                (candidate for batch_dir in folder.glob("comptes_50_*")
                 if batch_dir.is_dir()
                 for candidate in [batch_dir / path.name]
                 if candidate.exists()),
                None,
            )
            if moved_copy is not None:
                path = moved_copy
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            warning(f"Archivage des lots impossible : {exc}")
    else:
        CREATION_STATS["problem"] += 1
        warning(f"Compte @{username} classé dans COMPTE_PROBLEME : {verification_detail or verification}")
    result["_verification_status"] = verification
    result["_verification_detail"] = verification_detail
    info(f"Bilan créations — réussis: {CREATION_STATS['success']} | problèmes: {CREATION_STATS['problem']}")
    return path


STATS_COOKIE_FILE = ROOT / "COMPTE" / ".instagram_stats_cookie"


def _load_stats_cookie() -> str:
    try:
        return STATS_COOKIE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _save_stats_cookie(cookie: str) -> None:
    STATS_COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATS_COOKIE_FILE.write_text(str(cookie).strip(), encoding="utf-8")


def _delete_stats_cookie() -> bool:
    try:
        STATS_COOKIE_FILE.unlink()
        return True
    except FileNotFoundError:
        return False


def _batch_directories() -> list[tuple[int, Path]]:
    folder = ROOT / "COMPTE"
    result = []
    for path in folder.glob("comptes_50_*"):
        match = re.fullmatch(r"comptes_50_(\d+)", path.name)
        if match and path.is_dir():
            files = list(path.glob("*.json"))
            if files:
                result.append((int(match.group(1)), path))
    return sorted(result, key=lambda item: item[0])


def _cookie_dict(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(cookie_header or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key:
            cookies[key] = value
    return cookies


def _check_instagram_account(username: str, cookie_header: str) -> tuple[str, str]:
    """Retourne (statut, détail) sans exposer le cookie dans les logs."""
    clean = str(username or "").lstrip("@").strip()
    if not clean:
        return "ERROR", "username vide"
    url = f"https://www.instagram.com/{quote(clean, safe='')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,"
                  "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": cookie_header,
    }
    try:
        if curl_requests is not None:
            try:
                response = curl_requests.get(
                    url, headers=headers, timeout=20, allow_redirects=True, impersonate="chrome",
                )
            except TypeError:
                response = curl_requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        else:
            response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        html = response.text
        body = html.casefold()
        route_text = " ".join(
            str(getattr(item, "url", "")).casefold()
            for item in [*getattr(response, "history", []), response]
        )
        if any(token in route_text for token in ("/accounts/suspended", "/suspended/", "/account/suspended")):
            return "SUSPENDED", "Instagram a suspendu le compte"
        if any(token in route_text for token in ("/accounts/disabled", "/accounts/disable", "/account/disable", "/disabled/")):
            return "DISABLED", "Instagram a désactivé le compte"
        if any(token in route_text for token in ("/challenge/", "/checkpoint/", "/accounts/verify")):
            return "CHALLENGE", "Instagram demande une vérification"
        if "/accounts/login" in route_text or "/login/" in route_text:
            return "DISCONNECTED", "Session redirigée vers la connexion"
        if any(token in body for token in ("account suspended", "your account has been suspended", "compte suspendu")):
            return "SUSPENDED", "Instagram a suspendu le compte"
        if any(token in body for token in ("account disabled", "your account has been disabled", "compte désactivé")):
            return "DISABLED", "Instagram a désactivé le compte"
        if any(token in body for token in ("checkpoint_required", "challenge_required", "/challenge/")):
            return "CHALLENGE", "Instagram demande une vérification"
        route_page_ids = {
            value.casefold()
            for value in re.findall(r'"pageID"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
        }
        unavailable = (
            response.status_code == 404
            or "page isn't available" in body
            or "page not found" in body
            or "profile isn't available" in body
            or "the link you followed may be broken" in body
            or '"show_lox_redesigned_404_page":true' in body
            or "httperrorpage" in route_page_ids
        )
        if unavailable:
            return "DEAD_OR_UNAVAILABLE", f"HTTP {response.status_code} / profil introuvable"
        if response.status_code >= 400:
            return "ERROR", f"HTTP {response.status_code}"
        if "login" in response.url.casefold() and "instagram.com/accounts/login" in response.url.casefold():
            return "ERROR", "session redirigée vers la connexion"
        username_re = re.compile(rf'"username"\s*:\s*"{re.escape(clean)}"', re.IGNORECASE)
        username_found = bool(username_re.search(html))

        def meta_content(property_name: str) -> str:
            patterns = (
                rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']*)',
                rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
            )
            for pattern in patterns:
                found = re.search(pattern, html, re.IGNORECASE)
                if found:
                    return html_unescape(found.group(1)).strip()
            return ""

        og_title = meta_content("og:title")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        page_title = html_unescape(title_match.group(1)).strip() if title_match else ""
        identity_text = f"{og_title} {page_title}"
        identity_found = bool(re.search(rf"(?<![A-Za-z0-9_.])@?{re.escape(clean)}(?![A-Za-z0-9_.])", identity_text, re.IGNORECASE))

        if username_found or identity_found:
            full_name = ""
            for candidate in (og_title, page_title):
                name_match = re.search(rf"^\s*(.*?)\s*\(@?{re.escape(clean)}\)", candidate, re.IGNORECASE)
                if name_match:
                    full_name = name_match.group(1).strip()
                    break
            if not full_name:
                profile_name_match = re.search(
                    rf'"full_name"\s*:\s*"([^"]*)".{{0,1800}}?"username"\s*:\s*"{re.escape(clean)}"',
                    html, re.IGNORECASE | re.DOTALL,
                )
                if profile_name_match:
                    full_name = html_unescape(profile_name_match.group(1)).strip()
            detail = f"HTTP {response.status_code}"
            if full_name:
                detail += f" — nom: {full_name}"
            return "LIVE", detail
        return "ERROR", f"identité du profil non confirmée (HTTP {response.status_code})"
    except Exception as exc:
        return "ERROR", str(exc)


def manage_stats_cookie() -> None:
    """Ajoute, remplace ou supprime le cookie utilisé par les statistiques."""
    while True:
        current = _load_stats_cookie()
        print("\nCookie de vérification Instagram")
        print("  [1] Ajouter / remplacer le cookie")
        print("  [2] Supprimer le cookie")
        print("  [0] Retour")
        choice = ask("Choix", "0")
        if choice == "1":
            value = ask("Cookie Instagram (header complet)")
            if value:
                _save_stats_cookie(value)
                ok("Cookie enregistré.")
            else:
                warning("Cookie vide : aucune modification.")
        elif choice == "2":
            if _delete_stats_cookie():
                ok("Cookie supprimé.")
            else:
                info("Aucun cookie enregistré.")
        else:
            return
        if current and choice in {"1", "2"}:
            info("Le cookie précédent a été remplacé ou supprimé.")


def show_account_statistics() -> None:
    batches = _batch_directories()
    if not batches:
        warning("Aucun dossier comptes_50_* contenant des fichiers JSON n'est disponible.")
        return
    cookie = _load_stats_cookie()
    if not cookie:
        cookie = ask("Cookie Instagram (il sera enregistré pour les prochaines vérifications)")
        if not cookie:
            warning("Cookie obligatoire pour lancer la vérification.")
            return
        _save_stats_cookie(cookie)
        ok("Cookie enregistré.")
    print("\nLots disponibles :")
    for index, path in batches:
        count = len(list(path.glob("*.json")))
        print(f"  [{index}] {path.name} — {count} compte(s)")
    selected = ask("Lot à vérifier (0 = retour)", "0")
    if selected == "0":
        return
    try:
        index = int(selected)
    except ValueError:
        warning("Numéro de lot invalide.")
        return
    batch_path = next((path for batch_index, path in batches if batch_index == index), None)
    if batch_path is None:
        warning("Ce dossier n'existe pas ou ne contient aucun fichier JSON.")
        return

    account_files = sorted(batch_path.glob("*.json"), key=lambda p: p.name.casefold())
    live = dead = errors = 0
    dead_users: list[str] = []
    total_accounts = len(account_files)
    print(f"\n{BLUE}{BOLD}STATISTIQUES — {batch_path.name} ({total_accounts} comptes){RESET}")
    for position, account_file in enumerate(account_files, 1):
        try:
            data = json.loads(account_file.read_text(encoding="utf-8"))
            username = str(data.get("username") or account_file.stem)
        except (OSError, ValueError) as exc:
            errors += 1
            print(f"[{position:02d}/{total_accounts}] {account_file.name} — ERROR: {exc}")
            continue
        status, detail = _check_instagram_account(username, cookie)
        if status == "LIVE":
            live += 1
        elif status == "DEAD_OR_UNAVAILABLE":
            dead += 1
            dead_users.append(username)
        else:
            errors += 1
        print(f"[{position:02d}/{total_accounts}] @{username} — {status} ({detail})")
        time.sleep(0.25)
    print("\n┌─ BILAN ─────────────────────────────┐")
    print(f"│ Total       : {total_accounts:>3}              │")
    print(f"│ Vivants     : {live:>3}              │")
    print(f"│ Introuvables: {dead:>3}              │")
    print(f"│ Erreurs     : {errors:>3}              │")
    print("└─────────────────────────────────────┘")
    if dead_users:
        print("\nComptes introuvables/supprimés :")
        for username in dead_users:
            print(f"  - @{username}")


def show_single_account_status() -> None:
    """Vérifie un seul username avec le cookie de statistiques enregistré."""
    cookie = _load_stats_cookie()
    if not cookie:
        cookie = ask("Cookie Instagram (il sera enregistré pour les prochaines vérifications)")
        if not cookie:
            warning("Cookie obligatoire pour lancer la vérification.")
            return
        _save_stats_cookie(cookie)
        ok("Cookie enregistré.")

    username = ask("Username Instagram à vérifier").strip().lstrip("@")
    if not username:
        warning("Username vide.")
        return
    status, detail = _check_instagram_account(username, cookie)
    print(f"\n{BLUE}{BOLD}VÉRIFICATION DU COMPTE{RESET}")
    print(f"  Username : @{username}")
    print(f"  Statut   : {status}")
    name_match = re.search(r"— nom:\s*(.+)$", detail)
    if name_match:
        print(f"  Nom      : {name_match.group(1).strip()}")
    print(f"  Détail   : {detail}")


# ---------------------------------------------------------------------------
# SPC (mode manuel uniquement)
# ---------------------------------------------------------------------------
def _bearer_from_result(result: dict) -> str:
    bearer = str(result.get("mobile_bearer") or result.get("mobile_authorization") or "").strip()
    if bearer:
        return bearer.removeprefix("Bearer ").strip()
    cookies = result.get("cookies") or result.get("web_cookies") or {}
    user_id = str(result.get("user_id") or cookies.get("ds_user_id") or "")
    sessionid = str(cookies.get("sessionid") or result.get("session_id") or "")
    if not user_id or not sessionid:
        return ""
    raw = json.dumps({"ds_user_id": user_id, "sessionid": sessionid}, separators=(",", ":")).encode("utf-8")
    return "IGT:2:" + base64.b64encode(raw).decode("ascii")


def create_spc_chain(source_result: dict, password: str, limit: int = 4) -> dict:
    source = dict(source_result)
    created_paths: list[str] = []
    for index in range(1, limit + 1):
        _first, _last, _full_name, generated_username = generate_identity()
        username = ask(
            f"Username du profil SPC {index}/{limit} (vide = généré: @{generated_username})",
            generated_username,
        )
        bearer = _bearer_from_result(source)
        if not bearer:
            error("SPC : la session source ne contient pas de Bearer exploitable.")
            break
        info(f"Création du profil SPC {index}/{limit} via la session @{source.get('username', '')}...")
        try:
            spc_result = create_secondary_mobile_profile(
                bearer,
                source_user_id=str(source.get("user_id") or ""),
                rur=str(source.get("rur") or ""),
                mid=str(source.get("mid") or ""),
                www_claim=str(source.get("www_claim") or ""),
                username=username,
                save_to_file=False,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            error(f"SPC {index}/{limit} : {exc}")
            break
        if not spc_result.get("success"):
            error(f"SPC {index}/{limit} : création non confirmée.")
            break
        try:
            path = save_account(spc_result, password)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error(f"SPC créé, mais sauvegarde impossible : {exc}")
            break
        if spc_result.get("_verification_status") != "LIVE":
            warning("SPC créé mais classé dans COMPTE_PROBLEME.")
            break
        ok(f"Profil SPC {index}/{limit} créé : @{spc_result.get('username', username)}")
        info(f"JSON sauvegardé : {path}")
        created_paths.append(str(path))
    return {"created_paths": created_paths, "source": source}


# ===========================================================================
# SMSBOWER — client intégré
# ===========================================================================
SMSBOWER_BASE_URL = "https://smsbower.page/stubs/handler_api.php"
SMSBOWER_SERVICE = "ig"
SMSBOWER_COUNTRY = "12"
SMSBOWER_MAX_PRICE = "0.08"
SMSBOWER_OFFER_MAX_PRICE = 0.08
SMSBOWER_KEY_FILE = ROOT / "smsbowser.txt"


def _display_phone(phone: str) -> str:
    phone = str(phone)
    return phone if phone.startswith("+") else "+" + phone


def _money(value: Any) -> str:
    try:
        return f"{float(value):.4f} $"
    except (TypeError, ValueError):
        return "inconnu"


def _json_response(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


class SMSBowerClient:
    """Wrapper minimal de l'API SMSBower (service Instagram, pays 12)."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._received_codes: set[str] = set()
        self.max_price = SMSBOWER_MAX_PRICE
        self.provider_ids: list[str] = []
        self.selected_offers: list[dict[str, Any]] = []
        self.selection_mode = "manual"

    def has_received_code(self, activation_id: str) -> bool:
        return str(activation_id) in self._received_codes

    def _request(self, action: str, **params: Any) -> str | None:
        payload = {"api_key": self.api_key, "action": action, **params}
        try:
            response = requests.get(SMSBOWER_BASE_URL, params=payload, timeout=30)
            response.raise_for_status()
            return response.text.strip()
        except requests.RequestException as exc:
            error(f"SMSBower réseau ({action}) : {exc}")
            return None

    @staticmethod
    def verify_key(api_key: str) -> bool:
        try:
            response = requests.get(
                SMSBOWER_BASE_URL,
                params={"api_key": api_key, "action": "getBalance"},
                timeout=15,
            )
            return response.text.strip().startswith("ACCESS_BALANCE:")
        except requests.RequestException:
            return False

    @classmethod
    def load_or_ask_key(cls) -> "SMSBowerClient | None":
        if SMSBOWER_KEY_FILE.is_file():
            try:
                key = SMSBOWER_KEY_FILE.read_text(encoding="utf-8").strip()
                if key and cls.verify_key(key):
                    ok(f"Clé API SMSBower chargée depuis {SMSBOWER_KEY_FILE.name}.")
                    return cls(key)
                if key:
                    warning("Clé SMSBower enregistrée invalide.")
            except OSError as exc:
                warning(f"Lecture {SMSBOWER_KEY_FILE.name} : {exc}")

        while True:
            key = ask("Clé API SMSBower (vide = annuler)")
            if not key:
                return None
            if cls.verify_key(key):
                try:
                    SMSBOWER_KEY_FILE.write_text(key, encoding="utf-8")
                    ok(f"Clé API vérifiée et enregistrée dans {SMSBOWER_KEY_FILE.name}.")
                except OSError as exc:
                    warning(f"Sauvegarde clé impossible : {exc}")
                return cls(key)
            error("Clé SMSBower invalide ou service inaccessible.")

    def get_offers(self, max_price: float = SMSBOWER_OFFER_MAX_PRICE) -> list[dict[str, Any]]:
        raw = self._request("getPricesV3", service=SMSBOWER_SERVICE, country=SMSBOWER_COUNTRY)
        data = _json_response(raw)
        country = data.get(SMSBOWER_COUNTRY) if isinstance(data, dict) else None
        service = country.get(SMSBOWER_SERVICE) if isinstance(country, dict) else None
        if not isinstance(service, dict):
            return []

        offers: list[dict[str, Any]] = []
        for provider_key, details in service.items():
            if not isinstance(details, dict):
                continue
            try:
                price = float(details.get("price"))
                stock = int(details.get("count", 0))
            except (TypeError, ValueError):
                continue
            if price <= max_price and stock > 0:
                offers.append({
                    "provider_id": str(details.get("provider_id") or provider_key),
                    "price": price,
                    "count": stock,
                })
        return sorted(offers, key=lambda item: (item["price"], item["provider_id"]))

    def choose_offers(self) -> bool:
        offers = self.get_offers()
        if not offers:
            error(f"SMSBower : aucune offre {SMSBOWER_SERVICE} en stock jusqu'à {SMSBOWER_OFFER_MAX_PRICE:.2f} $.")
            return False

        print(f"\n{CYAN}{BOLD}Offres SMSBower Instagram disponibles{RESET}")
        for index, offer in enumerate(offers, 1):
            print(f"  [{index}] provider={offer['provider_id']} — {offer['price']:.3f} $ — stock={offer['count']}")
        print("  [A] Automatique — moins chère, puis suivante si indisponible")
        print("  [T] Toutes les offres affichées (sélection dynamique)")
        choice = ask("Offre à utiliser", "A").strip().lower()

        if choice in {"a", "auto", "automatique"}:
            selected = offers
            self.selection_mode = "auto"
        elif choice in {"t", "all", "toutes", "0"}:
            selected = offers
            self.selection_mode = "all"
        else:
            try:
                selected = [offers[int(choice) - 1]]
            except (ValueError, IndexError):
                warning("Choix invalide : l'offre la moins chère sera utilisée.")
                selected = [offers[0]]
            self.selection_mode = "manual"

        self.selected_offers = selected
        self.provider_ids = [str(item["provider_id"]) for item in selected]
        self.max_price = f"{max(item['price'] for item in selected):.3f}"
        if self.selection_mode == "auto":
            selected_text = f"automatique ({len(selected)} offres, de {selected[0]['price']:.3f} à {self.max_price} $)"
        elif len(selected) == 1:
            selected_text = f"provider={selected[0]['provider_id']}, prix={selected[0]['price']:.3f} $"
        else:
            selected_text = f"{len(selected)} fournisseurs jusqu'à {self.max_price} $"
        ok(f"Offre SMSBower sélectionnée : {selected_text}.")
        return True

    def get_price_info(self) -> tuple[Any, Any]:
        if self.selected_offers:
            offers = self.get_offers(max_price=SMSBOWER_OFFER_MAX_PRICE)
            selected_ids = {str(item["provider_id"]) for item in self.selected_offers}
            selected = [item for item in offers if str(item["provider_id"]) in selected_ids]
            if selected:
                return min(item["price"] for item in selected), sum(item["count"] for item in selected)
        raw = self._request("getPrices", service=SMSBOWER_SERVICE, country=SMSBOWER_COUNTRY)
        data = _json_response(raw)
        if not isinstance(data, dict):
            return None, None
        country = data.get(SMSBOWER_COUNTRY) or data.get(str(SMSBOWER_COUNTRY))
        service = country.get(SMSBOWER_SERVICE) if isinstance(country, dict) else None
        if not isinstance(service, dict):
            return None, None
        return service.get("cost", service.get("price")), service.get("count")

    def buy_number(self) -> dict[str, Any] | None:
        listed_price, stock = self.get_price_info()
        if listed_price is not None or stock is not None:
            info(f"SMSBower prix actuel : {_money(listed_price)} | stock : {stock if stock is not None else 'inconnu'}")
        else:
            info("SMSBower : prix détaillé illisible, plafond serveur appliqué.")

        info(f"SMSBower : achat automatique d'un numéro (service={SMSBOWER_SERVICE}, pays={SMSBOWER_COUNTRY}, max={self.max_price} $)...")
        candidates = self.selected_offers if self.selection_mode == "auto" else [None]
        last_raw: str | None = None

        for candidate in candidates:
            candidate_price = f"{float(candidate['price']):.3f}" if candidate is not None else self.max_price
            params = {
                "service": SMSBOWER_SERVICE,
                "country": SMSBOWER_COUNTRY,
                "maxPrice": candidate_price,
            }
            if candidate is not None:
                provider_id = str(candidate["provider_id"])
                params["providerIds"] = provider_id
                info(f"Essai offre {provider_id} : {candidate['price']:.3f} $ (stock={candidate['count']})")
            elif self.provider_ids:
                params["providerIds"] = ",".join(self.provider_ids)
                info(f"SMSBower fournisseurs utilisés : {','.join(self.provider_ids)}")

            raw = self._request("getNumberV2", **params)
            number = self._parse_number(raw)
            if number is None and raw in {"BAD_ACTION", "BAD_METHOD", "NOT_IMPLEMENTED"}:
                raw = self._request("getNumber", **params)
                number = self._parse_number(raw)
            last_raw = raw
            if number is None:
                if candidate is not None:
                    warning(f"Offre indisponible ({candidate['provider_id']}), passage à la suivante...")
                    continue
                break

            if number.get("price") is None:
                number["price"] = candidate.get("price") if candidate else listed_price
            ok(f"SMSBower numéro : {_display_phone(number['phone'])} | activation {number['activation_id']} | prix {_money(number.get('price'))}")
            return number

        self._print_api_error(last_raw)
        return None

    @staticmethod
    def _parse_number(raw: str | None) -> dict[str, Any] | None:
        data = _json_response(raw)
        if isinstance(data, dict):
            aid = data.get("activationId")
            phone = data.get("phoneNumber")
            if aid is not None and phone is not None:
                return {
                    "activation_id": str(aid),
                    "phone": str(phone),
                    "price": data.get("activationCost"),
                    "raw": data,
                }
            return None
        if raw and raw.startswith("ACCESS_NUMBER:"):
            parts = raw.split(":", 2)
            if len(parts) == 3:
                return {
                    "activation_id": parts[1],
                    "phone": parts[2],
                    "price": None,
                    "raw": raw,
                }
        return None

    @staticmethod
    def _print_api_error(raw: str | None) -> None:
        messages = {
            "NO_NUMBERS": "Aucun numéro disponible à ce prix.",
            "NO_BALANCE": "Solde SMSBower insuffisant.",
            "BAD_KEY": "Clé API SMSBower invalide.",
            "BAD_SERVICE": "Service Instagram invalide.",
            "BAD_COUNTRY": "Pays invalide.",
        }
        error(f"SMSBower : {messages.get(raw, raw or 'réponse vide')}")

    def wait_for_code(self, activation_id: str, max_seconds: int = 90, interval: int = 3) -> str | None:
        deadline = time.monotonic() + max_seconds
        attempt = 0
        last_retry_status: str | None = None
        while time.monotonic() < deadline:
            attempt += 1
            raw = self._request("getStatus", id=activation_id)
            if raw is None:
                print(f"    [{attempt:02d}] réseau indisponible, retry...")
            elif raw.startswith("STATUS_OK:"):
                code = raw.split(":", 1)[1].strip()
                self._received_codes.add(str(activation_id))
                ok(f"Code SMSBower reçu : {code}")
                return code
            elif raw == "STATUS_WAIT_CODE":
                print(f"    [{attempt:02d}] en attente du SMS...")
            elif raw.startswith("STATUS_WAIT_RETRY:"):
                old = raw.split(":", 1)[1]
                if raw != last_retry_status:
                    print(f"    nouveau SMS en attente (ancien: {old})...")
                    last_retry_status = raw
            elif raw == "STATUS_CANCEL":
                warning("Activation annulée côté SMSBower.")
                return None
            elif raw in ("NO_ACTIVATION", "BAD_STATUS"):
                warning(f"Activation invalide/expirée : {raw}")
                return None
            else:
                print(f"    [{attempt:02d}] statut : {raw}")
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        warning(f"Délai {max_seconds}s atteint, aucun code reçu.")
        return None

    def wait_for_code_or_resend(self, activation_id: str, resend_after: int = 30, allow_sms_resend: bool = True) -> str | None:
        code = self.wait_for_code(activation_id, max_seconds=resend_after)
        if code:
            return code
        if allow_sms_resend:
            info(f"Aucun code depuis {resend_after}s : demande de renvoi Instagram (R) et SMSBower (statut 3)...")
            if not self.request_new_sms(activation_id, report_error=False):
                warning("SMSBower n'accepte pas encore le renvoi; poursuite avec R Instagram.")
        else:
            info(f"Premier SMS absent après {resend_after}s : renvoi Instagram (R) uniquement; le statut SMSBower 3 est volontairement ignoré.")
        return "R"

    def request_new_sms(self, activation_id: str, report_error: bool = True) -> bool:
        raw = self._request("setStatus", id=activation_id, status=3)
        if raw in ("ACCESS_RETRY_GET", "ACCESS_READY"):
            ok("SMSBower : nouveau SMS demandé (statut 3).")
            return True
        if report_error:
            self._print_api_error(raw)
        return False

    def complete_activation(self, activation_id: str) -> bool:
        raw = self._request("setStatus", id=activation_id, status=6)
        if raw == "ACCESS_ACTIVATION":
            ok("SMSBower : activation terminée (statut 6).")
            return True
        self._print_api_error(raw)
        return False

    def cancel_activation(self, activation_id: str) -> bool:
        raw = self._request("setStatus", id=activation_id, status=8)
        if raw == "ACCESS_CANCEL":
            ok("SMSBower : activation annulée (statut 8).")
            return True
        self._print_api_error(raw)
        return False


# ===========================================================================
# --- NOUVEAU : Fonctions utilitaires WhatsApp (client HTTP vers wa.js) ---
# ===========================================================================
def wa_health() -> bool:
    """Vérifie si le serveur WhatsApp est démarré et répond."""
    try:
        r = requests.get(f"{WA_SERVICE_URL}/health", timeout=5)
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False


def wa_get_accounts() -> list[dict]:
    """Récupère la liste des comptes WhatsApp depuis le serveur."""
    try:
        r = requests.get(f"{WA_SERVICE_URL}/accounts", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return []


def wa_add_account(phone: str) -> dict:
    """Compatibilité : vérifie un compte déjà présent côté serveur."""
    try:
        r = requests.post(f"{WA_SERVICE_URL}/add-account", json={"phone": phone}, timeout=10)
        return r.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def wa_register_account(phone: str, account_name: str, method: str = "wa_old") -> dict:
    """Enregistre directement via le flux wa_old, sans QR, pairing ni choix de canal."""
    try:
        r = requests.post(
            f"{WA_SERVICE_URL}/register-account/start",
            json={"phone": phone, "name": account_name, "method": method,
                  "displayName": account_name}, timeout=90
        )
        data = r.json()
        if not data.get("success"):
            return data
        if data.get("registered"):
            return data
        while True:
            code = ask(f"Code WhatsApp reçu pour +{data.get('phone', phone)} (R = renvoyer)").strip()
            if code.upper() in {"R", "RESEND", "RENVOYER"}:
                r = requests.post(
                    f"{WA_SERVICE_URL}/register-account/start",
                    json={"phone": phone, "name": account_name, "method": method,
                          "displayName": account_name}, timeout=90
                )
                data = r.json()
                if data.get("success"):
                    info("Nouveau code WhatsApp demandé.")
                else:
                    error(data.get("error", "Échec du renvoi WhatsApp."))
                continue
            r = requests.post(
                f"{WA_SERVICE_URL}/register-account/verify",
                json={"name": account_name, "code": code}, timeout=90
            )
            data = r.json()
            if data.get("success"):
                return data
            error(data.get("error", "Code WhatsApp refusé."))
            warning("Le code reste attendu ; saisis un nouveau code ou R pour renvoyer.")
    except requests.RequestException as e:
        return {"error": str(e)}


def wa_listen_account(account_name: str) -> bool:
    """Demande au serveur de démarrer l'écoute d'un compte."""
    try:
        r = requests.post(f"{WA_SERVICE_URL}/listen/{account_name}", timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False


def wa_get_otp(account_name: str, timeout: int = 90) -> str | None:
    """Attend un OTP depuis le buffer du serveur (polling)."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            r = requests.get(f"{WA_SERVICE_URL}/otp/{account_name}", timeout=5)
            r.raise_for_status()
            data = r.json()
            if data.get("otps"):
                return data["otps"][0]
        except requests.RequestException:
            pass
        time.sleep(2)
    return None


def wa_delete_account(account_name: str) -> bool:
    """Supprime un compte WhatsApp via l'API."""
    try:
        r = requests.delete(f"{WA_SERVICE_URL}/accounts/{account_name}", timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False


def _wa_phone_key(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or "")) or str(phone or "")


def wa_load_creation_stats() -> dict[str, int]:
    """Charge le nombre total de comptes créés par numéro WhatsApp."""
    try:
        raw = json.loads(WHATSAPP_CREATION_STATS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): int(value) for key, value in raw.items() if str(value).isdigit()}


def wa_save_creation_stats(stats: dict[str, int]) -> None:
    WHATSAPP_CREATION_STATS_FILE.write_text(
        json.dumps({str(k): int(v) for k, v in stats.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def wa_creation_count(phone: str, stats: dict[str, int] | None = None) -> int:
    values = stats if stats is not None else wa_load_creation_stats()
    return int(values.get(_wa_phone_key(phone), 0))


def wa_archived_sessions() -> list[Path]:
    archive_root = ROOT / "WS_INUTILISABLE"
    if not archive_root.is_dir():
        return []
    return sorted((item for item in archive_root.iterdir() if item.is_dir()), key=lambda p: p.name.lower())


def wa_archived_account_name(folder_name: str, known_names: list[str] | None = None) -> str:
    """Retrouve le nom DB d'un dossier archivé, y compris après collision."""
    known_names = known_names or []
    if folder_name in known_names:
        return folder_name
    matches = [name for name in known_names if folder_name.startswith(f"{name}_")]
    if matches:
        return max(matches, key=len)
    match = re.match(r"^(.*)_\d{10,}$", folder_name)
    return match.group(1) if match else folder_name


def wa_mark_session_unusable(account_name: str) -> Path | None:
    """Déplace la session WhatsApp dont Instagram a refusé le canal.

    Le compte reste enregistré dans le serveur WhatsApp afin de ne pas
    supprimer ses métadonnées, mais son dossier de session est isolé dans
    ``WS_INUTILISABLE`` pour empêcher sa réutilisation accidentelle.
    """
    source = ROOT / "accounts" / str(account_name)
    archive_root = ROOT / "WS_INUTILISABLE"
    if not source.is_dir():
        return None
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / source.name
    if destination.exists():
        destination = archive_root / f"{source.name}_{int(time.time())}"
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        warning(f"Session WhatsApp {account_name} non déplacée : {exc}")
        return None
    return destination


def wa_session_is_unusable(account_name: str) -> bool:
    """Retourne vrai si une session a déjà été classée comme SMS/inutilisable."""
    archive_root = ROOT / "WS_INUTILISABLE"
    if not archive_root.is_dir():
        return False
    name = str(account_name)
    return any(
        item.name == name or item.name.startswith(f"{name}_")
        for item in archive_root.iterdir()
    )


def wa_ensure_server() -> bool:
    global _WA_SERVER_PROCESS
    """Vérifie que le serveur est démarré ; sinon, affiche un message clair."""
    if wa_health():
        info("Serveur WhatsApp connecté.")
        return True
    parsed = urlparse(WA_SERVICE_URL)
    wa_file = ROOT / "wa.js"
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"} and wa_file.is_file() and (
        _WA_SERVER_PROCESS is None or _WA_SERVER_PROCESS.poll() is not None
    ):
        try:
            _WA_SERVER_PROCESS = subprocess.Popen(
                ["node", str(wa_file)], cwd=str(ROOT), stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            for _ in range(20):
                time.sleep(0.25)
                if wa_health():
                    info("Serveur WhatsApp local démarré automatiquement.")
                    return True
        except OSError as exc:
            error(f"Impossible de démarrer wa.js automatiquement : {exc}")
    error(f"Serveur WhatsApp injoignable sur {WA_SERVICE_URL}.")
    warning("Démarrez d'abord le serveur en lançant : node wa.js")
    warning("Ou définissez la variable d'environnement WA_SERVICE_URL si le serveur est ailleurs.")
    return False


# ===========================================================================
# Création manuelle (mode 1) — INCHANGÉE
# ===========================================================================
def create_account(
    proxy: str | bool | None,
    proxy_mode: str,
    proxy_country: str = "any",
    proxy_retries: int = 3,
) -> dict:
    print(f"\n{MAGENTA}{BOLD}┌─ CRÉATION INSTAGRAM WebCAA {'─' * 42}┐{RESET}")
    contact = ask("E-mail ou numéro de téléphone")
    if not contact:
        error("Un e-mail ou un numéro est obligatoire.")
        return {"success": False, "error": "Contact manquant."}

    if proxy is True:
        info("Proxy Capsolver sélectionné.")
    elif isinstance(proxy, str):
        info("Proxy personnalisé sélectionné.")
    else:
        info("Connexion directe sélectionnée.")
    if proxy is not None:
        scope_labels = {
            "all": "toute la session",
            "pre_final": "jusqu'à la soumission du code, puis direct",
            "code": "envoi/renvoi uniquement",
            "final": "soumission finale uniquement",
            "after_code": "direct jusqu'au code reçu, puis proxy pour la suite",
            "before_code": "proxy jusqu'au code reçu, puis direct pour la suite",
        }
        info(f"Portée : {scope_labels.get(proxy_mode, proxy_mode)}.")

    _first, _last, full_name, generated_username = generate_identity()
    username = ask(f"Username (vide = généré: @{generated_username})", generated_username)
    password = generate_password()
    day, month, year = generate_birthday()

    print(f"\n{BLUE}{BOLD}┌─ NOUVEAU COMPTE {'─' * 50}┐{RESET}")
    print(f"  {BOLD}Nom        :{RESET} {full_name}")
    print(f"  {BOLD}Username   :{RESET} @{username}")
    print(f"  {BOLD}Mot de passe:{RESET} {password}")
    print(f"  {BOLD}Naissance  :{RESET} {year:04d}-{month:02d}-{day:02d}")

    spinner = Spinner("Création et confirmation Instagram...")
    creator = InstagramSignup(verbose=False)
    account_proxy = proxy_for_account(proxy, proxy_country)

    channel_announced = False

    def confirmation_code_callback(address: str) -> str:
        nonlocal channel_announced
        spinner.stop()
        client = getattr(creator, "_client", None)
        if not channel_announced:
            medium = str(getattr(client, "confirmation_medium", "") or "").strip().lower()
            medium_label = {
                "sms": "SMS", "whatsapp": "WhatsApp", "email": "e-mail", "flash_call": "appel flash",
            }.get(medium, "canal de confirmation")
            info(f"Code envoyé par {medium_label} au {address}.")
            channel_announced = True
        if client is not None:
            resend_state = getattr(client, "last_resend_ok", None)
            if resend_state is True:
                ok("Renvoi accepté : un nouveau code devrait arriver.")
            elif resend_state is False:
                error(f"Renvoi échoué : {getattr(client, 'last_resend_error', '') or 'raison inconnue'}.")
            confirmation_state = getattr(client, "last_confirmation_ok", None)
            if confirmation_state is False:
                error(f"Code incorrect ou expiré : {getattr(client, 'last_confirmation_error', '') or 'Instagram l’a refusé'}.")
            client.last_resend_ok = None
            client.last_resend_error = ""
            client.last_confirmation_ok = None
            client.last_confirmation_error = ""
        code = ask(f"Code reçu pour {address} (R = renvoyer)")
        if code.upper() in {"R", "RESEND", "RENVOYER"}:
            info("Demande de renvoi envoyée...")
        spinner.start("Renvoi du code..." if code.upper() in {"R", "RESEND", "RENVOYER"} else "Vérification du code...")
        return code

    def contactpoint_retry_callback(current_phone: str) -> str:
        spinner.stop()
        error(f"SMS non envoyé pour +{current_phone}.")
        replacement = ask("Nouveau numéro international (vide = arrêter cette inscription)")
        spinner.start("Nouvel envoi SMS...")
        return replacement

    spinner.start()
    try:
        result = creator.signup(
            email=contact,
            username=username,
            password=password,
            full_name=full_name,
            birthday_day=day,
            birthday_month=month,
            birthday_year=year,
            verification_code_callback=confirmation_code_callback,
            contactpoint_retry_callback=(
                contactpoint_retry_callback
                if re.sub(r"[\s().-]", "", str(contact)).lstrip("+").isdigit()
                else None
            ),
            max_code_attempts=3,
            save_to_file=False,
            proxy=account_proxy,
            proxy_mode=proxy_mode,
            proxy_country=proxy_country,
            proxy_retries=proxy_retries,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"success": False, "error": str(exc), "username": username}
    finally:
        spinner.stop()

    if not result.get("success"):
        error(f"Inscription : {result.get('error') or 'échec inconnu'}")
        return result

    try:
        path = save_account(result, password)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        error(f"Compte créé, mais sauvegarde impossible : {exc}")
        return {**result, "save_error": str(exc)}

    result["path"] = str(path)
    if result.get("_verification_status") != "LIVE":
        warning("Compte créé mais classé dans COMPTE_PROBLEME.")
        return result
    ok("Compte créé et confirmé.")
    info(f"JSON sauvegardé : {path}")
    if ask("Créer un profil Instagram mobile secondaire SPC avec cette session ? (o/n)", "n").lower() in {"o", "oui", "y", "yes"}:
        chain = create_spc_chain(result, password, limit=4)
        result["spc_created_paths"] = chain["created_paths"]
        if len(chain["created_paths"]) == 4:
            ok("Chaîne SPC terminée : 4 profils créés.")
        else:
            warning(f"Chaîne SPC arrêtée après {len(chain['created_paths'])} profil(s).")
    return result


# ===========================================================================
# MODE AUTOMATIQUE (SMSBower) — INCHANGÉ
# ===========================================================================
def _create_one_auto_account(
    client: SMSBowerClient,
    phone: str,
    activation_id: str,
    proxy: str | bool | None,
    proxy_mode: str,
    proxy_country: str,
    proxy_retries: int,
    first_use: bool,
    spinner: Spinner,
    allow_sms_resend: bool | None = None,
) -> dict:
    """Crée un compte Instagram en utilisant un numéro SMSBower déjà acheté."""
    _first, _last, full_name, generated_username = generate_identity()
    username = generated_username
    password = generate_password()
    day, month, year = generate_birthday()

    print(f"\n{BLUE}{BOLD}┌─ COMPTE AUTO @{username} {'─' * 30}┐{RESET}")
    print(f"  {BOLD}Nom        :{RESET} {full_name}")
    print(f"  {BOLD}Numéro     :{RESET} {_display_phone(phone)}")
    print(f"  {BOLD}Mot de passe:{RESET} {password}")
    print(f"  {BOLD}Naissance  :{RESET} {year:04d}-{month:02d}-{day:02d}")

    creator = InstagramSignup(verbose=False)
    account_proxy = proxy_for_account(proxy, proxy_country)
    resend_count = 0
    max_instagram_resends = 2
    sms_wait_cycles = 0

    def fetch_code_once() -> str | None:
        nonlocal resend_count, sms_wait_cycles
        sms_wait_cycles += 1
        code = client.wait_for_code(activation_id, max_seconds=30)
        if code:
            return code
        if resend_count >= max_instagram_resends:
            warning(f"Aucun code après {max_instagram_resends} renvois Instagram : échec de cette activation.")
            return ""

        resend_count += 1
        can_resend_sms = (not first_use) if allow_sms_resend is None else allow_sms_resend
        if can_resend_sms:
            info(f"Aucun code depuis 30s : renvoi Instagram R ({resend_count}/{max_instagram_resends}) et SMSBower statut 3...")
            if not client.request_new_sms(activation_id, report_error=False):
                warning("SMSBower refuse ce renvoi; le renvoi Instagram continue.")
        else:
            info(f"Premier SMS absent après 30s : renvoi Instagram R ({resend_count}/{max_instagram_resends}) uniquement.")
        return "R"

    channel_announced = False

    def confirmation_code_callback(address: str) -> str:
        nonlocal channel_announced
        spinner.stop()
        client_web = getattr(creator, "_client", None)
        if not channel_announced:
            medium = str(getattr(client_web, "confirmation_medium", "") or "").strip().lower()
            medium_label = {"sms": "SMS", "whatsapp": "WhatsApp", "email": "e-mail", "flash_call": "appel flash"}.get(medium, "canal de confirmation")
            info(f"Code reçu par {medium_label} au {address}.")
            channel_announced = True
        code = fetch_code_once()
        if not code:
            spinner.start("Attente SMS expirée, abandon de ce compte...")
            raise RuntimeError(f"Aucun code SMS après {max_instagram_resends} renvois Instagram.")
        spinner.start("Vérification du code Instagram...")
        return code

    spinner.start("Soumission du numéro à Instagram...")
    try:
        result = creator.signup(
            email=_display_phone(phone),
            username=username,
            password=password,
            full_name=full_name,
            birthday_day=day,
            birthday_month=month,
            birthday_year=year,
            verification_code_callback=confirmation_code_callback,
            contactpoint_retry_callback=None,
            max_code_attempts=3,
            save_to_file=False,
            proxy=account_proxy,
            proxy_mode=proxy_mode,
            proxy_country=proxy_country,
            proxy_retries=proxy_retries,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"success": False, "error": str(exc), "username": username}
    finally:
        spinner.stop()

    if not result.get("success"):
        result["instagram_resends"] = resend_count
        result["sms_wait_cycles"] = sms_wait_cycles
        error(f"Compte @{username} : {result.get('error') or 'échec inconnu'}")
        return result

    try:
        path = save_account(result, password)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        error(f"Compte créé, mais sauvegarde impossible : {exc}")
        return {**result, "save_error": str(exc)}

    result["path"] = str(path)
    if result.get("_verification_status") != "LIVE":
        warning(f"Compte @{result.get('username', username)} classé dans COMPTE_PROBLEME.")
        return result
    ok(f"Compte @{result.get('username', username)} créé et confirmé.")
    info(f"JSON sauvegardé : {path}")
    return result


def auto_create_accounts(
    proxy: str | bool | None,
    proxy_mode: str,
    proxy_country: str = "any",
    proxy_retries: int = 3,
) -> None:
    """Boucle 100% automatique avec SMSBower."""
    print(f"\n{MAGENTA}{BOLD}╔{'═' * 58}╗")
    print(f"║{' MODE AUTOMATIQUE — SMSBower Instagram ':^58}║")
    print(f"╚{'═' * 58}╝{RESET}")
    info(f"Proxy sélectionné : {'Capsolver' if proxy is True else (proxy if isinstance(proxy, str) else 'direct')} (pays={proxy_country}, rotation par compte, portée={proxy_mode})")
    info(f"SMSBower : service={SMSBOWER_SERVICE}, pays={SMSBOWER_COUNTRY}, offres jusqu'à {SMSBOWER_OFFER_MAX_PRICE:.2f} $, {4} comptes par numéro.")

    client = SMSBowerClient.load_or_ask_key()
    if not client:
        warning("Aucune clé SMSBower : abandon du mode automatique.")
        return
    if not client.choose_offers():
        warning("Aucune offre SMSBower utilisable : abandon du mode automatique.")
        return

    spinner = Spinner("Préparation...")
    total_success = 0
    total_numbers = 0
    total_attempts = 0

    try:
        while True:
            total_numbers += 1
            print(f"\n{CYAN}{BOLD}══ Numéro #{total_numbers} ══{RESET}")
            number = client.buy_number()
            if not number:
                warning("Achat impossible (stock/prix/solde). Fin du mode automatique.")
                break

            activation_id = str(number["activation_id"])
            phone = str(number["phone"])
            success_this_number = 0

            for use_index in range(1, 5):
                print(f"\n{YELLOW}{BOLD}→ Utilisation {use_index}/4 du numéro {_display_phone(phone)}{RESET}")

                if use_index > 1:
                    if not client.request_new_sms(activation_id):
                        warning("Nouveau SMS refusé pour l'instant; les 3 tentatives sur le même numéro vont quand même être essayées.")

                result: dict[str, Any] = {"success": False}
                use_succeeded = False
                first_use_no_code = False
                for retry_index in range(1, 4):
                    if retry_index > 1:
                        if use_index == 1 and not client.has_received_code(activation_id):
                            first_use_no_code = True
                            if result.get("sms_wait_cycles", 0) >= 3:
                                warning("Le premier numéro n'a reçu aucun code après les 2 renvois : abandon immédiat de ce numéro.")
                            else:
                                warning(f"Instagram n'a pas ouvert/confirmé l'envoi SMS (cycles d'attente={result.get('sms_wait_cycles', 0)}; abandon immédiat de ce numéro).")
                            break
                        info(f"Retry utilisation {use_index}/4 : tentative {retry_index}/3 avec une nouvelle identité et une nouvelle session...")
                        if client.has_received_code(activation_id):
                            client.request_new_sms(activation_id, report_error=False)

                    result = _create_one_auto_account(
                        client=client,
                        phone=phone,
                        activation_id=activation_id,
                        proxy=proxy,
                        proxy_mode=proxy_mode,
                        proxy_country=proxy_country,
                        proxy_retries=proxy_retries,
                        first_use=(use_index == 1),
                        allow_sms_resend=(use_index > 1 or client.has_received_code(activation_id)),
                        spinner=spinner,
                    )
                    total_attempts += 1
                    if result.get("success"):
                        use_succeeded = True
                        break
                    if result.get("proxy_error"):
                        warning(f"Échec de prévalidation proxy, tentative {retry_index}/3 : {result.get('error')}")
                        if retry_index < 3:
                            continue
                        break
                    warning(f"Échec utilisation {use_index}/4, tentative {retry_index}/3 : {result.get('error') or 'erreur inconnue'}")

                if not use_succeeded:
                    if not first_use_no_code:
                        warning(f"Échec utilisation {use_index}/4 après 3 tentatives. Passage au numéro suivant.")
                    break

                success_this_number += 1
                total_success += 1

            if success_this_number == 4:
                ok(f"Numéro {_display_phone(phone)} : 4/4 comptes créés.")
                client.complete_activation(activation_id)
            elif success_this_number == 0:
                warning(f"Numéro {_display_phone(phone)} : 0 succès, annulation.")
                if not client.cancel_activation(activation_id):
                    warning("SMSBower n'a pas accepté le statut 8 (activation déjà consommée ou état invalide); tentative de clôture par statut 6.")
                    client.complete_activation(activation_id)
            else:
                warning(f"Numéro {_display_phone(phone)} : {success_this_number}/4 succès, clôture de l'activation.")
                client.complete_activation(activation_id)

            print(f"\n{CYAN}Bilan : {total_success} compte(s) créé(s) sur {total_numbers} numéro(s), {total_attempts} tentative(s).{RESET}")
            info("Passage automatique au prochain numéro SMSBower...")
    except KeyboardInterrupt:
        warning("Interruption utilisateur : clôture.")
    finally:
        spinner.stop()
        print(f"\n{GREEN}{BOLD}══ MODE AUTOMATIQUE TERMINÉ ══{RESET}")
        print(f"  Comptes réussis : {total_success}")
        print(f"  Numéros achetés : {total_numbers}")
        print(f"  Tentatives      : {total_attempts}")


# ===========================================================================
# --- NOUVEAU : Mode 3 : Automatique via WhatsApp ---
# ===========================================================================
def auto_create_via_whatsapp(
    proxy: str | bool | None = None,
    proxy_mode: str = "none",
    proxy_country: str = "any",
    proxy_retries: int = 1,
) -> None:
    """Mode 3 : utilise un compte WhatsApp existant pour recevoir l'OTP Instagram.

    - Vérifie que le serveur wa.js est démarré.
    - Récupère la liste des comptes WhatsApp enregistrés côté serveur.
    - Pour chaque compte, essaie l'inscription Instagram :
        * si Instagram confirme l'envoi via WhatsApp -> attend l'OTP puis valide ;
        * si Instagram affiche « envoyer par SMS » -> abandon et passage au compte suivant ;
        * si aucun OTP n'arrive -> passage au compte suivant.
    - Dès qu'un compte réussit, on s'arrête.
    """
    if not wa_ensure_server():
        return

    accounts = wa_get_accounts()
    if not accounts:
        warning("Aucun compte WhatsApp enregistré sur le serveur.")
        warning("Ajoutez-en un via le menu de gestion des comptes WhatsApp (option 4).")
        return

    print(f"\n{CYAN}{BOLD}Comptes WhatsApp disponibles :{RESET}")
    for i, acc in enumerate(accounts, 1):
        status = "connecté" if acc.get("connected") else "déconnecté"
        print(f"  [{i}] {acc['name']} — +{acc['phone']} ({acc['mode']}, {status})")

    account_index = 0
    failed_accounts: set[str] = set()
    unusable_accounts: set[str] = set()
    success_counts: dict[str, int] = {}
    success_limits: dict[str, int] = {}
    cooldown_until: dict[str, float] = {}
    total_creation_stats = wa_load_creation_stats()

    def account_key(phone: str) -> str:
        return _wa_phone_key(phone)

    def cooldown_left(key: str) -> float:
        until = cooldown_until.get(key, 0.0)
        remaining = until - time.monotonic()
        if remaining <= 0:
            cooldown_until.pop(key, None)
            success_counts[key] = 0
            success_limits[key] = random.randint(4, 7)
            return 0.0
        return remaining

    def mark_success(phone: str) -> float | None:
        """Enregistre une réussite et déclenche un repos de 90 à 160 s."""
        key = account_key(phone)
        total_creation_stats[key] = total_creation_stats.get(key, 0) + 1
        try:
            wa_save_creation_stats(total_creation_stats)
        except OSError as exc:
            warning(f"Compteur WhatsApp non sauvegardé : {exc}")
        success_limits.setdefault(key, random.randint(4, 7))
        success_counts[key] = success_counts.get(key, 0) + 1
        if success_counts[key] < success_limits[key]:
            return None
        delay = random.uniform(90.0, 160.0)
        cooldown_until[key] = time.monotonic() + delay
        return delay

    def select_next_account() -> dict | None:
        """Choisit un numéro non isolé et hors période de repos."""
        nonlocal account_index
        now_available = []
        for offset in range(len(accounts)):
            index = (account_index + offset) % len(accounts)
            candidate = accounts[index]
            name = str(candidate.get("name") or "")
            key = account_key(candidate.get("phone", ""))
            if not name or name in unusable_accounts or wa_session_is_unusable(name):
                continue
            if cooldown_left(key) <= 0:
                account_index = (index + 1) % len(accounts)
                return candidate
            now_available.append((key, cooldown_left(key)))
        if now_available:
            wait_for = min(remaining for _, remaining in now_available)
            warning(f"Tous les numéros sont temporairement en pause. Reprise dans {wait_for:.0f} s.")
            time.sleep(max(0.0, wait_for))
            return select_next_account()
        return None

    def move_to_next_whatsapp(account_name: str) -> None:
        failed_accounts.add(account_name)
        if len(failed_accounts) >= len(accounts):
            warning("Tous les comptes WhatsApp ont échoué ; nouvelle rotation immédiate.")
            failed_accounts.clear()

    try:
      while True:
        acc = select_next_account()
        if acc is None:
            warning("Aucun compte WhatsApp utilisable.")
            return
        account_name = acc["name"]
        phone = acc["phone"]
        if not phone:
            warning(f"Compte {account_name} sans numéro, ignoré.")
            unusable_accounts.add(account_name)
            move_to_next_whatsapp(account_name)
            continue

        if not wa_listen_account(account_name):
            warning(f"Impossible d'écouter {account_name}, passage au suivant.")
            move_to_next_whatsapp(account_name)
            continue

        try:
            requests.get(f"{WA_SERVICE_URL}/otp/{account_name}", timeout=2)
        except Exception:
            pass

        _first, _last, full_name, generated_username = generate_identity()
        username = generated_username
        password = generate_password()
        day, month, year = generate_birthday()

        print(f"\n{BLUE}{BOLD}┌─ COMPTE VIA WHATSAPP @{username} {'─' * 30}┐{RESET}")
        print(f"  {BOLD}Numéro WhatsApp:{RESET} {_display_phone(phone)}")
        print(f"  {BOLD}Nom             :{RESET} {full_name}")
        print(f"  {BOLD}Mot de passe    :{RESET} {password}")
        print(f"  {BOLD}Naissance       :{RESET} {year:04d}-{month:02d}-{day:02d}")

        creator = InstagramSignup(verbose=False)
        account_proxy = proxy_for_account(proxy, proxy_country)
        spinner_holder = {"spinner": Spinner("Soumission du numéro à Instagram...")}
        otp_state: dict[str, str | None] = {"value": None}

        def confirmation_code_callback(address: str) -> str:
            spinner_holder["spinner"].stop()
            client_web = getattr(creator, "_client", None)
            medium = str(getattr(client_web, "confirmation_medium", "") or "").strip().lower()
            if medium != "whatsapp":
                if "sms" in medium or "text" in medium:
                    error("Instagram a envoyé le code par SMS")
                    raise RuntimeError("CANAL_SMS")
                raise RuntimeError("CANAL_INCONNU")
            if otp_state["value"]:
                spinner_holder["spinner"].start("Vérification du code Instagram...")
                return str(otp_state["value"])
            info(f"Code envoyé par WhatsApp au {_display_phone(phone)}")
            otp = wa_get_otp(account_name, timeout=90)
            if not otp:
                error("Aucun OTP reçu via WhatsApp dans le délai imparti.")
                raise RuntimeError("OTP_TIMEOUT")
            otp_state["value"] = otp
            ok(f"OTP récupéré depuis WhatsApp : {otp}")
            spinner_holder["spinner"].start("Vérification du code Instagram...")
            return otp

        spinner = spinner_holder["spinner"]
        spinner.start()
        try:
            result = creator.signup(
                email=_display_phone(phone),
                username=username,
                password=password,
                full_name=full_name,
                birthday_day=day,
                birthday_month=month,
                birthday_year=year,
                verification_code_callback=confirmation_code_callback,
                contactpoint_retry_callback=None,
                max_code_attempts=1,
                save_to_file=False,
                proxy=account_proxy,
                proxy_mode=proxy_mode,
                proxy_country=proxy_country,
                proxy_retries=proxy_retries,
            )
        except RuntimeError as e:
            spinner.stop()
            if str(e) == "CANAL_SMS":
                unusable_accounts.add(account_name)
                moved = wa_mark_session_unusable(account_name)
                if moved:
                    warning(f"Session {account_name} déplacée dans WS_INUTILISABLE (canal SMS).")
            elif str(e) == "CANAL_INCONNU":
                warning("Canal de vérification Instagram non identifié. Passage au compte suivant.")
            elif str(e) == "OTP_TIMEOUT":
                warning("Pas d'OTP reçu. Passage au compte suivant.")
            else:
                error(f"Erreur : {e}")
            move_to_next_whatsapp(account_name)
            continue
        except Exception as e:
            spinner.stop()
            error(f"Erreur inattendue : {e}")
            move_to_next_whatsapp(account_name)
            continue
        finally:
            spinner.stop()

        if result.get("success"):
            created_username = str(result.get("username") or username).strip()
            try:
                saved_path = save_account(result, password)
            except Exception as e:
                error(f"Compte créé mais sauvegarde impossible : {e}")
                move_to_next_whatsapp(account_name)
                continue
            if result.get("_verification_status") != "LIVE":
                warning(f"Compte @{created_username} classé dans COMPTE_PROBLEME.")
                move_to_next_whatsapp(account_name)
                continue
            print(f"Instagram créé : @{created_username}")
            cooldown = mark_success(phone)
            if cooldown is not None:
                warning(
                    f"{_display_phone(phone)} a atteint sa limite de créations "
                    f"({success_limits[account_key(phone)]}). Pause de {cooldown:.0f} s."
                )
            failed_accounts.clear()
            # Pause uniquement après une réussite, jamais après un échec.
            success_pause = random.uniform(15.0, 25.0)
            info(f"Pause avant le prochain compte : {success_pause:.0f} s.")
            time.sleep(success_pause)
            continue
        else:
            failure = str(result.get("error") or "échec inconnu")
            if "CANAL_SMS" in failure:
                unusable_accounts.add(account_name)
                moved = wa_mark_session_unusable(account_name)
                if moved:
                    warning(f"Session {account_name} déplacée dans WS_INUTILISABLE (canal SMS).")
            elif "OTP_TIMEOUT" in failure:
                warning("Aucun OTP WhatsApp reçu. Passage au compte suivant.")
            else:
                error(f"Échec : {failure}")
            move_to_next_whatsapp(account_name)

    except KeyboardInterrupt:
        warning("Mode WhatsApp interrompu par l'utilisateur.")


# ===========================================================================
# --- Gestion des sessions WhatsApp isolées ---
# ===========================================================================
def manage_unusable_whatsapp_sessions() -> None:
    """Liste, restaure ou supprime les sessions déplacées dans WS_INUTILISABLE."""
    archive_root = ROOT / "WS_INUTILISABLE"
    archive_root.mkdir(parents=True, exist_ok=True)
    known_accounts = wa_get_accounts()
    known_names = [str(item.get("name") or "") for item in known_accounts]

    while True:
        archived = wa_archived_sessions()
        stats = wa_load_creation_stats()
        print(f"\n{YELLOW}{BOLD}── Sessions WS_INUTILISABLE ──{RESET}")
        if not archived:
            info("Aucune session isolée.")
        else:
            phone_by_name = {
                str(item.get("name")): str(item.get("phone") or "")
                for item in known_accounts
            }
            for index, folder in enumerate(archived, 1):
                original = wa_archived_account_name(folder.name, known_names)
                phone = phone_by_name.get(original, "")
                created = wa_creation_count(phone, stats) if phone else 0
                suffix = f" +{phone}" if phone else ""
                print(f"  [{index}] {original}{suffix} — créations Instagram: {created}")

        print("  [1] Restaurer une session")
        print("  [2] Restaurer toutes les sessions")
        print("  [3] Supprimer définitivement une session")
        print("  [4] Supprimer définitivement toutes les sessions")
        print("  [0] Retour")
        choice = ask("Action", "0")
        archived = wa_archived_sessions()

        if choice == "0":
            return
        if choice in {"1", "3"}:
            if not archived:
                warning("Aucune session isolée.")
                continue
            try:
                index = int(ask("Numéro de la session à traiter", "1")) - 1
            except ValueError:
                warning("Choix invalide.")
                continue
            if not 0 <= index < len(archived):
                warning("Choix hors limites.")
                continue
            folder = archived[index]
            original = wa_archived_account_name(folder.name, known_names)
            if choice == "1":
                destination = ROOT / "accounts" / original
                if destination.exists():
                    # wa.js peut recréer le dossier pendant que le compte est
                    # encore présent dans sa base. Le compte est alors déjà
                    # utilisable : on retire simplement l'ancienne archive.
                    try:
                        shutil.rmtree(folder)
                        ok(f"Session {original} déjà disponible ; archive WS_INUTILISABLE supprimée.")
                    except OSError as exc:
                        error(f"Suppression de l'archive impossible : {exc}")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(folder), str(destination))
                    ok(f"Session {original} restaurée et de nouveau utilisable.")
                except OSError as exc:
                    error(f"Restauration impossible : {exc}")
            else:
                confirm = ask(f"Supprimer définitivement {original} ? (o/n)", "n").lower()
                if confirm not in {"o", "oui", "y", "yes"}:
                    continue
                try:
                    shutil.rmtree(folder)
                    # La suppression définitive retire aussi l'entrée du serveur.
                    if original and not wa_delete_account(original):
                        warning("Session supprimée, mais l'entrée serveur n'a pas pu être retirée.")
                    else:
                        ok(f"Session {original} supprimée définitivement.")
                except OSError as exc:
                    error(f"Suppression impossible : {exc}")
            continue
        if choice in {"2", "4"}:
            if not archived:
                warning("Aucune session isolée.")
                continue
            action = "restaurer" if choice == "2" else "supprimer définitivement"
            confirm = ask(f"Voulez-vous {action} les {len(archived)} sessions ? (o/n)", "n").lower()
            if confirm not in {"o", "oui", "y", "yes"}:
                continue
            done = 0
            for folder in archived:
                original = wa_archived_account_name(folder.name, known_names)
                try:
                    if choice == "2":
                        destination = ROOT / "accounts" / original
                        if destination.exists():
                            shutil.rmtree(folder)
                            done += 1
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(folder), str(destination))
                    else:
                        shutil.rmtree(folder)
                        if original:
                            wa_delete_account(original)
                    done += 1
                except OSError as exc:
                    warning(f"{folder.name} : {exc}")
            label = "restaurée(s)" if choice == "2" else "supprimée(s) définitivement"
            ok(f"{done} session(s) {label}.")
            continue
        warning("Choix invalide.")


# ===========================================================================
# --- NOUVEAU : Gestion des comptes WhatsApp (sous-menu) ---
# ===========================================================================
def manage_whatsapp_accounts() -> None:
    """Sous-menu : ajouter / lister / écouter / supprimer des comptes WhatsApp."""
    if not wa_ensure_server():
        return

    while True:
        print(f"\n{MAGENTA}{BOLD}── Gestion des comptes WhatsApp ──{RESET}")
        print("  [1] Enregistrer un compte par code WhatsApp (sans QR)")
        print("  [2] Lister les comptes")
        print("  [3] Écouter un compte (temps réel)")
        print("  [4] Supprimer un compte")
        print("  [5] Gérer les sessions WS_INUTILISABLE")
        print("  [0] Retour")
        choice = ask("Choix", "0")

        if choice == "1":
            phone = ask("Numéro de téléphone (format international)").strip()
            if not phone:
                warning("Numéro requis.")
                continue
            digits = re.sub(r"\D", "", phone)
            account_name = f"wa_{digits[-6:]}"
            info(f"Flux wa_old sélectionné automatiquement : {account_name}.")
            result = wa_register_account(phone, account_name, "wa_old")
            if result.get("success"):
                ok(f"Compte WhatsApp enregistré : {result.get('account')} (+{result.get('phone')})")
            else:
                error(result.get("error", "Erreur inconnue."))

        elif choice == "2":
            accounts = wa_get_accounts()
            stats = wa_load_creation_stats()
            if not accounts:
                info("Aucun compte enregistré.")
            else:
                print(f"\n{CYAN}Comptes WhatsApp :{RESET}")
                for acc in accounts:
                    status = "🟢 connecté" if acc.get("connected") else "⚪ déconnecté"
                    archived = " — WS_INUTILISABLE" if wa_session_is_unusable(acc["name"]) else ""
                    created = wa_creation_count(acc.get("phone", ""), stats)
                    print(
                        f"  • {acc['name']} — +{acc['phone']} ({acc['mode']}) {status}"
                        f" — créations Instagram: {created}{archived}"
                    )

        elif choice == "3":
            accounts = wa_get_accounts()
            if not accounts:
                warning("Aucun compte.")
                continue
            for i, acc in enumerate(accounts, 1):
                print(f"  [{i}] {acc['name']} (+{acc['phone']})")
            try:
                idx = int(ask("Numéro du compte à écouter", "1")) - 1
            except ValueError:
                warning("Choix invalide.")
                continue
            if 0 <= idx < len(accounts):
                acc_name = accounts[idx]["name"]
                if wa_listen_account(acc_name):
                    ok(f"Écoute démarrée pour {acc_name}. Les OTP s'affichent dans la console du serveur wa.js.")
                else:
                    error("Impossible de démarrer l'écoute.")
            else:
                warning("Choix hors limites.")

        elif choice == "4":
            accounts = wa_get_accounts()
            if not accounts:
                warning("Aucun compte.")
                continue
            for i, acc in enumerate(accounts, 1):
                print(f"  [{i}] {acc['name']} (+{acc['phone']})")
            try:
                idx = int(ask("Numéro du compte à supprimer", "1")) - 1
            except ValueError:
                warning("Choix invalide.")
                continue
            if 0 <= idx < len(accounts):
                acc_name = accounts[idx]["name"]
                if wa_delete_account(acc_name):
                    ok(f"Compte {acc_name} supprimé.")
                else:
                    error("Échec de la suppression.")
            else:
                warning("Choix hors limites.")

        elif choice == "5":
            manage_unusable_whatsapp_sessions()

        elif choice == "0":
            return
        else:
            warning("Choix invalide.")


# ===========================================================================
# Menu principal — MODIFIÉ (ajout des options 3 et 4)
# ===========================================================================
def print_main_menu() -> None:
    print(f"\n{BLUE}{BOLD}╔{'═' * 58}╗")
    print(f"║{' INSTAGRAM NEW — création simple WebCAA ':^58}║")
    print(f"╠{'═' * 58}╣")
    print(f"║  [1] Créer un compte (manuel){' ' * 30}║")
    print(f"║  [2] Mode automatique SMSBower (≤ 0,06 $){' ' * 16}║")
    print(f"║  [3] Mode automatique via WhatsApp{' ' * 22}║")
    print(f"║  [4] Gérer les comptes WhatsApp{' ' * 24}║")
    print(f"║  [5] Regrouper les comptes par lots de 50{' ' * 15}║")
    print(f"║  [6] Statistiques / vérifier un lot{' ' * 22}║")
    print(f"║  [7] Gérer le cookie de vérification{' ' * 21}║")
    print(f"║  [8] Vérifier un username Instagram{' ' * 23}║")
    print(f"║  [0] Quitter{' ' * 46}║")
    print(f"╚{'═' * 58}╝{RESET}")


def main() -> None:
    while True:
        print_main_menu()
        choice = ask("Choix", "1")
        if choice == "0":
            return
        if choice == "1":
            proxy, proxy_mode, proxy_country, proxy_retries = ask_proxy_settings()
            while True:
                result = create_account(proxy, proxy_mode, proxy_country, proxy_retries)
                print()
                if len(result.get("spc_created_paths") or []) == 4:
                    prompt = "Les 4 profils SPC sont terminés. Nouveau compte manuel maintenant ? (o/n)"
                else:
                    prompt = "Créer un autre compte avec la même connexion ? (o/n)"
                if ask(prompt, "o").lower() not in {"o", "oui", "y", "yes"}:
                    return
        elif choice == "2":
            proxy, proxy_mode, proxy_country, proxy_retries = ask_proxy_settings()
            auto_create_accounts(proxy, proxy_mode, proxy_country, proxy_retries)
            if ask("Revenir au menu ? (o/n)", "n").lower() in {"o", "oui", "y", "yes"}:
                continue
            return
        # --- NOUVEAU : option 3 - WhatsApp ---
        elif choice == "3":
            proxy, proxy_mode, proxy_country, proxy_retries = ask_proxy_settings()
            auto_create_via_whatsapp(proxy, proxy_mode, proxy_country, proxy_retries)
            continue
        # --- NOUVEAU : option 4 - Gestion WhatsApp ---
        elif choice == "4":
            manage_whatsapp_accounts()
            continue
        # --- MODIFIÉ : les anciennes options 3/4/5/6 deviennent 5/6/7/8 ---
        elif choice == "5":
            try:
                batches = organize_account_batches()
                if not batches:
                    count = len(list((ROOT / "COMPTE").glob("*.json")))
                    info(f"{count} compte(s) : aucun lot supplémentaire à créer (seuil 50).")
                else:
                    for batch in batches:
                        ok(f"Lot {batch['index']} : {batch['count']} compte(s) — {batch['folder']} et {batch['zip']}")
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                error(f"Organisation impossible : {exc}")
            continue
        elif choice == "6":
            show_account_statistics()
            continue
        elif choice == "7":
            manage_stats_cookie()
            continue
        elif choice == "8":
            show_single_account_status()
            continue
        else:
            warning("Choix invalide.")


if __name__ == "__main__":
    main()
