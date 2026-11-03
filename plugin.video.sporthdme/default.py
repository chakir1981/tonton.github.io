# -*- coding: utf-8 -*-
from __future__ import print_function
import xbmcvfs
import base64
import hashlib
import os
import re
import sys
import six
from six.moves.urllib.parse import urljoin, quote_plus, quote, unquote, parse_qsl, urlencode, urlparse
from datetime import datetime, timedelta
import json
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import requests
from resources.modules import control, client
from resources.modules import site_embedlivesports
from resources.modules import site_futbollibre
from resources.modules import site_futbolmov

# v3.7.6 — cache disque persistant (portage de la 3.7.4) : les caches en
# mémoire meurent à chaque navigation Kodi (nouveau processus) et chaque
# visite du menu re-téléchargeait la grille des matchs. fastcache garde la
# liste sur le disque du profil ; toute panne est silencieuse : l'add-on
# fonctionne alors exactement comme la 2.0.5, juste sans cache.
try:
    from resources.modules import fastcache as fastcache
except Exception:
    fastcache = None
import threading
import time
import unicodedata    # v3.7.27 — normalisation des noms (grilles FR bêta)
from dateutil.parser import parse
from dateutil.tz import gettz
from dateutil import parser, tz

# Extra sports sites: one self-contained module each. To add a site, drop a
# module exposing NAME/KEY/list_events()/resolve() and append it here.
# tvsport.guide is no longer a menu entry: it is the fallback schedule for
# ÉVÉNEMENTS EN DIRECT and Événements en direct (see Live_fallback_url).
EXTRA_SITES = [site_embedlivesports, site_futbollibre]
SITES = {s.KEY: s for s in EXTRA_SITES}
# futbol-libre.mov: one main-menu entry -> its three lists (fmov_menu)
SITES.update((s.KEY, s) for s in site_futbolmov.SUBSITES)

_url = sys.argv[0]
_handle = int(sys.argv[1])

ADDON = xbmcaddon.Addon()
ADDON_DATA = ADDON.getAddonInfo('profile')
ADDON_PATH = ADDON.getAddonInfo('path')
DESCRIPTION = ADDON.getAddonInfo('description')
FANART = ADDON.getAddonInfo('fanart')
ICON = ADDON.getAddonInfo('icon')
ID = ADDON.getAddonInfo('id')
NAME = ADDON.getAddonInfo('name')
VERSION = ADDON.getAddonInfo('version')
Lang = ADDON.getLocalizedString
Dialog = xbmcgui.Dialog()
vers = VERSION
ART = ADDON_PATH + "/resources/icons/"

BASEURL = 'https://one.sporthd.me/'  # 'https://sporthd.live/'  #'https://sportl.ivesoccer.sx/'
Live_url = 'https://league.st'  # super.league.st died Sep 2026; bare league.st is back
Live_fallback_url = 'https://tvsport.guide/proxy.php?source=index'  # same matches schema, JSON
headers = {'User-Agent': client.agent(),
           'Referer': BASEURL}

# v3.7.27 (B1 bêta) — liens personnalisés ÉVENTUELS publiés par le guide lui-même
# (matchstream/custom_links.php, tableau JSON — vide le 03/11/2026). Ce n'est PAS
# une nouvelle source : c'est le même guide, son propre mécanisme d'extension.
Custom_links_url = 'https://tvsport.guide/matchstream/custom_links.php'
# v3.7.27 (B6 bêta) — grille TV publique FR (XMLTV, information seule : AUCUN
# flux). Sert à écrire dans la fiche du match la chaîne FR officielle qui le
# diffuse (Canal+ Foot, Canal+ Live 1-19, beIN 1-3/MAX, L'Équipe…).
EPG_FR_URL = 'https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz'

# Kodi 19+ prefers LOGINFO; Kodi 18 still has LOGNOTICE — same idea: visible in kodi.log
# without turning on full "debug logging".
_LOG_INFO = getattr(xbmc, 'LOGINFO', None)
if _LOG_INFO is None:
    _LOG_INFO = getattr(xbmc, 'LOGNOTICE', xbmc.LOGDEBUG)


def _log_line(text):
    """Prefix addon name; safe str for xbmc.log on Py2/Py3."""
    try:
        line = u'[{0}] {1}'.format(NAME, text)
        return six.ensure_str(line, encoding='utf-8', errors='replace')
    except Exception:
        return six.ensure_str(text, encoding='utf-8', errors='replace')


def log_debug(msg):
    xbmc.log(_log_line(msg), xbmc.LOGDEBUG)


def log_info(msg):
    """Like old ``xbmc.log(msg)`` one-arg — shows in normal kodi.log."""
    xbmc.log(_log_line(msg), _LOG_INFO)


def log_warning(msg):
    xbmc.log(_log_line(msg), xbmc.LOGWARNING)


def log_error(msg):
    xbmc.log(_log_line(msg), xbmc.LOGERROR)


def Main_menu():
    # v2.0.5 — add-on recentré : seule la rubrique « ÉVÉNEMENTS EN DIRECT »
    # (affichée en blanc) est conservée au menu. Les rubriques bleues
    # « Événements en direct » (doublon embedlivesports), « Football Libre »
    # et « Football Libre MOV » ne sont plus proposées (les modules et les
    # modes du routeur restent en place, simplement sans entrée de menu).
    # v3.7.7 — préchauffage : la grille des matchs se télécharge en
    # arrière-plan dès l'écran d'accueil, pendant que l'utilisateur lit le
    # menu -> le premier clic sur « ÉVÉNEMENTS EN DIRECT » est instantané.
    _refresh_matches_bg(Live_url)
    # v3.7.8 — descriptions des entrées du menu (panneau d'information du skin)
    # v3.7.16 — « ÉVÉNEMENTS EN DIRECT » devient « MATCHS » (même contenu :
    # matchs en cours et à venir, grille du jour).
    addDir(u'[B][COLOR white]MATCHS[/COLOR][/B]', Live_url, 'events', ICON, FANART, True,
           infoLabels={'title': u'Matchs',
                       'plot': u'Matchs en cours et à venir — grille du jour, sources françaises en priorité.'})
    # addDir('[B][COLOR gold]Channels 24/7[/COLOR][/B]', 'https://1.livesoccer.sx/program.php', 14, ICON, FANART, '')
    # addDir('[B][COLOR gold]Alternative VIEW [/COLOR][/B]', '', '', ICON, FANART, '')
    # addDir('[B][COLOR white]SPORTS[/COLOR][/B]', '', 3, ICON, FANART, '')
    # addDir('[B][COLOR white]BEST LEAGUES[/COLOR][/B]', '', 2, ICON, FANART, '')
    addDir(u'[B][COLOR gold]Paramètres[/COLOR][/B]', 'set', 'settings', ICON, FANART, False,
           infoLabels={'title': u'Paramètres',
                       'plot': u'Uniquement les sources françaises, fuseau horaire.'})
    addDir(u"[B][COLOR gold]Vider les données de l'add-on[/COLOR][/B]", 'clear', 'clear', ICON, FANART, False,
           infoLabels={'title': u"Vider les données de l'add-on",
                       'plot': u'Supprime le cache (grille, lecteurs) et la base de chaînes locales.'})
    addDir(u'[B][COLOR gold]Version : [COLOR lime]{}[/COLOR][/B]'.format(vers), '', 'version', ICON, FANART, False,
           infoLabels={'title': u'Version ' + six.ensure_text(vers, encoding='utf-8', errors='replace'),
                       'plot': u'Vérifier les mises à jour des dépôts.'})
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _matches_from_page(url):
    data = requests.get(url, timeout=12).text
    data = six.ensure_text(data, encoding='utf-8', errors='ignore')
    data = re.sub('\t', '', data).replace('&nbsp', '')

    events = client.parseDOM(data, 'script')
    events = [i for i in events if '''matchDate''' in i][0]

    new_matches = re.findall(r'window\.matches\s*=\s*JSON\.parse\(`(\[.+?\])`\)', events, re.DOTALL)
    if new_matches:
        return json.loads(new_matches[0])
    events = events[:-1].replace('self.__next_f.push(', '').replace('\\', '')
    old_pattern = r'"matches"\s*\:\s*(\[.+?])}]]}]n'
    return json.loads(re.findall(old_pattern, events.replace(',false', ''), re.DOTALL)[0])


def _fetch_matches(url):
    """v3.7.7 — télécharge la liste des matchs (page du guide, puis repli
    JSON tvsport.guide) et l'écrit dans le cache disque (2 min). Retourne la
    liste ou None ; toute panne réseau est silencieuse.
    v3.7.25 (B) — 3e étage : si les DEUX lectures strictes échouent (le guide
    a changé sa structure), mode dégradé « rapprochement par titre » : les
    mêmes données sont relues de façon permissive et les paires match ↔
    chaînes sont reconstruites — au lieu d'afficher « Aucun match
    programmé »."""
    matches = None
    if url != Live_fallback_url:
        try:
            matches = _matches_from_page(url)
        except Exception as e:
            log_info('events page {0} failed: {1!r}'.format(url, e))
    if not matches:
        # fallback: same schema served as plain JSON by tvsport.guide
        try:
            matches = requests.get(Live_fallback_url, headers={'User-Agent': client.agent()},
                                   timeout=12).json().get('matches')
        except Exception as e:
            log_info('events fallback {0} failed: {1!r}'.format(Live_fallback_url, e))
    if not matches:
        # v3.7.25 (B) — mode dégradé « rapprochement par titre » : lecture
        # permissive des MÊMES sources (page + repli JSON), reconstruction
        # des matchs au schéma du plugin. Aucune source ni aucun lien
        # ajoutés : seuls les contenus déjà servis par le guide sont
        # re-associés (équipes + heure + chaînes).
        try:
            matches = _matches_degraded(url)
            if matches:
                log_info('events degraded OK: {0} matchs reconstruits'.format(len(matches)))
        except Exception as e:
            log_info('events degraded {0} failed: {1!r}'.format(url, e))
    if matches and fastcache is not None:
        fastcache.fset('ev:' + url, matches)
        fastcache.flush()
    return matches


# ---------------------------------------------------------------------------
# v3.7.25 (B) — MODE DÉGRADÉ « rapprochement par titre ». Principe hérité des
# addons « Find My Game » appliqué EN INTERNE : si le guide change de
# structure, équipes + heure + chaînes suffisent à reformer la grille.
# Repères mesurés sur la grille réelle (03/11/2026) : heure affichée = UTC+2
# (114/114 matchs), « channel » = « <chaîne> <code pays> », links et
# oldLinks portés par chaque canal.
# ---------------------------------------------------------------------------
_GUIDE_TZ_OFFSET_MIN = 120      # heure affichée du guide = UTC + 2


def _strip_lang_suffix(name):
    """« TNT Sports 1 GB » -> « TNT Sports 1 » : retire le(s) code(s) pays
    collés en fin du champ `channel` du guide (2-3 lettres majuscules après
    un espace, au plus deux codes). Sans effet sur « Canal+ Foot » etc."""
    s = six.ensure_text(name or u'', encoding='utf-8', errors='replace').strip()
    for _ in range(2):
        s2 = re.sub(r'\s+[A-Z]{2,3}$', '', s)
        if s2 == s:
            break
        s = s2
    return s.strip()


def _degraded_time_ms(m):
    """Horodatage (ms) le plus fiable d'un match « brut » :
    1) startTimestamp (secondes OU millisecondes, fenêtre de plausibilité
       1973-2100 — un timestamp absurde ne doit jamais masquer le match) ;
    2) matchDate (« 2026-11-03 ») + time (« 22:00 ») à UTC+2 (décalage
       mesuré du guide : 114/114 matchs) ;
    3) sinon 999999999999 (queue de liste, comme la voie stricte — un
       horaire inconnu ne doit JAMAIS être interprété comme « terminé »,
       sinon le match disparaît avec « Masquer les matchs terminés »)."""
    try:
        v = float(m.get('startTimestamp') or 0)
        if v > 1e11:                        # déjà en millisecondes
            v = v / 1000.0
        if 1e8 <= v <= 4.1e9:               # 1973..2100 : plausible
            return int(v * 1000)
    except Exception:
        pass
    md = six.ensure_text(m.get('matchDate') or '', encoding='utf-8', errors='replace').strip()
    hh = six.ensure_text(m.get('time') or m.get('livetvtimestr') or '',
                         encoding='utf-8', errors='replace').strip()
    if (re.match(r'^\d{4}-\d{2}-\d{2}$', md) and re.match(r'^\d{1,2}:\d{2}', hh)):
        try:
            hhmm = hh.split(' ')[0].split(':')
            dt = datetime(int(md[0:4]), int(md[5:7]), int(md[8:10]),
                          int(hhmm[0]), int(hhmm[1]))
            dt = dt - timedelta(minutes=_GUIDE_TZ_OFFSET_MIN)   # -> UTC
            epoch = datetime(1970, 1, 1)
            return int((dt - epoch).total_seconds() * 1000)
        except Exception:
            pass
    return int('999999999999')


def _degraded_duration_min(m):
    """Durée en MINUTES (schéma du guide) ; 105 min par défaut, bornée."""
    try:
        v = int(float(m.get('duration')))
        if 5 <= v <= 600:
            return v
    except Exception:
        pass
    return 105


def _degraded_teams(m):
    """(team1, team2) au mieux : champs dédiés, sinon « A vs B » de
    matchstr, sinon matchText nettoyé (retire l'heure en tête et les
    segments [crochets] : « 22:00 [Ligue] A vs B [TNT Sports 1 GB] »)."""
    t1 = m.get('team1')
    t1 = six.ensure_text(t1, encoding='utf-8', errors='replace').strip() if t1 else u''
    t2 = m.get('team2')
    t2 = six.ensure_text(t2, encoding='utf-8', errors='replace').strip() if t2 else u''
    if t1 and t2:
        return t1, t2
    mstr = six.ensure_text(m.get('matchstr') or '', encoding='utf-8', errors='replace').strip()
    if ' vs ' in mstr:
        a, b = mstr.split(' vs ', 1)
        return (t1 or a.strip()), (t2 or b.strip())
    mt = six.ensure_text(m.get('matchText') or '', encoding='utf-8', errors='replace').strip()
    mt = re.sub(r'^\d{1,2}:\d{2}\s*', '', mt)
    mt = re.sub(r'\[[^\]]*\]', '', mt).strip()
    if mt and ' vs ' in mt:
        a, b = mt.split(' vs ', 1)
        return (t1 or a.strip()), (t2 or b.strip())
    if mt and not t1:
        return mt, t2
    return t1, t2


def _degraded_channels(m):
    """Chaînes du match en mode dégradé — RIEN N'EST INVENTÉ :
    • canal du guide AVEC liens -> dict (name/language/links) d'origine ;
    • canal SANS liens -> NOM SEUL : la liste des liens du match ira alors
      les chercher dans la base de chaînes locale (get_links_for_channel),
      exactement comme la voie stricte traite déjà les chaînes « string » ;
    • à défaut, champ `channel` (« TNT Sports 1 GB ») -> nom débarassé du
      code pays."""
    out = []
    chs = m.get('channels')
    if isinstance(chs, list):
        for c in chs:
            try:
                if isinstance(c, dict):
                    nm = six.ensure_text(c.get('name') or '', encoding='utf-8',
                                         errors='replace').strip()
                    if not nm:
                        continue
                    c_links = c.get('links')
                    c_old = c.get('oldLinks')
                    links = [l for l in ((c_links if isinstance(c_links, list) else [])
                                         + (c_old if isinstance(c_old, list) else [])) if l]
                    if links:
                        out.append({'name': nm,
                                    'language': six.ensure_text(c.get('language') or '',
                                                                encoding='utf-8',
                                                                errors='replace').strip(),
                                    'links': links,
                                    'oldLinks': []})
                    else:
                        out.append(nm)      # nom seul -> base de chaînes locale
                elif isinstance(c, six.string_types):
                    nm = _strip_lang_suffix(c)
                    if nm:
                        out.append(nm)
            except Exception:
                continue
    if not out:
        s = _strip_lang_suffix(m.get('channel'))
        if s:
            out.append(s)
    return out


def _degraded_looks_like_match(o):
    """Un dict « ressemble à un match » s'il porte au moins un nom."""
    return isinstance(o, dict) and bool(
        o.get('team1') or o.get('team2') or o.get('matchstr') or o.get('matchText'))


def _degraded_find_matches(obj, depth=0):
    """Première liste d'objets « ressemblant à des matchs » dans un JSON
    quelconque (clé 'matches', ou liste directe, recherche en profondeur 4)."""
    if depth > 4:
        return None
    if isinstance(obj, list):
        return obj if (obj and _degraded_looks_like_match(obj[0])) else None
    if isinstance(obj, dict):
        ms = obj.get('matches')
        if isinstance(ms, list) and ms:
            return ms
        for v in obj.values():
            r = _degraded_find_matches(v, depth + 1)
            if r:
                return r
    return None


def _degraded_normalize_match(m):
    """Objet brut -> match au schéma du plugin (mêmes clés que la voie
    stricte). None si l'objet n'a vraiment rien d'un match."""
    if not isinstance(m, dict):
        return None
    t1, t2 = _degraded_teams(m)
    if not (t1 or t2):
        return None
    return {
        'team1': t1 or t2,
        'team2': t2,
        'matchstr': (u'{0} vs {1}'.format(t1, t2) if (t1 and t2) else (t1 or t2)),
        'sport': six.ensure_text(m.get('sport') or '', encoding='utf-8', errors='replace'),
        'league': six.ensure_text(m.get('league') or '', encoding='utf-8',
                                  errors='replace') or None,
        'startTimestamp': _degraded_time_ms(m),
        'duration': _degraded_duration_min(m),
        'channels': _degraded_channels(m),
        'important': bool(m.get('important')),
    }


def _degraded_json_candidates(text):
    """Candidats JSON « liste d'objets » extraits d'une page HTML/JS :
    1) intérieurs des JSON.parse(`…`) ; 2) tableaux littéraux [ {"…
    (balayage avec comptage de crochets, respect des chaînes, plafond
    300 000 caractères et 40 candidats). Tolère les échappements des
    payloads Next.js."""
    t = six.ensure_text(text or '', encoding='utf-8', errors='ignore')
    t = t.replace('\\n', ' ').replace('\\t', ' ').replace('\\"', '"')
    out = []
    for m in re.finditer(r'JSON\.parse\(`(\[.+?\])`', t, re.DOTALL):
        out.append(m.group(1))
    pos = 0
    while len(out) < 40:
        i = t.find('[{"', pos)
        if i < 0:
            break
        depth = 0
        j = i
        end = -1
        instr = False
        esc = False
        while j < len(t):
            ch = t[j]
            if instr:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    instr = False
            else:
                if ch == '"':
                    instr = True
                elif ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
                elif ch == '<':      # ne traverse pas les balises (</script>)
                    break
            j += 1
            if j - i > 300000:
                break
        if end > 0:
            out.append(t[i:end])
            pos = end
        else:
            pos = i + 1
    return out


def _matches_degraded(url):
    """v3.7.25 (B) — mode dégradé « rapprochement par titre » : quand la
    lecture stricte de la page ET le repli JSON échouent (le guide a changé
    de structure), relit les MÊMES données de façon permissive et reconstruit
    des matchs au schéma du plugin (équipes, heure, chaînes). Aucune source
    ni aucun lien ajoutés : seuls les contenus déjà servis par le guide sont
    re-associés. Retourne la liste reconstruite ou None."""
    texts = []
    if url != Live_fallback_url:
        try:
            texts.append(requests.get(url, timeout=12).text)
        except Exception:
            pass
    try:
        texts.append(requests.get(Live_fallback_url,
                                  headers={'User-Agent': client.agent()},
                                  timeout=12).text)
    except Exception:
        pass
    blobs = []
    for tx in texts:
        try:
            blobs.extend(_degraded_json_candidates(tx))
        except Exception:
            continue
    for b in blobs:
        try:
            parsed = json.loads(b)
        except Exception:
            try:
                parsed = json.loads(b.replace(',false', '').replace('\\/', '/'))
            except Exception:
                continue
        raw = _degraded_find_matches(parsed)
        if not raw:
            continue
        matches = []
        for m in raw[:400]:
            try:
                nm = _degraded_normalize_match(m)
            except Exception:
                nm = None
            if nm:
                matches.append(nm)
        if matches:
            if fastcache is not None:
                fastcache.fset('ev:' + url, matches)
                fastcache.flush()
            return matches
    return None


_WARM_INFLIGHT = set()
_WARM_LOCK = threading.Lock()


def _refresh_matches_bg(url):
    """v3.7.7 — lance le (re)téléchargement de la grille en ARRIÈRE-PLAN
    (fil daemon) : l'affichage, lui, part aussitôt du cache — la liste à
    jour sera là à la navigation suivante. Un seul téléchargement à la fois
    par URL (anti-doublon accueil + menu)."""
    def _run():
        with _WARM_LOCK:
            if url in _WARM_INFLIGHT:
                return
            _WARM_INFLIGHT.add(url)
        try:
            _fetch_matches(url)
        except Exception:
            pass
        finally:
            with _WARM_LOCK:
                _WARM_INFLIGHT.discard(url)
    try:
        t = threading.Thread(target=_run)
        t.daemon = True
        t.start()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# v3.7.26 — LOGOS D'ÉQUIPES SUPPRIMÉS (ESPN + TheSportsDB + téléchargement
# local + réglage « team_logos »). Demande utilisateur : « fais aussi la
# suppression des logos » — ces 460 lignes ralentissaient la liste au
# premier passage de chaque compétition (requête ESPN bloquante, budget
# 2,5 s) et consommaient réseau/CPU en arrière-plan (fil TheSportsDB).
# La liste affiche l'icône de base de l'add-on ; « Vider les données »
# supprime toujours le dossier logos/ posé par les anciennes versions.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# v3.7.27 — OPTIONS BÊTA (désactivées par défaut, Paramètres > Expérimental)
#   B1 : consommer les liens personnalisés ÉVENTUELS que le guide publierait
#        dans son propre endpoint custom_links.php (même source, pas une
#        nouvelle source ; parser défensif : le format n'est pas documenté).
#   B6 : lire la grille TV publique FR (XMLTV epgshare01, 5 Mo gz, mise à jour
#        quotidienne mesurée 11:38) pour écrire dans la FICHE du match la
#        chaîne française officielle qui le diffuse (info seule — AUCUN flux
#        ajouté, le filtre « sources françaises » reste basé sur les liens
#        réellement fournis par le guide). Mesuré 03/11/2026 : 32 des 114
#        matchs du guide sont identifiés en plus (LDC/UEFA sur Canal+ Live
#        2-19, NBA/NHL sur beIN 1-3/MAX).
# ---------------------------------------------------------------------------


def _beta_custom():
    """Réglage BÊTA B1 — liens personnalisés du guide (désactivé par défaut)."""
    try:
        return (ADDON.getSetting('beta_custom_links') or 'false').lower() == 'true'
    except Exception:
        return False


def _beta_epg():
    """Réglage BÊTA B6 — chaînes FR des grilles TV officielles (désactivé par défaut)."""
    try:
        return (ADDON.getSetting('beta_official_epg') or 'false').lower() == 'true'
    except Exception:
        return False


_NORM_TRANS = {0x00f8: u'o', 0x0111: u'd', 0x0142: u'l', 0x00e6: u'ae',
               0x0153: u'oe', 0x00df: u'ss', 0x00f0: u'd', 0x00fe: u'th',
               0x0127: u'h'}


def _norm_txt(s):
    """Minuscules sans accents, lettres nordiques translittérées :
    « Bodø / Glimt » -> « bodo glimt » (le guide et l'EPG n'écrivent pas
    toujours ø de la même façon)."""
    s = six.ensure_text(s or u'', encoding='utf-8', errors='replace')
    s = s.lower().translate(_NORM_TRANS)
    s = unicodedata.normalize('NFD', s)
    s = u''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(u'[^a-z0-9]+', u' ', s).strip()


def _team_tokens(s):
    """Mots significatifs d'un nom d'équipe (« Bodø / Glimt » -> ['bodo','glimt'])."""
    stop = set((u'fc', u'cf', u'ac', u'as', u'sc', u'ss', u'fk', u'bk', u'if', u'cd', u'cf'))
    return [t for t in _norm_txt(s).split() if len(t) > 2 and t not in stop]


# ---- B1 : liens personnalisés du guide ------------------------------------


def _custom_links_fetch():
    """Tableau JSON de custom_links.php -> entrées normalisées. Le format n'étant
    pas documenté, CHAQUE entrée est lue de façon défensive : on accepte les
    clés usuelles (team1/team2, match, title, slug, channel, name, links,
    urls, oldLinks, url, channels). Retourne une liste (vide si rien)."""
    data = None
    if fastcache is not None:
        data = fastcache.fget('cl:custom', 1800)
        if data is not None:
            return data
    try:
        r = requests.get(Custom_links_url, headers={'User-Agent': client.agent()}, timeout=10)
        data = r.json()
    except Exception as e:
        log_info('custom links fetch failed: {0!r}'.format(e))
        data = []
    if not isinstance(data, list):
        data = []
    out = []
    for ent in data[:200]:
        try:
            if not isinstance(ent, dict):
                continue
            # --- équipes/match visés
            t1 = ent.get('team1') or u''
            t2 = ent.get('team2') or u''
            mm = ent.get('match') or ent.get('title') or ent.get('matchstr') or u''
            if mm and u' vs ' in mm and not (t1 and t2):
                a, b = mm.split(u' vs ', 1)
                t1, t2 = t1 or a, t2 or b
            slug = six.ensure_text(ent.get('slug') or u'', encoding='utf-8',
                                   errors='replace')
            toks = set(_team_tokens(t1)) | set(_team_tokens(t2))
            if len(toks) < 2 and slug:
                toks = set(w for w in _norm_txt(slug).split() if len(w) > 2)
            if len(toks) < 2:
                continue
            # --- chaînes + liens
            chans = []
            cs = ent.get('channels')
            if isinstance(cs, list) and cs:
                for c in cs:
                    if isinstance(c, dict) and (c.get('links') or c.get('url')):
                        us = [u for u in ((c.get('links') or []) +
                                          (c.get('oldLinks') or []) +
                                          ([c.get('url')] if c.get('url') else [])) if u]
                        if us:
                            chans.append((six.ensure_text(c.get('name') or u'',
                                                          encoding='utf-8',
                                                          errors='replace'),
                                          six.ensure_text(c.get('language') or u'',
                                                          encoding='utf-8',
                                                          errors='replace'), us))
            us = [u for u in ((ent.get('links') if isinstance(ent.get('links'), list) else [])
                              + (ent.get('urls') if isinstance(ent.get('urls'), list) else [])
                              + ([ent.get('url')] if isinstance(ent.get('url'), six.string_types) else [])) if u]
            if us:
                chans.append((six.ensure_text(ent.get('name') or ent.get('channel') or u'',
                                              encoding='utf-8', errors='replace'),
                              six.ensure_text(ent.get('language') or u'',
                                              encoding='utf-8', errors='replace'), us))
            if not chans:
                continue
            out.append({'tokens': toks, 'channels': chans})
        except Exception:
            continue
    if fastcache is not None:
        fastcache.fset('cl:custom', out)
        fastcache.flush()
    return out


def _apply_custom_links(matches):
    """Fusionne les liens personnalisés du guide dans les matchs visés : la
    chaîne custom devient un canal de plus du match (même schéma que la grille),
    si les DEUX équipes du match figurent dans les repères de l'entrée. Aucune
    URL en dur : tout vient du fichier du guide. Doublons d'URL refusés."""
    if not matches:
        return matches
    ents = _custom_links_fetch()
    if not ents:
        return matches
    added = 0
    for m in matches:
        try:
            if not isinstance(m, dict):
                continue
            t1 = m.get('team1') or u''
            t2 = m.get('team2') or u''
            if not (t1 and t2):
                continue
            ta, tb = set(_team_tokens(t1)), set(_team_tokens(t2))
            if not (ta and tb):
                continue
            chs = m.get('channels')
            if not isinstance(chs, list):
                chs = []
            seen = set()
            for c in chs:
                try:
                    if isinstance(c, dict):
                        for u in (c.get('links') or []):
                            seen.add(six.ensure_text(u, encoding='utf-8',
                                                     errors='replace'))
                except Exception:
                    continue
            for ent in ents:
                if not (ta <= ent['tokens'] and tb <= ent['tokens']):
                    continue
                for nm, lg, us in ent['channels']:
                    fresh = [u for u in us
                             if six.ensure_text(u, encoding='utf-8',
                                                errors='replace') not in seen]
                    if not fresh:
                        continue
                    seen.update(six.ensure_text(u, encoding='utf-8',
                                                errors='replace') for u in fresh)
                    chs.append({'name': nm or u'Liens du guide',
                                'language': lg,
                                'links': fresh,
                                'oldLinks': []})
                    added += 1
            m['channels'] = chs
        except Exception:
            continue
    if added:
        log_info('custom links: {0} canal(aux) fusionné(s)'.format(added))
    return matches


# ---- B6 : chaînes FR des grilles TV officielles (info seule) ---------------

_EPG_CH_RE = re.compile(u'|'.join([
    u'^beIN\\.SPORTS\\.[123]\\.fr$', u'^beIN\\.SPORTS\\.MAX\\.(?:[4-9]|10)\\.fr$',
    u'^Canal\\+\\.Foot\\.fr$', u'^CANAL\\+FOOT\\.fr$',
    u'^Canal\\+\\.Sport\\.fr$', u'^CANAL\\+SPORT\\.fr$',
    u'^Canal\\+\\.Sport\\.360\\.fr$', u'^CANAL\\+SPORT360\\.fr$',
    u'^CANAL\\+LIVE\\.(?:[1-9]|1[0-9])\\.fr$',
    u'^Canal\\+\\.fr$', u'^CANAL\\+\\.fr$',
    u"^L'Equipe\\.fr$", u'^DAZN\\.1\\.fr$', u'^RMC\\.Sport\\.[12]\\.fr$',
]))


def _epg_cache_path():
    return control.translatePath(ADDON_DATA + 'epg_fr.json')


def _epg_prog_re():
    return re.compile(
        u'<programme channel="([^"]+)" start="([0-9]{14})[^"]*" '
        u'stop="([0-9]{14})[^"]*">\\s*<title[^>]*>([^<]+)</title>', re.S)


def _epg_ms(s):
    """« 20261103050000 » (heure locale de la grille) -> ms epoch naïf :
    les fenêtres étant comparées ENTRE elles (grille guide = UTC+2 fixe,
    EPG = heure FR fixe), un décalage constant de ±1 h n'empêche jamais
    l'appariement (fenêtre de tolérance 90 min de chaque côté)."""
    try:
        import calendar
        return calendar.timegm((int(s[0:4]), int(s[4:6]), int(s[6:8]),
                                int(s[8:10]), int(s[10:12]), int(s[12:14]),
                                0, 0, 0)) * 1000
    except Exception:
        return 0


def _epg_build_cache():
    """Télécharge la grille FR du jour (5 Mo gz), n'en garde que les programmes
    des chaînes sportives FR cibles (~1 700), écrit un JSON léger dans le
    profil : {'_date': 'AAAA-MM-JJ', 'progs': [[début, fin, chaîne, titre], …]}.
    Panne réseau ou XML cassé -> cache conservé, exception silencieuse."""
    import gzip as _gzip
    p = _epg_cache_path()
    today = _epg_today()
    try:
        if xbmcvfs.exists(p):
            f = xbmcvfs.File(p)
            try:
                raw = f.read()
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', 'replace')
                old = json.loads(raw)
            finally:
                f.close()
            if isinstance(old, dict) and old.get('_date') == today:
                return old
    except Exception:
        pass
    raw = None
    try:
        r = requests.get(EPG_FR_URL, timeout=60)
        raw = _gzip.decompress(r.content) if r.content[:2] == b'\x1f\x8b' else r.content
    except Exception as e:
        log_info('epg fr download failed: {0!r}'.format(e))
        return None
    txt = None
    try:
        txt = raw.decode('utf-8', 'replace')
    except Exception:
        return None
    names = {}
    for cid, blk in re.findall(u'<channel id="([^"]+)">(.*?)</channel>', txt, re.S):
        if _EPG_CH_RE.match(cid):
            mm = re.search(u'<display-name[^>]*>([^<]+)', blk)
            if mm:
                names[cid] = (mm.group(1).replace(u'&apos;', u"'")
                              .replace(u'&amp;', u'&'))
    progs = []
    for cid, st, sp, ti in _epg_prog_re().findall(txt):
        if cid in names:
            progs.append([_epg_ms(st), _epg_ms(sp), names[cid], _norm_txt(ti)])
    if not progs:
        log_info('epg fr: aucune chaîne cible reconnue (format changé ?)')
        return None
    data = {'_date': today, 'progs': progs[:4000]}
    try:
        f = xbmcvfs.File(p, 'w')
        f.write(six.ensure_binary(json.dumps(data), encoding='utf-8'))
        f.close()
    except Exception as e:
        log_info('epg fr cache write failed: {0!r}'.format(e))
    return data


def _epg_today():
    try:
        return datetime.now().strftime('%Y-%m-%d')
    except Exception:
        return u''


def _epg_map(matches):
    """Apparie chaque match (les DEUX équipes dans le titre du programme,
    fenêtre 90 min) -> {« A vs B »: [« Canal+ Foot », …]}. Zéro réseau :
    travaille sur le cache du jour déjà écrit."""
    data = _epg_build_cache()
    if not data:
        return {}
    progs = data.get('progs') or []
    if not progs:
        return {}
    out = {}
    for m in matches[:400]:
        try:
            if not isinstance(m, dict):
                continue
            t1, t2 = m.get('team1') or u'', m.get('team2') or u''
            if not (t1 and t2):
                continue
            ta, tb = _team_tokens(t1), _team_tokens(t2)
            if not (ta and tb):
                continue
            st = m.get('startTimestamp') or 0
            try:
                st = float(st)
                if st < 1e11:          # secondes -> ms
                    st *= 1000.0
            except Exception:
                st = 0
            chans = []
            for s, e, cn, tn in progs:
                if st and not (s - 5400e3 <= st <= e + 5400e3):
                    continue
                if all(w in tn for w in ta) and all(w in tn for w in tb):
                    if cn not in chans:
                        chans.append(cn)
            if chans:
                key = u'{0} vs {1}'.format(t1, t2)
                out[key] = chans[:6]
        except Exception:
            continue
    return out


def _epg_bg(matches):
    """Fil d'arrière-plan B6 : construit/rafraîchit le cache du jour (télé-
    chargement lourd 1×/jour max) puis publie le mapping dans fastcache."""
    try:
        mp = _epg_map(matches)
        if mp and fastcache is not None:
            fastcache.fset('epgfr:map', mp)
            fastcache.flush()
            log_info('epg fr: {0} match(s) identifié(s) en FR'.format(len(mp)))
    except Exception as e:
        log_info('epg fr bg failed: {0!r}'.format(e))


def _epg_channels_for(event):
    """Chaînes FR officielles d'un match (« A vs B ») selon la grille du jour.
    Lecture instantanée (fastcache) : [] si absent ou bêta désactivée."""
    if fastcache is None:
        return []
    try:
        mp = fastcache.fget('epgfr:map', 7200)
        if isinstance(mp, dict):
            return mp.get(event) or []
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# v3.7.28 — « MA PLAYLIST » (bêta, Paramètres > Expérimental (bêta))
#   L'utilisateur colle l'URL m3u / m3u_plus (Xtream) de SON PROPRE
#   abonnement dans les réglages. L'add-on télécharge cette playlist
#   UNIQUEMENT depuis son appareil, en extrait les chaînes live (ts / m3u8,
#   VOD et DASH refusés), et dans la fiche d'un match, ses chaînes dont le
#   nom correspond à une chaîne du match deviennent des entrées de lecture
#   en plus des liens du guide, libellées « Ma playlist : … ».
#   RÈGLE DU DÉPÔT : AUCUNE URL en dur, rien ne vient de nous — la playlist
#   reste sur l'appareil (cache profil mypl.json, empreinte de l'URL
#   seulement, jamais l'URL). Aucun inputstream ajouté (verrou).
# ---------------------------------------------------------------------------

_PL_QUALITY = (u'hd', u'fhd', u'sd', u'uhd', u'4k', u'8k', u'1080p', u'1080i',
               u'720p', u'480p', u'576p', u'hevc', u'h265', u'h264',
               u'backup', u'vip', u'iptv')
_PL_JUNK_RE = re.compile(u'xxx|porn|adult|sexy|18\\+', re.I)


def _mypl_url():
    """URL « Ma playlist » des réglages, validée : schéma http(s), sans
    espace, hôte présent, PAS de crochets (le template non rempli
    « http://[adresse-du-serveur]:8080/… » est ainsi refusé — les portails
    Xtream sont toujours en hôte:port). Chaîne vide si invalide.
    JAMAIS écrite dans les logs."""
    try:
        u = six.ensure_text(ADDON.getSetting('my_playlist_url') or u'',
                            encoding='utf-8', errors='replace').strip()
    except Exception:
        return u''
    if not u.lower().startswith((u'http://', u'https://')):
        return u''
    if u' ' in u or u'[' in u or u']' in u:
        return u''
    try:
        if not urlparse(u).hostname:
            return u''
    except Exception:
        return u''
    return u


def _mypl_fp(url):
    """Empreinte courte de l'URL (8 hex) : sert à invalider le cache quand
    l'utilisateur change d'URL — l'URL elle-même n'est jamais stockée."""
    try:
        return hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]
    except Exception:
        return u''


def _mypl_cache_path():
    return control.translatePath(ADDON_DATA + 'mypl.json')


def _mypl_norm_key(s):
    """Clé d'appariement des noms de chaînes : apostrophes typographiques
    ramenées à l'ASCII (« L'Équipe » = « L'Equipe »), annotations entre
    crochets/parenthèses retirées (« (1080p) [Geo-blocked] » des listes
    publiques), normalisation sans accents, puis mots « qualité » retirés —
    « beIN SPORTS 1 HD » et « beIN Sports 1 » donnent 'bein sports 1'."""
    s = s.replace(u'\u2019', u"'").replace(u'\u2018', u"'")
    s = s.replace(u'\u02bc', u"'")
    s = re.sub(u'[\\[\\(][^\\]\\)]*[\\]\\)]', u' ', s)
    toks = [t for t in _norm_txt(s).split() if t not in _PL_QUALITY]
    return u' '.join(toks).strip()


def _mypl_parse(txt):
    """Texte M3U / m3u_plus -> {clé: [nom affiché, url]}.
    Ne garde que les flux live (http direct) ; saute VOD (.mp4/.mkv/.avi,
    /movie/, /series/), DASH (.mpd — DRM), player_api, groupes adultes ;
    dédup par clé (première URL gagnante) ; plafond 5 000 chaînes."""
    out = {}
    disp = u''
    tvg = u''
    grp = u''
    for raw in txt.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:7].lower() == u'#extinf':
            m = re.search(u'tvg-name="([^"]*)"', line)
            tvg = m.group(1).strip() if m else u''
            m = re.search(u'group-title="([^"]*)"', line)
            grp = m.group(1).strip() if m else u''
            clean = re.sub(u'[a-zA-Z0-9_-]+="[^"]*"', u'', line)
            i = clean.find(u',')
            disp = clean[i + 1:].strip() if i >= 0 else u''
            continue
        if line[0] == u'#':
            continue
        # ligne URL
        low = line.lower()
        nm = disp or tvg
        disp = u''
        tvg = u''
        grp_k = grp
        grp = u''
        if not low.startswith((u'http://', u'https://')):
            continue
        if _PL_JUNK_RE.search(grp_k) or _PL_JUNK_RE.search(nm):
            continue
        if any(x in low for x in (u'.mp4', u'.mkv', u'.avi', u'.mpd',
                                  u'/movie/', u'/series/', u'player_api.php',
                                  u'/dash/')):
            continue
        key = _mypl_norm_key(nm)
        if key and len(key) >= 4 and key not in out:
            out[key] = [nm, line]
            if len(out) >= 5000:
                break
    return out


def _mypl_read_cache():
    """Dict du cache profil si son empreinte correspond à l'URL ACTUELLE,
    sinon {} (URL changée -> cache considéré vide, il sera refait)."""
    url = _mypl_url()
    if not url:
        return {}
    fp = _mypl_fp(url)
    try:
        p = _mypl_cache_path()
        if xbmcvfs.exists(p):
            f = xbmcvfs.File(p)
            try:
                raw = f.read()
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', 'replace')
                d = json.loads(raw)
            finally:
                f.close()
            if (isinstance(d, dict) and d.get('_fp') == fp
                    and isinstance(d.get('chans'), dict)):
                return d['chans']
    except Exception:
        pass
    return {}


def _mypl_build(url):
    """Télécharge la playlist et écrit le cache du profil. Retourne le dict
    ({} si échec). Le cache porte l'empreinte de l'URL, JAMAIS l'URL."""
    try:
        r = requests.get(url, headers={'User-Agent': client.agent()},
                         timeout=(10, 45))
        txt = six.ensure_text(r.content, encoding='utf-8', errors='replace')
    except Exception as e:
        log_info('my playlist: téléchargement échoué {0!r}'.format(e))
        return {}
    if u'#EXTM3U' not in txt[:4000]:
        log_info('my playlist: la réponse n\'est pas une playlist M3U')
        return {}
    chans = _mypl_parse(txt)
    if not chans:
        log_info('my playlist: 0 chaîne live reconnue (VOD/DRM seulement ?)')
        return {}
    data = {'_fp': _mypl_fp(url), '_ts': _epg_today(), 'chans': chans}
    try:
        f = xbmcvfs.File(_mypl_cache_path(), 'w')
        f.write(six.ensure_binary(json.dumps(data), encoding='utf-8'))
        f.close()
    except Exception as e:
        log_info('my playlist: cache non écrit {0!r}'.format(e))
    log_info('my playlist: {0} chaîne(s) live prête(s)'.format(len(chans)))
    return chans


_MYPL_BG = {'running': False}


def _mypl_bg(url):
    """Fil d'arrière-plan : la fiche ne bloque JAMAIS sur le téléchargement
    (les playlists de portails font souvent plusieurs Mo)."""
    if _MYPL_BG['running']:
        return
    _MYPL_BG['running'] = True
    try:
        _mypl_build(url)
    except Exception as e:
        log_info('my playlist bg: {0!r}'.format(e))
    finally:
        _MYPL_BG['running'] = False


def _mypl_ensure():
    """Retourne les chaînes disponibles MAINTENANT ({} tant que rien n'est
    prêt) et lance le rafraîchissement en arrière-plan si besoin (cache
    absent, URL changée, ou pas reconstruite aujourd'hui ; au plus 1 essai
    par 15 min via fastcache)."""
    url = _mypl_url()
    if not url:
        return {}
    data = _mypl_read_cache()
    stale = not data
    if not stale:
        try:
            p = _mypl_cache_path()
            f = xbmcvfs.File(p)
            try:
                raw = f.read()
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', 'replace')
                d = json.loads(raw)
            finally:
                f.close()
            stale = not (isinstance(d, dict) and d.get('_ts') == _epg_today())
        except Exception:
            stale = True
    if stale and fastcache is not None:
        try:
            if not fastcache.fresh('mypl:bg', 900):
                fastcache.fset('mypl:bg', {'fp': _mypl_fp(url)})
                fastcache.flush()
                threading.Thread(target=_mypl_bg, args=(url,),
                                 daemon=True).start()
        except Exception:
            pass
    return data


def _mypl_items_for(chan_data):
    """[(url, libellé)] : chaînes de LA PLAYLIST DE L'UTILISATEUR dont le
    nom correspond à une chaîne du match (clé exacte normalisée ; le match
    ne fournit que des NOMS, les flux viennent de la playlist — rien
    d'ajouté par nous). Zéro réseau bloquant : cache profil seulement."""
    if not chan_data:
        return []
    chans = _mypl_ensure()
    if not chans:
        return []
    names = []
    for c in chan_data:
        try:
            if isinstance(c, dict):
                nm = six.ensure_text(c.get('name') or u'', encoding='utf-8',
                                     errors='replace').strip()
            else:
                raw = six.ensure_text(c, encoding='utf-8', errors='replace').strip()
                row = _db_channel(raw)
                nm = six.ensure_text(row.get('channelName') or u'',
                                     encoding='utf-8', errors='replace').strip() if row else raw
            if nm:
                names.append(nm)
        except Exception:
            continue
    out = []
    seen = set()
    for nm in names:
        for key in (_mypl_norm_key(nm), _norm_txt(nm)):
            ent = chans.get(key)
            if not ent:
                continue
            if ent[1] not in seen:
                seen.add(ent[1])
                out.append((ent[1], u'Ma playlist : {0}'.format(ent[0])))
            break
    return out


def _pl_payload(u):
    """Payload de lecture d'un flux de la playlist — même convention que la
    grille (quote(base64(...))) : décodé par play_mypl."""
    return str(quote(base64.b64encode(six.ensure_binary(u, encoding='utf-8'))))


def play_mypl(name, url):
    """v3.7.28 (bêta) — lecture DIRECTE d'un flux de « Ma playlist ». Le
    lien vient des réglages de L'UTILISATEUR (jamais du dépôt) et est déjà
    un flux final (ts / m3u8) : aucun résolveur, aucun inputstream ajouté
    (verrou), MIME correct pour un démarrage fiable."""
    try:
        stream_url = six.ensure_text(base64.b64decode(unquote(url)),
                                     encoding='utf-8', errors='strict').strip()
    except Exception:
        raise Exception(u'Lien de la playlist illisible.')
    if not stream_url.lower().startswith((u'http://', u'https://')):
        raise Exception(u'Lien de la playlist invalide.')
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name), encoding='utf-8',
                                          errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name)})
    liz.setProperty('IsPlayable', 'true')
    liz.setContentLookup(False)
    if u'.m3u8' in stream_url.lower():
        liz.setMimeType('application/vnd.apple.mpegurl')
    else:
        liz.setMimeType('video/mp2t')
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


def get_events(url):  # 5
    # v3.7.6/3.7.7 — menu toujours instantané : la liste des matchs vit sur
    # le disque du profil (fastcache, porté de la 3.7.4).
    #   1) fraîche (< 2 min)           -> affichée telle quelle ;
    #   2) plus ancienne (jusqu'à 3 h) -> affichée AUSSI, et re-téléchargée
    #      en arrière-plan pour la navigation suivante ;
    #   3) rien du tout                -> téléchargement direct (première
    #      visite ; l'accueil préchauffe déjà ce téléchargement).
    ck_ev = 'ev:' + url
    matches = fastcache.fget(ck_ev, 120) if fastcache is not None else None
    if not matches and fastcache is not None:
        matches = fastcache.fstale(ck_ev, 3 * 3600)
        if matches:
            _refresh_matches_bg(url)
    if not matches:
        matches = _fetch_matches(url)
    if not matches:
        control.infoDialog(u"[COLOR red]Aucun match programmé.[/COLOR]", NAME, ICON, 5000)
        return

    # v3.7.27 (B1 bêta) — fusionner les liens personnalisés ÉVENTUELS que le
    # guide publierait dans son propre endpoint (aucune source ajoutée).
    if _beta_custom():
        try:
            matches = _apply_custom_links(matches)
        except Exception:
            pass

    # matches = re.findall('''null\,(\{"(?:matches|customNotFoundMessage).+?)\]\}\]n''', events, re.DOTALL)[0]
    # pattern = r'("matches"\s*\:\s*\[.+?])}]}]n"'
    # pattern = r'"matches"\s*\:\s*(\[.+?])}]]}]n'
    # matches = re.findall(pattern, events.replace(',false', ''), re.DOTALL)[0]
    # xbmc.log('EVENTSSS: {}'.format(matches))
    # matches = json.loads(matches)

    event_list = []

    if six.PY2:
        now = datetime.now()
        now_time_in_ms = (time.mktime(now.timetuple()) + now.microsecond / 1e6) * 1000
    else:
        now_time_in_ms = datetime.now().timestamp() * 1000
    fr_only = _french_only()
    filtered_out = 0
    hidden_finished = 0
    for match in matches:
        channels = match.get("channels", [])
        if fr_only and not _channels_have_french(channels):
            filtered_out += 1
            continue
        # links = match['additionalLinks']
        # links.extend(match['channels'])
        icon = match.get('team1Img', ICON)
        sport = match.get('sport', '')
        lname = match.get('league', None)
        lname = six.ensure_text(lname, encoding='utf-8', errors='replace') if lname else sport
        team1 = match.get('team1', None)
        team1 = six.ensure_text(team1, encoding='utf-8', errors='replace') if team1 else None
        team2 = match.get('team2', None)
        team2 = six.ensure_text(team2, encoding='utf-8', errors='replace') if team2 else None
        event = u"{} vs {}".format(team1, team2) if team1 and team2 else team1

        try:
            compare = match['startTimestamp']
            ftime = time_convert(compare)
        except Exception:
            try:
                matchdt = match['matchDate']
                tvtime = match['livetvtimestr']
                compare = adjust_date_and_convert_to_timestamp_ms(matchdt, tvtime)
                ftime = time_convert(compare)
                # xbmc.log('SHOW FTIME: {}'.format(ftime))
            except Exception:
                compare = int('999999999999')
                ftime = '-'

        duration_in_ms = match['duration'] * 60 * 1000

        is_live = False
        if compare <= now_time_in_ms <= compare + duration_in_ms:
            is_live = True
        finished = (not is_live) and now_time_in_ms > compare + duration_in_ms
        # v3.7.22 — « Masquer les matchs terminés » (activé par défaut) : la
        # liste reste resserrée sur les matchs EN DIRECT et à venir ; le
        # réglage retiré les réaffiche.
        if finished and _hide_finished():
            hidden_finished += 1
            continue

        # v3.7.16 — les icônes emoji des sports sont RETIRÉES (elles ne
        # s'affichaient pas sur plusieurs appareils Kodi/Android : aucune
        # icône visible ou cases vides) ; restent le badge d'état, les
        # langues et la fiche (plot) pour le panneau d'information.
        if is_live:
            m_color = 'lime'
            state = u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]'
        elif finished:
            m_color = 'grey'
            state = u' [COLOR grey]fini[/COLOR]'
        else:
            m_color = 'gold'
            state = u''
            # v3.7.23 — pastille « dans X min » sur les coups d'envoi
            # imminents (donnée déjà dans la grille : aucune requête)
            try:
                _mins = int(round((compare - now_time_in_ms) / 60000.0))
                if 0 <= _mins <= 15:
                    state = u' [COLOR skyblue]· dans {0} min[/COLOR]'.format(
                        _mins if _mins >= 1 else 1)
            except Exception:
                pass
        # v3.7.11 — titre épuré : heure, équipes, langues. La compétition
        # (« France. Ligue 1 »…) SORT de la ligne du match (elle reste dans
        # la fiche ci-dessous, panneau d'informations du skin).
        # v3.7.10 — les langues du match s'affichent en MAJUSCULES dans une
        # couleur inédite (magenta) : « Lorient vs Brest FR », « Sassuolo vs
        # Fiorentina DE IT ». Remplace le compteur 3.7.8 qui comptait des
        # chaînes et non des liens (les miroirs un/deux/trois triplent
        # l'affichage -> chiffre trompeur).
        cnt = u''
        if True:  # v3.7.20 — « Langues dans le titre » TOUJOURS actif (réglage retiré des Paramètres)
            cnt = _lang_suffix(_match_langs(channels))
        ftime_s = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        name = u'{0} [COLOR {1}][B]{2}[/B][/COLOR]{3}{4}'.format(
            ftime_s, m_color, event, state, cnt)

        # fiche du match (infoLabels) — texte brut, sans balises Kodi
        plot = []
        if lname and lname != sport:
            plot.append(u'{0} — {1}'.format(sport or u'Sport', lname))
        elif sport:
            plot.append(sport)
        plot.append(u'Début : {0}{1}'.format(_plain(ftime),
                                             u' — en direct' if is_live else u''))
        chan_names = []
        for ch in (channels or [])[:6]:
            try:
                if isinstance(ch, dict):
                    nm = ch.get('name')
                else:
                    row = _db_channel(six.ensure_text(ch, encoding='utf-8', errors='replace'))
                    nm = row.get('channelName') if row else None
                if nm:
                    chan_names.append(six.ensure_text(nm, encoding='utf-8', errors='replace'))
            except Exception:
                continue
        if chan_names:
            plot.append(u'Chaînes : ' + u', '.join(chan_names))
        # v3.7.27 (B6 bêta) — « Aussi diffusé sur » : chaînes FR officielles
        # reconnues dans la grille TV publique du jour (INFORMATION SEULE :
        # aucun flux ajouté, le filtre FR reste basé sur les liens réels).
        if _beta_epg():
            try:
                _fr_epg = _epg_channels_for(event)
                if _fr_epg:
                    plot.append(u'Aussi diffusé sur (grille TV FR) : '
                                + u', '.join(_fr_epg))
            except Exception:
                pass
        info = {'title': _plain(event), 'plot': u'\n'.join(plot),
                'genre': six.ensure_text(sport, encoding='utf-8', errors='replace') or u'Sport'}
        event_list.append([name, compare, channels, icon, sport, info,
                           team1, team2, lname])

        # streams = str(quote(base64.b64encode(six.ensure_binary(str(streams)))))
    if not event_list:
        if fastcache is not None:
            fastcache.flush()          # persister la liste mise en cache
        if filtered_out:
            control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs du moment.[/COLOR]", NAME, ICON, 6000)
        elif hidden_finished:
            control.infoDialog(u"[COLOR gold]Tous les matchs du moment sont terminés (masqués).[/COLOR]\n"
                               u"[COLOR lime]Réglage : « Masquer les matchs terminés ».[/COLOR]", NAME, ICON, 7000)
        return
    # v3.7.27 (B6 bêta) — construire/rafraîchir en ARRIÈRE-PLAN la grille TV
    # FR du jour et son mapping (téléchargement lourd 1×/jour max ; la liste
    # ne bloque JAMAIS dessus, l'info apparaît aux ouvertures suivantes).
    if _beta_epg() and fastcache is not None:
        try:
            if fastcache.fget('epgfr:map', 1800) is None and event_list:
                threading.Thread(target=_epg_bg, args=(matches,),
                                 daemon=True).start()
        except Exception:
            pass
    # v3.7.28 (bêta) — « Ma playlist » : préparation en ARRIÈRE-PLAN dès
    # l'ouverture de la liste (téléchargement éventuel de plusieurs Mo,
    # jamais bloquant ; la fiche des matchs n'en profite qu'une fois prête).
    try:
        _mypl_ensure()
    except Exception:
        pass
    # v3.7.8 — tri par heure (comme avant) ou groupement par sport (réglage)
    if False:  # v3.7.20 — jamais de groupement par sport (réglage retiré des Paramètres)
        events = sorted(event_list, key=lambda x: (six.ensure_text(x[4], encoding='utf-8', errors='replace').lower(), x[1]))
    else:
        events = sorted(event_list, key=lambda x: x[1])
    ctx_refresh = [(u'Actualiser la grille',
                    'RunPlugin({0}?mode=refresh_events)'.format(_url))]
    for event in deduplicate_events(events):
        streams = str(quote(base64.b64encode(six.ensure_binary(str(event[2])))))
        name = event[0]
        icon = event[3]     # v3.7.26 — icône de base (logos supprimés)
        addDir(name, streams, 'get_streams', icon, FANART, isFolder=True,
               infoLabels=event[5], context=ctx_refresh)

    if fastcache is not None:
        fastcache.flush()              # persister la liste mise en cache
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def deduplicate_events(events):
    unique_events = []
    seen = set()
    for event in events:
        # repr(channels) : hachable que les canaux soient des chaînes OU des
        # dict (tuple() d'un dict lèverait TypeError).
        unique_key = (event[0], event[1], repr(event[2]), event[3], event[4])
        if unique_key not in seen:
            seen.add(unique_key)
            unique_events.append(event)
    return unique_events


def refresh_events(url):
    """v3.7.8 — menu contextuel « Actualiser la grille » : re-télécharge la
    liste des matchs immédiatement (avant : il fallait attendre l'expiration
    du cache ou vider les données), puis redessine la liste affichée."""
    control.infoDialog(u"[COLOR skyblue]Actualisation de la grille…[/COLOR]", NAME, ICON, 3000)
    matches = _fetch_matches(url)
    if fastcache is not None:
        fastcache.flush()
    if not matches:
        control.infoDialog(u"[COLOR gold]Actualisation impossible pour le moment (réseau ?).[/COLOR]", NAME, ICON, 5000)
        return
    xbmc.executebuiltin('Container.Refresh')


# ---------------------------------------------------------------------------
# Filtrage « sources françaises » (réglage french_only, activé par défaut).
# Le site marque chaque chaîne d'un match avec un code pays (language='FR',
# 'GB', 'ES'...) : on garde uniquement les chaînes françaises ; les matchs
# sans aucune chaîne française disparaissent de la liste.
# ---------------------------------------------------------------------------
FRENCH_LANGS = ('fr', 'french', u'français', 'francais')
# v3.7.1 — codes pays/langue EXPLICITEMENT non français : si le guide marque
# la chaîne avec l'un de ces codes, elle n'est pas française même si son nom
# contient une marque FR (Canal+ Sport 3/5 polonaises lang='PL', TSN3 'CA',
# NFL Sunday Ticket 'US'…). Les codes inconnus restent jugés sur le nom.
NON_FR_LANGS = ('pl', 'us', 'gb', 'uk', 'de', 'es', 'it', 'pt', 'nl', 'dk', 'no',
                'se', 'fi', 'is', 'ie', 'gr', 'tr', 'ru', 'ua', 'cz', 'sk', 'ro',
                'rs', 'hr', 'bg', 'hu', 'si', 'ee', 'lv', 'lt', 'sa', 'ae', 'qa',
                'eg', 'ma', 'dz', 'tn', 'jp', 'cn', 'kr', 'in', 'au', 'nz', 'ca',
                'mx', 'br', 'ar', 'cl', 'co', 'pe', 'en', 'eng')
FRENCH_NAME_RE = re.compile(
    r'\bfr\b|\[fr\]|\(fr\)|french|fran[cç]ais|\bfrance\b|canal\+\s*foot|rmc\s*sport|'
    r'quipe|tf1\b|\bm6\b|\bw9\b|\btmc\b|ligue1'
    # v3.6.1 — chaînes FR sans suffixe « France » ni champ langue :
    r'|bein sports (max )?\d+ france|eurosport \d+ france'
    # v3.7.1 — « canal+ » seul ne suffit plus : les Canal+ Sport 1-5 et
    # Canal+ 1 numérotés sont des chaînes ÉTRANGÈRES (Pologne…) ; la France
    # n'a que Canal+ Sport (sans numéro) et Canal+ Sport 360. On exclut donc
    # tout « canal+ » suivi d'un numéro nu ou de « sport 1-5 » (360 reste FR).
    r'|canal\+(?! ?sport ?[1-9]\b)(?! ?[1-9]\b)'
    r'|tennis\+|golf\+|\bfoot\+|automoto|motogp|infosport'
    # v3.6.2 — chaînes FR du guide sans marque explicite (6ter, Arte…) :
    r'|6ter|\barte\b|\bc8\b|\blci\b|\bbfm\b')

# v3.6.1 — les variantes ÉTRANGÈRES des mêmes groupes (Canal+ Sport SK/CZ/
# Poland, DAZN Spain/DE, beIN Arabic/Malaysia…) ne sont pas des sources
# françaises même si leur nom contient une marque FR. Testé sur le nom en
# minuscules, APRÈS le test de la langue explicite.
NOT_FRENCH_NAME_RE = re.compile(
    r'\b(sk|cz|pl|de|es|pt|it|nl|ro|rs|hr|bg|gr|tr|ua|ru)\b'
    r'|poland|spain|deutschland|germany|portugal|italy|belgium|netherlands'
    r'|nederland|romania|serbia|croatia|bulgaria|greece|hungary|slovakia'
    r'|slovenia|ukraine|russia|arabia|arabic|mena|t[uü]rkiye|turkey|malaysia'
    r'|australia|qatar|israel|kazakhstan|english|espa[nñ]ol|italiano'
    r'|portugu[eè]s|deutsch|nederlands|scandinavia|sweden|norway|denmark'
    r'|finland|usa',
    re.IGNORECASE)


def _french_only():
    """Réglage « Uniquement les sources françaises » (activé par défaut)."""
    try:
        return (ADDON.getSetting('french_only') or 'true').lower() == 'true'
    except Exception:
        return True


def _hide_finished():
    """v3.7.22 — réglage « Masquer les matchs terminés » (activé par défaut :
    le samedi après-midi, des dizaines de matchs gris « fini » s'empilent en
    tête de la grille, triée chronologiquement, avant les matchs en direct
    et à venir)."""
    try:
        return (ADDON.getSetting('hide_finished') or 'true').strip().lower() == 'true'
    except Exception:
        return True


def _is_french_source(name, lang):
    """True si la chaîne/le serveur est en français (code pays, langue ou nom).
    v3.7.1 : un code langue explicite non français (PL, US, GB…) rejette la
    chaîne AVANT tout test de nom — fin des Canal+ Sport 3/5 polonaises."""
    try:
        lang_s = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip().lower()
        name_s = six.ensure_text(name or u'', encoding='utf-8', errors='replace').lower()
    except Exception:
        return False
    if lang_s in FRENCH_LANGS:
        return True
    if lang_s and lang_s in NON_FR_LANGS:
        return False
    if name_s and NOT_FRENCH_NAME_RE.search(name_s):
        return False
    return bool(name_s and FRENCH_NAME_RE.search(name_s))


def _db_channel(channel_name):
    """Cherche une chaîne par nom dans la base locale (channels.json).
    v3.7.8 : la base est indexée UNE fois par processus (dict nom -> entrée)
    au lieu d'un parcours linéaire à chaque appel — la grille de 100 matchs
    s'affiche nettement plus vite quand la base de chaînes est grosse."""
    try:
        wanted = (channel_name or '').replace(' ', '')
        return _db_index().get(wanted)
    except Exception:
        return None


_DB_INDEX = {'built': False, 'map': {}}


def _db_index():
    if _DB_INDEX['built']:
        return _DB_INDEX['map']
    idx = {}
    try:
        for channel in load_data_from_json():
            idx[channel.get('channelName', '').replace(' ', '')] = channel
    except Exception:
        pass
    _DB_INDEX['map'] = idx
    _DB_INDEX['built'] = True
    return idx


_LANG_ALIASES = {'french': u'fr', u'français': u'fr', 'francais': u'fr',
                 'english': u'en', 'eng': u'en'}


def _channel_lang(name, lang):
    """v3.7.10 — code langue affichable d'une chaîne (« fr », « de », « it »…).
    Le code explicite du guide/de la base fait foi (règle 3.7.1 : un code
    non français rejette la chaîne) ; sans code, une chaîne reconnue
    française par son nom vaut « fr », une inconnue renvoie '' (rien à
    afficher plutôt qu'un code faux)."""
    lang_s = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip().lower()
    if lang_s:
        if lang_s in FRENCH_LANGS:
            return u'fr'
        if lang_s in _LANG_ALIASES:
            return _LANG_ALIASES[lang_s]
        if re.match(r'^[a-z]{2,3}$', lang_s):
            return lang_s
        return u''
    if name and _is_french_source(name, u''):
        return u'fr'
    return u''


def _match_langs(channels):
    """v3.7.10 — langues distinctes d'un match, « fr » d'abord puis ordre
    alphabétique ('fr' ; 'de', 'it' ; 'de it us'…). Mêmes sources que la liste
    des liens à l'ouverture (dicts du guide ou base locale channels.json) :
    ce qui est écrit dans le titre est exactement ce qu'on trouve dedans."""
    found = []
    try:
        for ch in channels or []:
            if isinstance(ch, dict):
                tag = _channel_lang(ch.get('name'), ch.get('language'))
            else:
                nm = six.ensure_text(ch, encoding='utf-8', errors='replace')
                row = _db_channel(nm)
                if row is not None:
                    tag = _channel_lang(row.get('channelName'), row.get('language'))
                else:
                    tag = _channel_lang(nm, u'')
            if tag and tag not in found:
                found.append(tag)
    except Exception:
        pass
    rest = sorted(l for l in found if l != u'fr')
    return ([u'fr'] + rest) if u'fr' in found else rest


def _lang_suffix(langs):
    """v3.7.11 — suffixe du titre : codes langue en MAJUSCULES (FR, DE, IT…)
    en MAGENTA — couleur non utilisée ailleurs dans l'add-on (or = équipes,
    vert = EN DIRECT et FR de la liste des liens, cyan = heure, gris =
    miroirs) ; « FR » toujours premier et en gras, les autres suivent dans
    l'ordre alphabétique. '' si aucune langue identifiée (titre inchangé).
    v3.7.16 — quand « Uniquement les sources françaises » est actif (défaut),
    seul FR est affiché : les autres langues ne concernent pas le filtrage
    français et alourdissent la ligne du match."""
    if _french_only():
        langs = [u'fr'] if u'fr' in langs else []
    parts = []
    for l in langs:
        if l == u'fr':
            parts.append(u'[COLOR magenta][B]FR[/B][/COLOR]')
        else:
            parts.append(u'[COLOR magenta]{0}[/COLOR]'.format(l.upper()))
    return (u' ' + u' '.join(parts)) if parts else u''


def _channels_have_french(channels):
    """True si le match a au moins une chaîne française. Les chaînes
    indéterminées (base absente, nom inconnu) ne masquent pas le match :
    le filtrage fin se fait à l'ouverture du match."""
    identified = False
    for ch in channels:
        if isinstance(ch, dict):
            identified = True
            if _is_french_source(ch.get('name'), ch.get('language')):
                return True
        else:
            row = _db_channel(six.ensure_text(ch, encoding='utf-8', errors='replace'))
            if row is not None:
                identified = True
                if _is_french_source(row.get('channelName'), row.get('language')):
                    return True
    return not identified


def _servers_have_french(servers):
    """True si au moins un serveur (sites annexes) est en français."""
    for srv in servers or []:
        if isinstance(srv, dict):
            if _is_french_source(srv.get('name'), srv.get('language')):
                return True
        elif isinstance(srv, (list, tuple)) and srv:
            if _is_french_source(srv[0], ''):
                return True
        elif isinstance(srv, six.string_types):
            if _is_french_source(srv, ''):
                return True
    return False


# Les trois miroirs du service principal sont affichés discrètement
# « un », « deux », « trois » au lieu des domaines réels.
_MIRROR_LABELS = ((r'vertex\.st', u'un'), (r'nexa\.st', u'deux'), (r'kora\.st', u'trois'))


def _link_label(link):
    """Libellé affiché après le nom de la chaîne : « un / deux / trois »
    pour les miroirs vertex.st / nexa.st / kora.st (sous-domaines inclus,
    ex. s1.nexa.st), sinon le domaine réel."""
    host = (urlparse(link).netloc or '').lower()
    for rx, lbl in _MIRROR_LABELS:
        if re.search(r'(?:^|\.)' + rx + '$', host):
            return lbl
    return host


# ---------------------------------------------------------------------------
# v3.7.8 — ergonomie / visuel
# ---------------------------------------------------------------------------

def _setting_bool(key, default='true'):
    """Petit accesseur réglage booléen (settings.xml), tolérant aux pannes."""
    try:
        return (ADDON.getSetting(key) or default).strip().lower() == 'true'
    except Exception:
        return default == 'true'


def _plain(text):
    """Retire les balises Kodi ([B] [I] [CR] [COLOR x]…) pour les champs
    infoLabels (le panneau d'information du skin les affiche en clair)."""
    try:
        return re.sub(r'\[/?(?:B|I|CR|UPPERCASE|LOWERCASE|COLOR[^\]]*)\]',
                      u'', text or u'', flags=re.IGNORECASE).strip()
    except Exception:
        return text or u''


# v3.7.16 — les icônes emoji des sports (ballon, raquette, panier…) ont été
# RETIRÉES : elles ne s'affichaient pas sur plusieurs appareils Kodi/Android
# (aucune icône visible ou cases vides dans les listes). Le réglage « Icônes
# emoji des sports » disparaît en même temps ; la liste des matchs est
# désormais identique sur tous les écrans.


def _chan_label(chan, lang, fr):
    """Libellé d'un lien dans la liste d'un match : chaîne en or, langue en
    vert quand c'est du français (gris sinon)."""
    lang = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip()
    tag = u''
    if fr:
        tag = u'[COLOR lime]{0}[/COLOR]'.format(lang.upper() or u'FR')
    elif lang:
        tag = u'[COLOR dimgray]{0}[/COLOR]'.format(lang)
    return u'[COLOR gold]{0}[/COLOR]{1}'.format(chan, u' ' + tag if tag else u'')


# ---------------------------------------------------------------------------
# v3.7.25 (A) — RATTACHEMENT DE SECOURS PAR CHAÎNE : un match a ses chaînes
# mais le guide n'a fourni AUCUN lien (grille servie à moitié remplie, mise à
# jour en cours). Le même canal porte TOUJOURS les mêmes liens dans toute la
# grille (mesuré 03/11/2026 : 54 canaux porteurs, 0 divergence), donc les
# liens de CES chaînes lus sur les AUTRES matchs de la grille sont les bons.
# La grille est déjà sur le disque (fastcache) : AUCUNE requête réseau, et
# AUCUN lien ajouté — on ne fait que re-attacher ce que le guide lui-même a
# déjà associé à ces mêmes chaînes.
# ---------------------------------------------------------------------------
_GRID_INDEX_TTL = 4 * 3600
_grid_idx_cache = {'at': 0.0, 'idx': {}}


def _grid_links_index():
    """Index nom de chaîne (minuscules, sans espaces) -> (nom, langue,
    liens) reconstruit depuis les grilles en cache disque. Recalculé au
    plus toutes les 10 minutes (coût local : lecture d'un cache déjà en
    mémoire). PAS de correspondance par numéro : les numéros de canaux se
    répètent entre sports (mesuré : 22 collisions, ex. n°1 = TNT Sports 1
    OU Sportsnet+)."""
    now = time.time()
    if _grid_idx_cache['idx'] and (now - _grid_idx_cache['at']) < 600:
        return _grid_idx_cache['idx']
    idx = {}
    if fastcache is not None:
        for key in ('ev:' + Live_url, 'ev:' + Live_fallback_url):
            grid = fastcache.fstale(key, _GRID_INDEX_TTL) or []
            try:
                for m in grid:
                    if not isinstance(m, dict):
                        continue
                    for c in (m.get('channels') or []):
                        if not isinstance(c, dict):
                            continue
                        try:
                            links = [l for l in
                                     ((c.get('links') or []) + (c.get('oldLinks') or []))
                                     if l]
                        except Exception:
                            links = []
                        if not links:
                            continue
                        nm = six.ensure_text(c.get('name') or '', encoding='utf-8',
                                             errors='replace').strip()
                        if not nm:
                            continue
                        lang = six.ensure_text(c.get('language') or '', encoding='utf-8',
                                               errors='replace').strip()
                        k = re.sub(r'\s+', '', nm.lower())
                        if k and k not in idx:
                            idx[k] = (nm, lang, links)
            except Exception:
                continue
    _grid_idx_cache['at'] = now
    _grid_idx_cache['idx'] = idx
    return idx


def _borrow_links_from_grid(channels):
    """[(url, libellé)] : liens de secours pour les chaînes du match restées
    sans liens dans l'entrée du guide. Le filtre « sources françaises » est
    respecté (les liens empruntés portent la langue de LEUR chaîne)."""
    if not channels:
        return []
    idx = _grid_links_index()
    if not idx:
        return []
    fr_only = _french_only()
    out = []
    seen = set()
    for c in channels:
        ent = None
        try:
            if isinstance(c, dict):
                if [l for l in ((c.get('links') or []) + (c.get('oldLinks') or [])) if l]:
                    continue              # cette chaîne a déjà ses propres liens
                nm = six.ensure_text(c.get('name') or '', encoding='utf-8',
                                     errors='replace').strip()
                lang = six.ensure_text(c.get('language') or '', encoding='utf-8',
                                       errors='replace').strip()
                if nm:
                    ent = idx.get(re.sub(r'\s+', '', nm.lower()))
            else:
                # chaîne « string » (mode dégradé ou champ `channel`) : le nom
                # peut porter un code pays collé (« Canal+ Foot FR ») — on
                # essaie le nom TEL QUEL d'abord (« Sky Sport DE » est un vrai
                # nom de canal), puis débarassé de son suffixe.
                raw = six.ensure_text(c, encoding='utf-8', errors='replace').strip()
                stripped = _strip_lang_suffix(raw)
                nm = raw
                lang = u''
                if raw:
                    ent = idx.get(re.sub(r'\s+', '', raw.lower()))
                if not ent and stripped:
                    ent = idx.get(re.sub(r'\s+', '', stripped.lower()))
                    if ent:
                        nm = stripped
            if not nm or not ent:
                continue
            bnm, blang, links = ent
            fr = _is_french_source(bnm or nm, blang or lang)
            if fr_only and not fr:
                continue
            for u in links:
                if u in seen:
                    continue
                seen.add(u)
                out.append((u, _chan_label(bnm or nm, blang or lang, fr)))
        except Exception:
            continue
    return out


def get_stream(name, url):  # 4
    data = six.ensure_text(base64.b64decode(unquote(url))).strip('\n')

    import ast
    fr_only = _french_only()
    sstreams = []
    try:
        chan_data = ast.literal_eval(data)
    except Exception:
        chan_data = []
    for event in chan_data:
        if not isinstance(event, dict):  # TNT Sports 1
            datos = get_links_for_channel(event) or []
            # xbmc.log('SHOW DATOS: {}'.format(datos))
            for chan, link, lang in datos:
                chan = six.ensure_text(chan, encoding='utf-8', errors='replace')
                lang = six.ensure_text(lang, encoding='utf-8', errors='replace')
                fr = _is_french_source(chan, lang)
                if fr_only and not fr:
                    continue
                # v3.7.8 — chaîne en or, langue en vert FR / gris
                sstreams.append((link, _chan_label(chan, lang, fr)))

        else:
            fr = _is_french_source(event.get('name'), event.get('language'))
            if fr_only and not fr:
                continue
            link = event.get('links', [])
            lang = six.ensure_text(event.get('language', ''), encoding='utf-8', errors='replace')
            chan = six.ensure_text(event.get('name', ''), encoding='utf-8', errors='replace')
            # v3.7.8 — chaîne en or, langue en vert FR / gris
            for url_ in link:
                sstreams.append((url_, _chan_label(chan, lang, fr)))

    if len(sstreams) < 1:
        # v3.7.25 (A) — rattachement de secours par chaîne : les liens de CES
        # chaînes tels qu'ils figurent sur les autres matchs de la grille
        # (cache disque, zéro requête, zéro lien ajouté).
        try:
            sstreams = _borrow_links_from_grid(chan_data)
        except Exception:
            sstreams = []
    # v3.7.28 (bêta) — « Ma playlist » : les chaînes de LA PLAYLIST DE
    # L'UTILISATEUR (URL collée dans Paramètres > Expérimental (bêta), elle
    # ne quitte JAMAIS cet appareil et n'apparaît nulle part dans le dépôt)
    # dont le nom correspond à une chaîne du match s'ajoutent EN FIN de
    # liste, libellées « Ma playlist : … » — lecture DIRECTE du flux
    # (ts / m3u8), aucun résolveur, aucun inputstream ajouté (verrou).
    pl_items = []
    try:
        pl_items = _mypl_items_for(chan_data)
    except Exception:
        pl_items = []
    if pl_items:
        _pl_seen = set(i[0] for i in sstreams)
        pl_items = [(u, lbl) for u, lbl in pl_items if u not in _pl_seen]
        sstreams = sstreams + pl_items
    if len(sstreams) < 1:
        if fr_only:
            control.infoDialog(u"[COLOR gold]Aucune source française pour ce match.[/COLOR]\n"
                               u"[COLOR lime]Réglage : « Uniquement les sources françaises » dans les paramètres.[/COLOR]",
                               NAME, ICON, 7000)
        else:
            control.infoDialog(u"[COLOR gold]Aucun lien disponible pour le moment.\n "
                               u"[COLOR lime]Réessayez plus tard ![/COLOR]", NAME, ICON, 5000)
        return
    else:
        if len(sstreams) > 1:
            # v3.7.8 — liens numérotés (1. 2. 3.…) pour s'y retrouver, miroir
            # (un/deux/trois) en gris discret, fiche en texte propre.
            # v3.7.22 — chaque entrée porte le paquet complet des liens du
            # match ({"l": […], "i": rang cliqué}) : si le lien cliqué est
            # mort, la lecture bascule automatiquement sur le suivant.
            # v3.7.28 (bêta) — les entrées « Ma playlist » (flux directs de
            # l'utilisateur) sont HORS paquet de bascule auto (réservé aux
            # liens du guide, qui restent prioritaires) et se jouent en
            # DIRECT via play_mypl, sans résolveur.
            _pl_urls = set(u for u, _lbl in pl_items)
            links_all = [i[0] for i in sstreams if i[0] not in _pl_urls]
            for idx, i in enumerate(sstreams, 1):
                title, link = i[1], i[0]
                if link in _pl_urls:
                    num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
                    info = {'title': _plain(title), 'sorttitle': '',
                            'plot': _plain(name)}
                    addDir(num + u'[COLOR skyblue]' + title + u'[/COLOR]',
                           _pl_payload(link), 'play_mypl', ICON, name,
                           isFolder=False, infoLabels=info)
                    continue
                lbl = _link_label(link)
                if lbl and 'vecdn' not in link and str(link) != str(title):
                    title = u'{0} [COLOR dimgray]· {1}[/COLOR]'.format(title, lbl)
                num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
                info = {'title': _plain(title), 'sorttitle': '', 'plot': _plain(name)}
                addDir(num + title, json.dumps({'l': links_all, 'i': idx - 1}),
                       'play_stream', ICON, name,
                       isFolder=False, infoLabels=info)
            # v3.7.22/3.7.23 — pré-résolution en ARRIÈRE-PLAN : du dernier
            # lien qui a marché pour ce match s'il est connu (re-clic
            # instantané), sinon du premier lien affiché. Le flux est prêt
            # (cache 10 min) avant même le clic. (v3.7.28 : liens du guide
            # seulement — un flux « Ma playlist » n'a rien à résoudre.)
            if links_all:
                _prewarm_link(name, _last_good_link(links_all) or links_all[0])
            xbmcplugin.setContent(_handle, 'videos')
            xbmcplugin.endOfDirectory(_handle)

        else:
            if sstreams[0][0] in set(u for u, _lbl in pl_items):
                play_mypl(name, _pl_payload(sstreams[0][0]))
            else:
                resolve2(name, sstreams[0][0])


def xbmc_curl_encode(url, headers):
    return "{}|{}".format(url, urlencode(headers))


def _resolve2_impl(name, url, quiet=False):
    """v3.7.8 : extrait l'URL finale du flux et la RETOURNE (la lecture,
    elle, est faite par resolve2() qui affiche la roue d'attente pendant
    l'extraction). v3.7.22 : quiet=True (pré-résolution en arrière-plan)
    supprime tout accès à l'interface (notifications, fenêtres de debug) —
    un thread d'arrière-plan ne doit jamais toucher à l'écran."""
    stream_url = ''
    ua_win = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.120 Safari/537.36'
    ua = 'Mozilla/5.0 (iPad; CPU OS 15_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.1 Mobile/15E148 Safari/604.1'

    resolved = ['//dabac', '//sansat', '//istorm', '//zvision', '//glisco', '//bedsport', '//coolrea', '//evfancy',
                '//s2watch', '//vuen', '//gopst']
    # new_streams = ['//dabac']
    log_info('RESOLVE-URL: {0}'.format(url))

    # NEW GLISCO/SANSAT BRANCH
    if '//glisco' in url or '//sansat' in url or 'vertex' in url or 'nexa' in url or 'kora.st' in url:
        if not quiet:
            Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        chan_id = url.split('id=')[-1]

        def _extract_stream(embed_html):
            # A channel's api/player.php can hand back several player types; try
            # each extraction in turn. Returns (method, url); method is set even
            # on failure so the log can say *why* a mirror produced no stream
            # (e.g. econfig-blank = the host blanked the URL for Kodi's TLS).
            # 1) ?ppcfg=1 JSON  (dinamic-embed.site, tursatrueper.xyz/pink.php)
            try:
                cfg = json.loads(embed_html)
                fl = cfg.get('src') or cfg.get('srcBase')
                if fl:
                    return ('ppcfg', fl)
            except Exception:
                pass
            # 2) character-array return([...].join(""))  (forgemindly.com)
            arr_m = re.search(r'return\s*\(\s*(\[(?:"[^"]*",?\s*)+\])', embed_html)
            if arr_m:
                fl = ''.join(re.findall(r'"([^"]*)"', arr_m.group(1))).replace('\\/', '/')
                if fl:
                    return ('char-array', fl)
            # 3) window._econfig blob  (talentedlost.click / fisherman / wilderness)
            try:
                from resources.modules import econfig as _econfig
                if _econfig.find_econfig(embed_html):
                    fl, _ = _econfig.extract_stream_from_html(embed_html)
                    # fl == '' means the blob decoded but the host blanked the
                    # stream_url (TLS-fingerprint gating) -> report it distinctly.
                    return ('econfig' if fl else 'econfig-blank', fl or None)
            except Exception:
                pass
            # 4) window.PP_CLAPPR_CONFIG in the plain page  (play.matchli.st
            #    clappr-classic.php answers 403 to ?ppcfg=1)
            pp = re.search(r'window\.PP_CLAPPR_CONFIG\s*=\s*(\{.*?\});\s*</script>', embed_html, re.DOTALL)
            if pp:
                try:
                    cfg = json.loads(pp.group(1))
                    fl = cfg.get('src') or cfg.get('srcBase')
                    if fl:
                        return ('pp-clappr-config', fl)
                except Exception:
                    pass
            return (None, None)

        # The same channel is mirrored across sub-domains (nexa.st, s1/s2/s3.
        # nexa.st, vertex.st ...) and each mirror routes to a *different* embed
        # host. Some hosts blank the stream for Kodi's old TLS fingerprint, so
        # when the caller's host yields nothing we retry the other mirrors.
        import time as _time
        uri0 = urlparse(url)
        hosts = [uri0.netloc]
        m = re.match(r'^(?:s\d+\.)?(nexa\.st|vertex\.st|kora\.st)$', uri0.netloc)
        if m:
            base = m.group(1)
            for h in (base, 's2.' + base, 's1.' + base, 's3.' + base):
                if h not in hosts:
                    hosts.append(h)

        # v3.7.7 — clic accéléré : les réponses api/player.php (l'URL du
        # lecteur embarqué de chaque miroir) sont stables pendant toute la
        # durée d'un événement -> cache disque 30 min (fastcache) et
        # récupération EN PARALLÈLE sur tous les miroirs en une seule salve.
        # Avant : 2 à 4 serveurs interrogés l'un après l'autre, jusqu'à
        # ~2 min quand un miroir est tombé ; maintenant la salve attend le
        # plus lent (souvent < 2 s) et le 2e clic ne rappelle plus l'api.
        def _api_key(h):
            return 'gap:{0}:{1}'.format(h, chan_id)

        api_results = {}
        to_fetch = []
        for h in hosts:
            v = fastcache.fget(_api_key(h), 1800) if fastcache is not None else None
            if v:
                api_results[h] = v
            else:
                to_fetch.append(h)

        if to_fetch:
            def _grab(h):
                try:
                    r = client.request(
                        '{0}://{1}/api/player.php?id={2}'.format(uri0.scheme, h, chan_id),
                        referer='{0}://{1}/'.format(uri0.scheme, h), timeout='12')
                    if r:
                        api_results[h] = six.ensure_text(r, encoding='utf-8', errors='replace')
                        if fastcache is not None:
                            fastcache.fset(_api_key(h), api_results[h])
                except Exception:
                    pass
            if fastcache is not None:
                fastcache.parallel_map(_grab, to_fetch, workers=4)
            else:
                for _h in to_fetch:
                    _grab(_h)

        # v3.7.22 — phase 2 (extraction du flux) en SALVE PARALLÈLE : tous
        # les miroirs sont testés EN MÊME TEMPS (cfg ?ppcfg=1, puis page du
        # cadre), et le premier qui rend un flux dans l'ordre de priorité
        # des miroirs gagne. Avant : miroirs interrogés l'un après l'autre —
        # un miroir muet (TLS bloqué) ajoutait 2 requêtes pleines avant
        # d'atteindre le bon (3 à 6 s perdus).
        def _phase2(h):
            try:
                api_ref = '{0}://{1}/'.format(uri0.scheme, h)
                api_resp = api_results.get(h)
                if not api_resp:
                    # ni cache ni salve : un ultime essai direct, vite abandonné
                    api_resp = six.ensure_text(client.request(
                        '{0}://{1}/api/player.php?id={2}'.format(uri0.scheme, h, chan_id),
                        referer=api_ref, timeout='6') or '')
                if not api_resp:
                    return None
                cand_frame = json.loads(api_resp)['url']
                cfg_url = cand_frame + ('&' if '?' in cand_frame else '?') + 'ppcfg=1&_=' + str(int(_time.time() * 1000))
                try:
                    embed_html = six.ensure_str(
                        client.request(cfg_url, referer=api_ref, headers={'User-Agent': ua_win},
                                       timeout='8') or '')
                except Exception:
                    embed_html = ''  # e.g. 403 on ?ppcfg=1 -> plain page below
                method, cand = _extract_stream(embed_html)
                if not cand:  # ?ppcfg=1 refused -> plain frame page
                    try:
                        method, cand = _extract_stream(six.ensure_str(
                            client.request(cand_frame, referer=api_ref,
                                           headers={'User-Agent': ua_win}, timeout='8')))
                    except Exception:
                        pass
                if cand:
                    return (h, cand_frame, cand, method)
                log_info('mirror {0} -> {1}: no stream ({2})'.format(
                    h, urlparse(cand_frame).netloc, method or 'unknown player'))
            except Exception as _me:
                log_info('mirror {0}: error {1!r}'.format(h, _me))
            return None

        if fastcache is not None:
            # v3.7.22 — « premier prêt » : à chaque instant, le meilleur
            # classé parmi les miroirs qui ONT RÉPONDU gagne ; un miroir
            # muet/lent ne retarde plus les autres (map_calls, lui, attend
            # tout le monde — un serveur à 8 s aurait retardé un gagnant
            # répondu en 1 s).
            pick, _pidx = fastcache.first_ready(
                [lambda h=h: _phase2(h) for h in hosts],
                 workers=4, max_wait=8)  # v3.7.26 — 8 s (bon miroir : 0,3-1 s)
        else:
            phase2 = [_phase2(h) for h in hosts]
            pick = next((r for r in phase2 if r), None)
        flink = None
        frame = None
        if pick:
            _phost, frame, flink, _pmethod = pick
            log_info('mirror {0} -> {1}: OK via {2}'.format(
                _phost, urlparse(frame).netloc, _pmethod))
        if not flink:
            raise Exception('could not extract stream src for ' + url)

        cookie_str = ''
        # Origin / Referer must match the winning iframe host.
        frame_origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(frame))
        stream_headers = {
            'Referer': frame_origin + '/',
            'Origin': frame_origin,
            'User-Agent': ua_win,
            'verifypeer': 'false',
        }
        if cookie_str:
            stream_headers['Cookie'] = cookie_str

        stream_url = xbmc_curl_encode(flink, stream_headers)

    elif any(i in url for i in resolved):
        if not quiet:
            Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        referer = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(url))
        r = six.ensure_str(client.request(url, timeout='12'))
        if 'get_content.php?channel=' in r or 'api/player.php?id=' in r:
            id_ = re.findall(r'(\d+)$', url)[0]
            if 'get_content.php?channel=' in r:
                frame = referer + "get_content.php"
                hdr = {
                    'referer': url,
                    # 'sec-fetch-mode': 'cors',
                    'user-agent': ua_win,
                }
                params = {'channel': id_, }
            elif 'api/player.php?id=' in r:
                frame = referer + "api/player.php"
                hdr = {
                    'referer': url,
                    # 'sec-fetch-mode': 'cors',
                    'user-agent': ua_win,
                }
                params = {'id': id_, }
            r = six.ensure_str(requests.get(frame, params=params, headers=hdr, timeout=12).content)
        if 'fid=' in r:
            regex = '''<script>fid=['"](.+?)['"].+?text/javascript.*?src=['"](.+?)['"]></script>'''
            vid, getembed = re.findall(regex, r, re.DOTALL)[0]
            getembed = 'https:' + getembed if getembed.startswith('//') else getembed
            embed = six.ensure_str(client.request(getembed, timeout='12'))
            embed = re.findall(r'''document.write.+?src=['"](.+?player)=''', embed, re.DOTALL)[0]
            host = '{}=desktop&live={}'.format(embed, str(vid))
            data = six.ensure_str(client.request(host, referer=referer, timeout='12'))
            try:
                link = re.findall(r'''return\((\[.+?\])\.join''', data, re.DOTALL)[0]
            except IndexError:
                link = re.findall(r'''file:.*['"](http.+?)['"]\,''', data, re.DOTALL)[0]

            flink = link.replace('[', '').replace(']', '').replace('"', '').replace(',', '').replace('\/', '/')
            stream_headers = {'Referer': host.split('embed')[0], 'User-Agent': ua_win}
            stream_url = xbmc_curl_encode(flink, stream_headers)
        else:
            try:
                frame = json.loads(r).get('url')
            except Exception:
                frame = client.parseDOM(r, 'iframe', ret='src')[-1]
            # xbmc.log('FRAME: {}'.format(frame))
            referer = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(frame))
            data = six.ensure_str(client.request(frame, referer=url, output=url, timeout='12'))
            try:
                data = re.findall(r'''script>(eval.+?\{\}\))\)''', data, re.DOTALL)[-1]
                from resources.modules import jsunpack
                data = six.ensure_text(jsunpack.unpack(str(data) + ')'), encoding='utf-8')
            except Exception:
                pass
            # xbmc.log('DATAAAA: {}'.format(data))

            if '"h","t","t","p"' in data:
                link = re.findall(r'''return\((\[.+?\])\.join''', data, re.DOTALL)[0]
                link = json.loads(link)
                link = "".join(link)
                flink = link.replace('////', '//')

            elif 'player.src({src:' in data:
                flink = re.findall(r'''player.src\(\{src:\s*["'](.+?)['"]\,''', data, re.DOTALL)[0]
            elif 'hlsjsConfig' in data and not 'new Clappr' in data:
                try:
                    if 'data-page=' in data:
                        data_page = client.parseDOM(data, 'div', ret='data-page', attrs={'id': 'app'})[0]
                        data_page = client.replaceHTMLCodes(data_page)
                        try:
                            data_page = data_page.replace('\/', '/')
                            flink = re.findall(r'''(https?:\/\/[^\s]+\.m3u8)''', data_page, re.DOTALL)[0]
                        except Exception:
                            data_page = json.loads(data_page)
                            flink = data_page['props']['streamData']['streamurl']
                    else:
                        if quiet:
                            log_debug('hlsjsConfig data kept for log ({0} chars)'.format(len(data)))
                        else:
                            xbmcgui.Dialog().textviewer('data', str(data))
                        hlsurl, pk, ea = \
                            re.findall('.*hlsUrl\s*=\s*"(.*?&\w+=)".*?var\s+\w+\s*=\s*"([^"]+).*?>\s*ea\s*=\s*"([^"]+)',
                                       data,
                                       re.DOTALL)[0]
                        pk = pk[:53] + pk[53 + 1:]
                        link = hlsurl.replace('" + ea + "', ea) + pk
                        link_data = six.ensure_str(client.request(link, timeout='12'))
                        flink = re.findall('.*(http.+?$)', link_data)[0]
                except Exception as e:
                    if quiet:
                        log_debug('hlsjsConfig fallback: {0!r}'.format(e))
                    else:
                        xbmcgui.Dialog().textviewer('e', str(e))
                    flink = re.findall(r'''src=\s*["'](.+?)['"]''', data, re.DOTALL)[0]
            elif 'new Clappr' in data:
                flink = re.findall(r'''source\s*:\s*["']?(.+?)['"]?\,''', str(data), re.DOTALL)[0]
                # xbmc.log('FLINKKK: {}'.format(flink))
                if flink == "m3u8Url" or flink == "m3u8":
                    html = six.ensure_str(data)
                    m = re.search(r'const\s+CHANNEL_KEY\s*=\s*["\']([\w-]+)["\']', html)
                    if not m:
                        raise Exception('CHANNEL_KEY not found')
                    channel_key = m.group(1)

                    m = re.search(r'const\s+BUNDLE\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', html)
                    if not m:
                        raise Exception('BUNDLE not found')
                    bundle_b64 = m.group(1)

                    parts_raw = base64.b64decode(bundle_b64)
                    parts = json.loads(six.ensure_str(parts_raw))
                    for k in list(parts.keys()):
                        try:
                            parts[k] = six.ensure_str(base64.b64decode(parts[k]))
                        except Exception:
                            pass

                    auth_url = (
                            parts.get('b_host', '') +
                            parts.get('b_script', '') +
                            '?channel_id=' + quote_plus(channel_key) +
                            '&ts=' + quote_plus(parts.get('b_ts', '')) +
                            '&rnd=' + quote_plus(parts.get('b_rnd', '')) +
                            '&sig=' + quote_plus(parts.get('b_sig', ''))
                    )

                    pu = urlparse(referer)
                    origin = '{}://{}'.format(pu.scheme or 'https', pu.netloc)
                    ref_for_headers = frame or referer
                    try:
                        client.request(auth_url, referer=ref_for_headers, timeout='12')
                    except Exception:
                        pass

                    # server_lookup για server_key
                    lookup_url = urljoin(origin, '/server_lookup.php?channel_id=' + quote_plus(channel_key))
                    body = six.ensure_str(client.request(lookup_url, referer=ref_for_headers, timeout='12'))

                    server_key = ''
                    try:
                        resp = json.loads(body)
                        server_key = six.ensure_str(resp.get('server_key', '')).strip()
                    except Exception:
                        m = re.search(r'"server_key"\s*:\s*"([^"]+)"', body)
                        if m:
                            server_key = six.ensure_str(m.group(1)).strip()

                    if not server_key:
                        raise Exception('server_key not found')

                    sk_slug = server_key.strip().strip('/')  # π.χ. 'x4-cdn' ή 'x4-cdn/cdn'
                    if sk_slug == 'top1/cdn':
                        candidates = [
                            'https://top1.newkso.ru/top1/cdn/{}/mono.m3u8'.format(channel_key),
                            'http://top1.newkso.ru/top1/cdn/{}/mono.m3u8'.format(channel_key),
                        ]
                    else:
                        host_prefix = sk_slug.replace('/', '.')  # 'x4-cdn.cdn' αν έχει subpath
                        candidates = [
                            'https://{}.new.newkso.ru/{}/{}/mono.m3u8'.format(host_prefix, sk_slug, channel_key),
                            'https://new.newkso.ru/{}/{}/mono.m3u8'.format(sk_slug, channel_key),
                            'http://{}.new.newkso.ru/{}/{}/mono.m3u8'.format(host_prefix, sk_slug, channel_key),
                            'http://new.newkso.ru/{}/{}/mono.m3u8'.format(sk_slug, channel_key),
                        ]

                    candidates = [re.sub(r'(?<!:)/{2,}', '/', u) for u in candidates]

                    # v3.7.22 — sondes CDN EN PARALLÈLE, timeout 6 s (avant :
                    # 12 s chacune, l'une après l'autre — un serveur mort
                    # faisait attendre jusqu'à 3 sondes pleines avant que la
                    # bonne adresse ne soit essayée). Priorité conservée : la
                    # première adresse qui répond dans l'ordre de la liste
                    # gagne.
                    def _probe(u):
                        try:
                            chunk = client.request(u, referer=ref_for_headers, timeout='6')
                            s = six.ensure_str(chunk) if chunk is not None else ''
                            if s.startswith('#EXTM3U') or len(s) > 0:
                                return u
                        except Exception as e:
                            log_debug('probe fail: {0} -> {1}'.format(u, e))
                        return None

                    if fastcache is not None:
                        # v3.7.22 — « premier prêt » : la première sonde qui
                        # répond gagne, sans attendre les serveurs lents.
                        working, _pi = fastcache.first_ready(
                            [lambda u=u: _probe(u) for u in candidates],
                            workers=4, max_wait=6)   # v3.7.26 — sondes CDN : 6 s
                    else:
                        working = None
                        for u in candidates:
                            working = _probe(u)
                            if working:
                                break

                    if not working:
                        working = candidates[0]
                    # xbmc.log('FLINK (new Clappr): {}'.format(working))
                    flink = working


                elif flink == "src":
                    flink = re.findall(r'''src=\s*["'](.+?)['"]''', data, re.DOTALL)[0]
            elif 'player.setSrc' in data:
                flink = re.findall(r'''player.setSrc\(["'](.+?)['"]\)''', data, re.DOTALL)[0]
            elif 'new Player(' in data:
                player = re.findall(r'''new Player(\(.+?\))''', data,
                                    re.DOTALL)[0]
                import ast, random
                player = ast.literal_eval(player)
                p1 = player[3]
                p2 = random.choice(list(player[4].keys()))
                flink = 'https://{}/hls/{}/live.m3u8'.format(p2, p1)
            else:
                try:
                    data = data.replace('\/', '/')
                    flink = re.findall(r'(https?:\/\/[^\s]+\.m3u8)', data, re.DOTALL)[0]
                except Exception:
                    try:
                        flink = re.findall(r'''source:\s*["'](.+?)['"]''', data, re.DOTALL)[0]
                    except IndexError:
                        ea = re.findall(r'''ajax\(\{url:\s*['"](.+?)['"],''', data, re.DOTALL)[0]
                        ea = six.ensure_text(client.request(ea, timeout='12')).split('=')[1]
                        flink = re.findall('''videoplayer.src = "(.+?)";''', ea, re.DOTALL)[0]
                        flink = flink.replace('" + ea + "', ea)
            stream_headers = {'Referer': referer + '/', 'Origin': referer, 'User-Agent': ua_win,
                              'Connection': 'keep-alive'}
            stream_url = xbmc_curl_encode(flink, stream_headers)
    elif '//dabac' in url:
        if not quiet:
            Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        referer = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(url))
        frame = referer + "api/player.php?id={}"
        id = url.split("id=")[-1]
        nurl = frame.format(id)
        data = six.ensure_text(client.request(nurl))
        # xbmc.log("DATAAAAAA: {}".format(data))
        url = json.loads(data)["url"]
        data = requests.get(url, headers=headers, timeout=10).text
        iframe = re.findall(r'<iframe[^>]+src="([^"]+?)\+encodeURIComponent\(document\.referrer\)', data, re.DOTALL)[-1]
        ref = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(iframe))
        iframe += quote_plus(referer)
        hdr = {
            'User-Agent': ua_win,  # ή ua_mob
            'Referer': url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
        }
        data = six.ensure_text(requests.get(iframe, headers=hdr, timeout=10).text)
        # xbmc.log("DATAAAAAA2: {}".format(data))
        m = re.search(r'id="crf__"\s+value=[\'"]([^\'"]+)', data)
        if m:
            b64url = m.group(1)
            flink = base64.b64decode(b64url).decode("utf-8")
            stream_headers = {'Referer': ref + '/', 'Origin': ref, 'User-Agent': 'iPad'}
            stream_url = xbmc_curl_encode(flink, stream_headers)

    else:
        stream_url = url

    # xbmc.log('STREAM: {}'.format(stream_url))
    return stream_url


# ---------------------------------------------------------------------------
# v3.7.22 — vitesse d'ouverture des vidéos
#   • cache « res: » (10 min) du flux résolu : un 2e clic (mauvais miroir,
#     reprise après coupure) ne re-résout plus rien ;
#   • pré-résolution en arrière-plan du 1er lien dès l'affichage de la liste
#     des liens du match : le flux est prêt avant même le clic ;
#   • bascule automatique sur le lien suivant quand le lien cliqué est mort
#     (paquet {"l": [liens], "i": rang} porté par chaque entrée de lecture).
# ---------------------------------------------------------------------------
_RES_TTL = 600              # durée (s) du cache du flux résolu (v3.7.26 : 10 min)
_RES_INFLIGHT = set()       # pré-résolutions déjà en cours (anti-doublon)
_RES_LOCK = threading.Lock()


def _auto_next():
    """Réglage « Si un lien ne répond pas, essayer automatiquement le
    suivant » (activé par défaut)."""
    try:
        return (ADDON.getSetting('auto_next') or 'true').strip().lower() == 'true'
    except Exception:
        return True


def _parse_play_payload(url):
    """L'entrée d'une lecture est soit le lien brut, soit un paquet JSON
    {"l": [liens dans l'ordre affiché], "i": rang du lien cliqué}. Le paquet
    porte la liste complète du match pour la bascule automatique. Retourne
    (liste_liens, rang_0) — lien brut -> ([lien], 0)."""
    if url and url.startswith('{'):
        try:
            d = json.loads(url)
            lst = [six.ensure_text(x, encoding='utf-8', errors='replace')
                   for x in (d.get('l') or []) if x]
            if lst:
                i = d.get('i', 0)
                try:
                    i = int(i)
                except Exception:
                    i = 0
                if i < 0 or i >= len(lst):
                    i = 0
                return lst, i
        except Exception:
            pass
    return [url], 0


# v3.7.23 — MÉMOIRE DU DERNIER LIEN GAGNANT par match : quand une lecture
# réussit, le lien qui a marché est retenu (2 jours) ; à la prochaine
# lecture du même match, il est essayé en premier (après le lien cliqué)
# et c'est LUI que le préchauffage 3.7.22 résout en arrière-plan — on ne
# retombe plus sur un lien déjà mort.
_GOOD_TTL = 2 * 86400


def _links_key(links):
    """Clé stable d'un paquet de liens (indépendante de l'ordre affiché)."""
    try:
        joined = u'|'.join(sorted(
            six.ensure_text(x, encoding='utf-8', errors='replace') for x in links))
        return hashlib.md5(joined.encode('utf-8')).hexdigest()
    except Exception:
        return None


def _remember_good_link(links, link):
    if fastcache is None or not link:
        return
    ck = _links_key(links)
    if not ck:
        return
    try:
        fastcache.fset('good:' + ck, link)
        fastcache.flush()
    except Exception:
        pass


def _last_good_link(links):
    """Dernier lien qui a marché pour CE match, s'il est encore dans la
    liste affichée (sinon None)."""
    if fastcache is None:
        return None
    ck = _links_key(links)
    if not ck:
        return None
    try:
        good = fastcache.fget('good:' + ck, _GOOD_TTL)
    except Exception:
        return None
    if good and good in links:
        return good
    return None


def _play_order(links, idx, auto_next):
    """Ordre d'essai des liens : le lien cliqué d'abord, puis (si la
    bascule automatique 3.7.22 est active) le dernier lien qui a marché
    pour ce match, puis les autres dans l'ordre affiché."""
    order = [idx]
    if auto_next and len(links) > 1:
        good = _last_good_link(links)
        if good and good in links:
            gi = list(links).index(good)
            if gi not in order:
                order.append(gi)
        for j in range(len(links)):
            if j not in order:
                order.append(j)
    return order


def _resolve2_cached(name, link, quiet=False):
    """Résolution avec cache « res: » (10 min). Les en-têtes étant inclus
    dans l'URL finale (convention Kodi « url|Referer=… »), le flux en cache
    est jouable tel quel."""
    ck = 'res:' + link
    cached = fastcache.fget(ck, _RES_TTL) if fastcache is not None else None
    if cached:
        log_info('RESOLVE-CACHE hit: {0}'.format(link[:70]))
        return cached
    stream_url = _resolve2_impl(name, link, quiet=quiet)
    if stream_url and fastcache is not None:
        fastcache.fset(ck, stream_url)
        fastcache.flush()
    return stream_url


def _prewarm_link(name, link):
    """Pré-résolution en ARRIÈRE-PLAN du premier lien d'un match, lancée dès
    l'affichage de sa liste de liens : pendant que l'utilisateur lit la
    liste, le flux se résout et remplit le cache « res: » — au clic, la
    lecture démarre sans nouvelle résolution. Silencieux : un échec ici ne
    change rien au clic, qui garde son chemin complet."""
    if fastcache is None or not link:
        return
    try:
        if fastcache.fresh('res:' + link, _RES_TTL):
            return
    except Exception:
        pass
    with _RES_LOCK:
        if link in _RES_INFLIGHT:
            return
        _RES_INFLIGHT.add(link)

    def _run():
        try:
            _resolve2_cached(name, link, quiet=True)
        except Exception:
            pass
        finally:
            with _RES_LOCK:
                _RES_INFLIGHT.discard(link)

    try:
        t = threading.Thread(target=_run)
        t.daemon = True
        t.start()
    except Exception:
        with _RES_LOCK:
            _RES_INFLIGHT.discard(link)


def resolve2(name, url):
    """v3.7.8 — roue d'attente pendant la résolution (plus d'écran qui semble
    figé), message clair si aucun flux n'est extrait, lecture avec un titre
    propre et le bon type MIME HLS (démarrage plus fiable du lecteur).
    v3.7.22 — lecture immédiate du cache « res: » quand le préchauffage a
    déjà résolu (ni roue d'attente, ni résolution) ; bascule automatique sur
    le lien suivant quand le lien cliqué est mort (réglage, au plus 3
    essais)."""
    links, idx = _parse_play_payload(url)
    auto_next = _auto_next() and len(links) > 1
    stream_url = ''
    if fastcache is not None:
        pre = fastcache.fget('res:' + links[idx], _RES_TTL)
        if pre:
            stream_url = pre
            log_info('RESOLVE-CACHE hit (instant): {0}'.format(links[idx][:70]))
    if not stream_url:
        xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
        try:
            # v3.7.23 — ordre d'essai : lien cliqué, puis dernier lien qui a
            # marché pour ce match (mémoire 2 j), puis les autres ; les
            # messages reprennent les numéros AFFICHÉS (1. 2. 3.).
            order = _play_order(links, idx, auto_next)
            pos = 0
            tries = 0
            while pos < len(order):
                j = order[pos]
                tries += 1
                if tries > 1:
                    try:
                        Dialog.notification(
                            NAME,
                            u"[COLOR skyblue]Lien {0} inaccessible — essai du lien {1}…[/COLOR]".format(
                                order[pos - 1] + 1, j + 1),
                            ICON, 3000, False)
                    except Exception:
                        pass
                try:
                    stream_url = _resolve2_cached(name, links[j])
                except Exception as e:
                    log_info('resolve link {0}/{1}: {2!r}'.format(j + 1, len(links), e))
                    stream_url = ''
                if stream_url:
                    _remember_good_link(links, links[j])
                    break
                pos += 1
                if not auto_next or tries >= 3 or pos >= len(order):
                    break
        finally:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not stream_url:
        raise Exception(u'Flux introuvable pour ce lien — réessayez ou choisissez un autre serveur.')
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name), encoding='utf-8', errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name)})
    liz.setProperty("IsPlayable", "true")
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


################################################################################
#########################CHANNELS HELPERS#######################################
################################################################################

xbmcvfs.mkdir(control.translatePath(ADDON_DATA))
JSON_FILE_PATH = control.translatePath(ADDON_DATA + 'channels.json')
LAST_UPDATE_FILE = control.translatePath(ADDON_DATA + 'last_update.txt')


def is_time_to_update(hours=6):
    if not xbmcvfs.exists(LAST_UPDATE_FILE):
        f = control.openFile(LAST_UPDATE_FILE, 'w')
        f.write(str(time.time()))
        f.close()
    f = control.openFile(LAST_UPDATE_FILE, 'r')
    content = f.read()
    last_update_timestamp = float(content.strip())
    f.close()

    hours_in_seconds = hours * 60 * 60
    current_time = time.time()

    return (current_time - last_update_timestamp) >= hours_in_seconds


def fetch_and_store_channel_data():
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36'}
    try:
        url = BASEURL + '''api/trpc/mutual.getTopTeams,saves.getAllUserSaves,mutual.getFooterData,mutual.getAllChannels,mutual.getWebsiteConfig?batch=1&input={"0":{"json":null,"meta":{"values":["undefined"]}},"1":{"json":null,"meta":{"values":["undefined"]}},"2":{"json":null,"meta":{"values":["undefined"]}},"3":{"json":null,"meta":{"values":["undefined"]}},"4":{"json":null,"meta":{"values":["undefined"]}}}'''
        response = six.ensure_text(client.request(url, headers=hdrs), encoding='utf-8', errors='ignore')
        new_data = json.loads(response)

        new_channels_data = None
        for result in new_data:
            if ("result" in result and "data" in result["result"] and
                    "json" in result["result"]["data"] and "allChannels" in result["result"]["data"]["json"]):
                new_channels_data = result["result"]["data"]["json"]["allChannels"]
                break

        if not new_channels_data:
            return

        if not xbmcvfs.exists(JSON_FILE_PATH):
            existing_data = []
        else:
            file = xbmcvfs.File(JSON_FILE_PATH)
            existing_data = json.load(file)
            file.close()

        existing_data_map = {channel['_id']: channel for channel in existing_data}
        changes_made = False

        for new_channel in new_channels_data:
            channel_id = new_channel['_id']
            if channel_id not in existing_data_map or new_channel['links'] != existing_data_map[channel_id]['links']:
                existing_data_map[channel_id] = new_channel
                changes_made = True

        if changes_made:
            file = xbmcvfs.File(JSON_FILE_PATH, 'w')
            file.write(json.dumps(list(existing_data_map.values())))
            file.close()
            print("Channel links updated")

    except Exception as e:
        print("Error fetching or updating channel data: {}".format(e))


def load_data_from_json():
    # v3.7.8 — lecture disque une seule fois par version du fichier (mtime) :
    # la base est lue en boucle par l'index de chaînes et le compteur FR.
    try:
        mtime = xbmcvfs.Stat(JSON_FILE_PATH).st_mtime()
    except Exception:
        mtime = None
    if _DB_CACHE['data'] is not None and _DB_CACHE['mtime'] == mtime:
        return _DB_CACHE['data']
    if not xbmcvfs.exists(JSON_FILE_PATH):
        return []
    f = control.openFile(JSON_FILE_PATH, 'r')
    contents = f.read()
    f.close()
    try:
        data = json.loads(contents)
    except Exception:
        return []
    _DB_CACHE['mtime'] = mtime
    _DB_CACHE['data'] = data
    _DB_INDEX['built'] = False      # l'index suit la nouvelle version
    return data


_DB_CACHE = {'mtime': None, 'data': None}


def get_links_for_channel(channel_name):
    channels_data = load_data_from_json()
    for channel in channels_data:
        datos = []
        if channel['channelName'] == channel_name:
            for link in channel['links']:
                chan = channel['channelName']
                lang = channel['language']
                datos.append((chan, link, lang))
            return datos
        else:
            if channel['channelName'].replace(' ', '') == channel_name.replace(' ', ''):
                for link in channel['links']:
                    chan = channel['channelName']
                    lang = channel['language']
                    datos.append((chan, link, lang))
                return datos
    return None


###################################################
###################################################
###################################################
########### Time and Date Helpers #################
###################################################
def fetch_user_timezone():
    try:
        locale_timezone = json.loads(xbmc.executeJSONRPC(
            '{"jsonrpc": "2.0", "method": "Settings.GetSettingValue", "params": {"setting": "locale.timezone"}, "id": 1}'))

        if locale_timezone['result']['value']:
            local_tzinfo = gettz(locale_timezone['result']['value'])
            if local_tzinfo:
                log_debug('SHOW FINAL ΤΙΜΕΖΟΝΕ: {0}'.format(local_tzinfo))
                return local_tzinfo
            else:
                log_warning("Failed to get tzinfo for timezone: {0}".format(locale_timezone['result']['value']))
        else:
            log_debug("No timezone value found in Kodi settings")
    except Exception as e:
        log_warning("Error fetching timezone from Kodi settings: {0}".format(e))

    return gettz()


def convDateUtil(timestring, newfrmt='default', in_zone='UTC'):
    if newfrmt == 'default':
        newfrmt = xbmc.getRegion('time').replace(':%S', '')
    try:
        local_tzinfo = fetch_user_timezone()
        in_time = parse(timestring)
        in_time_with_timezone = in_time.replace(tzinfo=gettz(in_zone))
        local_time = in_time_with_timezone.astimezone(local_tzinfo)
        return local_time.strftime(newfrmt)
    except Exception:
        return timestring


def time_convert(timestamp):
    timestamp = int(str(timestamp)[:10])
    dt_object = datetime.fromtimestamp(timestamp)
    time_ = dt_object.strftime("%d-%b, %H:%M")
    # strftime's %b is the locale month abbrev -> a byte str with non-ascii bytes
    # on non-english locales; return unicode so it can't poison label .format().
    return six.ensure_text(time_, encoding='utf-8', errors='replace')


def time_to_update(hours=6):
    try:
        f = control.openFile(LAST_UPDATE_FILE, 'r')
        content = f.read()
        last_update_timestamp = float(content.strip())
        f.close()
    except Exception:
        return True

    hours_in_seconds = int(hours) * 60 * 60
    current_time = time.time()

    return (current_time - last_update_timestamp) >= hours_in_seconds


def update_last_update_time():
    try:
        f = control.openFile(LAST_UPDATE_FILE, 'w')
        f.write(str(time.time()))
        f.close()
    except Exception:
        print("Error updating last update time")


def adjust_date_and_convert_to_timestamp_ms(matchDate, livetvtimestr):
    date_obj = parser.parse(matchDate[2:])
    hours, minutes = map(int, livetvtimestr.split(":"))
    date_obj = date_obj.replace(hour=hours, minute=minutes)

    date_obj += timedelta(days=1)
    user_timezone = fetch_user_timezone()
    date_obj = date_obj.astimezone(user_timezone)

    if six.PY2:
        timestamp_ms = int((time.mktime(date_obj.timetuple()) + date_obj.microsecond / 1e6) * 1000)
    else:
        timestamp_ms = int(date_obj.timestamp() * 1000)
    return timestamp_ms


##########################################################################################
##########################################################################################


def Open_settings():
    control.openSettings()


def addDir(name, url, mode, iconimage, description, isFolder=True, infoLabels=None, context=None):
    if isinstance(name, str):
        name = six.ensure_text(name)
    if isinstance(url, str):
        url = six.ensure_text(url)
    if isinstance(description, str):
        description = six.ensure_text(description)
    if isinstance(iconimage, str):
        iconimage = six.ensure_text(iconimage)

    url_encoded = quote_plus(url.encode('utf-8') if isinstance(url, six.text_type) else url)
    name_encoded = quote_plus(name.encode('utf-8') if isinstance(name, six.text_type) else name)
    iconimage_encoded = quote_plus(iconimage.encode('utf-8') if isinstance(iconimage, six.text_type) else iconimage)
    description_encoded = quote_plus(
        description.encode('utf-8') if isinstance(description, six.text_type) else description)

    u = sys.argv[0] + "?url=" + url_encoded + "&mode=" + str(
        mode) + "&name=" + name_encoded + "&iconimage=" + iconimage_encoded + "&description=" + description_encoded

    liz = xbmcgui.ListItem(six.ensure_str(name, encoding='utf-8', errors='replace'))
    liz.setArt({'icon': iconimage, 'thumb': iconimage, 'poster': iconimage, 'fanart': FANART})
    if infoLabels:
        liz.setInfo(type="Video", infoLabels=infoLabels)
    if context:
        # v3.7.8 — menu contextuel (appui long / touche C) sur l'élément
        try:
            liz.addContextMenuItems(context)
        except Exception:
            pass
    if not isFolder:
        if mode == 'settings' or mode == 'version' or mode == 'clear' or mode == 'play_stream' or mode == 'site_play' or mode == 'play_mypl':
            isFolder = False
        else:
            liz.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(_handle, url=u, listitem=liz, isFolder=isFolder)


def site_events_menu(key):
    site = SITES[key]
    try:
        events = site.list_events()
    except Exception as e:
        log_info('site {0} failed: {1!r}'.format(key, e))
        events = []
    if not events and key == site_embedlivesports.KEY:
        # streamx-hd down / empty agenda: futbollibrefullhd.org agenda instead,
        # resolved by the Futbol Libre module (same embed family)
        try:
            events = site_futbollibre.list_diaries()
        except Exception as e:
            log_info('emls fallback failed: {0!r}'.format(e))
        key = site_futbollibre.KEY
    if not events:
        # v3.7.6 — message clair au lieu d'une page vide quand le site ne
        # répond pas ou n'a aucun événement.
        control.infoDialog(u"[COLOR red]Aucun événement disponible pour le moment.[/COLOR]",
                           NAME, ICON, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return
    fr_only = _french_only()
    shown = 0
    for e in events:
        if fr_only and e.get('servers') and not _servers_have_french(e.get('servers')):
            continue
        _add_site_event(key, site, e)
        shown += 1
    if events and not shown:
        control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs de ce site.[/COLOR]", NAME, ICON, 6000)
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _add_site_event(key, site, e):
    # title colour signals state: cyan = live, gold = upcoming, grey = finished.
    colors = {'live': 'cyan', 'soon': 'gold', 'done': 'grey'}
    t = time_convert(e['start_ms']) if e['start_ms'] else u'-'
    # event titles carry accents/ñ (España, Brasil...) -> keep every piece
    # unicode so the byte-str .format() can't fall back to the ascii codec.
    tag = six.ensure_text(e.get('code') or e.get('league') or '',
                          encoding='utf-8', errors='replace')  # country code, else league
    title = six.ensure_text(e['title'], encoding='utf-8', errors='replace')
    cc = u'[COLOR orange][{0}][/COLOR] '.format(tag) if tag else u''
    if getattr(site, 'TAG_LAST', False):  # time + title first, league/sport tag at the end
        label = u'[COLOR cyan]{0}[/COLOR] [COLOR {1}][B]{2}[/B][/COLOR]{3}'.format(
            t, colors.get(e['status'], 'gold'), title, u' ' + cc.rstrip() if cc else u'')
    else:
        label = u'{0}[COLOR cyan]{1}[/COLOR] [COLOR {2}][B]{3}[/B][/COLOR]'.format(
            cc, t, colors.get(e['status'], 'gold'), title)
    # carry the event's title + poster down the chain so the servers menu
    # and the player show the match, not the generic SportHD art.
    payload = json.dumps({'k': key, 's': e['servers'],
                          't': e['title'], 'p': e['poster']})
    addDir(label, payload, 'site_streams', e['poster'] or ICON, e['title'], True)


def fmov_menu():
    for s in site_futbolmov.SUBSITES:
        addDir(u'[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(s.NAME), s.KEY,
               'site_events', ICON, FANART, True,
               infoLabels={'title': s.NAME, 'plot': s.DESC})
    addDir(u'[B][COLOR gold]Rechercher[/COLOR][/B]', 'fmov', 'fmov_search', ICON, FANART, True,
           infoLabels={'title': u'Rechercher', 'plot': u'Rechercher dans les trois listes (équipe, championnat…).'})
    xbmcplugin.endOfDirectory(_handle)


def fmov_search():
    kb = xbmc.Keyboard('', u'Rechercher')
    kb.doModal()
    query = six.ensure_text(kb.getText(), encoding='utf-8', errors='replace').strip().lower() \
        if kb.isConfirmed() else u''
    if not query:
        xbmcplugin.endOfDirectory(_handle, succeeded=False)
        return
    found = 0
    for site in site_futbolmov.SUBSITES:
        try:
            events = site.list_events()
        except Exception as e:
            log_info('fmov search {0} failed: {1!r}'.format(site.KEY, e))
            continue
        for e in events:
            hay = six.ensure_text(u'{0} {1}'.format(e['title'], e.get('league') or ''),
                                  encoding='utf-8', errors='replace').lower()
            if query in hay:
                _add_site_event(site.KEY, site, e)
                found += 1
    if not found:
        control.infoDialog(u'[COLOR red]Aucun résultat[/COLOR]', NAME, ICON, 4000)
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def site_streams_menu(payload):
    d = json.loads(payload)
    poster = d.get('p') or ICON
    title = six.ensure_text(d.get('t', ''), encoding='utf-8', errors='replace')
    fr_only = _french_only()
    shown = 0
    for sname, surl in d['s']:
        # server names carry accents too (e.g. "Señal 1") -- byte-str .format()
        # on a unicode arg raises UnicodeEncodeError in py2, so keep it unicode.
        sname = six.ensure_text(sname, encoding='utf-8', errors='replace')
        if fr_only and not _is_french_source(sname, ''):
            continue
        info = {'title': u'{0} | {1}'.format(title, sname), 'plot': title}
        addDir(u'[B]{0}[/B]'.format(sname),
               json.dumps({'k': d['k'], 'u': surl, 't': title, 'p': d.get('p', '')}),
               'site_play', poster, title, isFolder=False, infoLabels=info)
        shown += 1
    if not shown:
        control.infoDialog(u"[COLOR gold]Aucune source française pour ce match.[/COLOR]", NAME, ICON, 6000)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def site_play(payload):
    d = json.loads(payload)
    site = SITES[d['k']]
    title = d.get('t') or NAME
    poster = d.get('p') or ICON
    Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
    # v3.7.8 — roue d'attente pendant la résolution (sites annexes)
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        result = site.resolve(d['u'])
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not result:
        raise Exception('could not resolve stream for ' + d['u'])
    # resolve() normally returns just the m3u8 string; sites that chain
    # through intermediate hosts (e.g. futbollibre's tarjetarojita.xyz
    # wrapper) return (m3u8, final_page_url) since the CDN token's own
    # host, not the originally-clicked link, is what the player needs
    # for Referer/Origin.
    flink, referer_url = result if isinstance(result, tuple) else (result, d['u'])
    origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(referer_url))
    stream_headers = {'Referer': origin + '/', 'Origin': origin, 'User-Agent': site.UA}
    # installed inputstream.ffmpegdirect (21.3.8) does not whitelist a
    # stream_headers listitem property -- its addon.xml listitemprops=
    # "...default_url|is_realtime_stream|..." has no headers entry, so
    # Kodi silently drops that property before the addon ever sees it
    # and FFmpeg opens the URL with no User-Agent, which this CDN 403s.
    # Embed the headers in the URL itself via Kodi's "url|key=val&..."
    # pipe convention instead -- recognized by Kodi's ffmpeg build
    # regardless of which inputstream addon is driving it.
    stream_url = xbmc_curl_encode(flink, stream_headers)
    liz = xbmcgui.ListItem(six.ensure_str(title, encoding='utf-8', errors='replace'))
    liz.setArt({'icon': poster, 'thumb': poster, 'poster': poster, 'fanart': FANART})
    liz.setInfo('video', {'title': title, 'plot': title})
    liz.setProperty("IsPlayable", "true")
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    # live m3u8 via inputstream.ffmpegdirect: the plain FFmpeg HLS demuxer
    # leaves a read-ahead thread that isn't cancelled on CloseFile() for
    # live (non-EOF) streams, so Kodi blocks ~28s per pending segment
    # request before it can close/switch. ffmpegdirect tears the stream
    # down immediately instead. It doesn't exist for Kodi 18/Leia at all
    # (dropped from addon.xml <requires> for that reason -- a hard
    # dependency there would make the *whole* addon uninstallable on
    # Leia), so check for it at runtime and fall back to the plain
    # player -- with the old close-hang -- when it's missing.
    if xbmc.getCondVisibility('System.HasAddon(inputstream.ffmpegdirect)'):
        liz.setProperty('inputstream', 'inputstream.ffmpegdirect')
        liz.setProperty('inputstream.ffmpegdirect.is_realtime_stream', 'true')
        liz.setProperty('inputstream.ffmpegdirect.stream_mode', 'live')
        liz.setProperty('inputstream.ffmpegdirect.manifest_type', 'hls')
        # open_mode=curl routes I/O through Kodi's own CCurlFile instead
        # of raw FFmpeg AVFormat. Some CDNs (seen on a futbollibre
        # alt-server chained through tarjetarojita.xyz) silently
        # blackhole FFmpeg's bundled TLS client for ~30s while an
        # ordinary curl/requests call against the exact same URL and
        # headers succeeds in well under a second -- a TLS/client-
        # fingerprint block aimed at ffmpeg, not the headers.
        # CCurlFile's TLS stack isn't flagged the same way.
        # ...but curl mode only sends our headers with the playlist request:
        # FFmpeg's HLS demuxer then fetches the segments itself without
        # Referer/Origin, so CDNs that check them on every segment (the
        # quotarevival.net player behind tarjetarojita: 403) never start.
        # A resolver that returns its player page means "this CDN wants
        # these headers" -> let FFmpeg open it, it passes them to segments.
        liz.setProperty('inputstream.ffmpegdirect.open_mode',
                        'ffmpeg' if isinstance(result, tuple) else 'curl')
        liz.setProperty('inputstream.ffmpegdirect.default_url', stream_url)
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


# v3.7.29 — TAMPHON VIDÉO ANTI-SACCADÉS (option « Réglages »).
# Les soirs de gros matchs, le CDN d'une source peut ralentir : la vidéo se
# met à tourner au ralenti (« ça ram ») puis la lecture échoue (« Échec de
# lecture »). Kodi sait absorber ces à-coups en gardant D'AVANCE plus de
# vidéo en mémoire vive : c'est le rôle du fichier advancedsettings.xml de
# son profil (buffermode 1 = tous les types de flux, memorysize 100 Mo,
# readfactor 3 = lire sur le réseau plus vite que la lecture). L'option
# ÉCRIT ce fichier uniquement s'il n'existe pas encore — on n'écrase JAMAIS
# un advancedsettings.xml existant (fichier de l'utilisateur) ; à la
# désactivation, seul le fichier portant NOTRE marqueur est retiré. Kodi ne
# lit ce fichier qu'au démarrage : une notification rappelle de redémarrer.
_ADV_MARKER = '<!-- sporthd buffer_boost v1 -->'
_ADV_FILE = 'special://masterprofile/advancedsettings.xml'
_ADV_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    + _ADV_MARKER + '\n'
    '<advancedsettings>\n'
    '  <cache>\n'
    '    <buffermode>1</buffermode>\n'
    '    <memorysize>104857600</memorysize>\n'
    '    <readfactor>3.0</readfactor>\n'
    '  </cache>\n'
    '</advancedsettings>\n')


def _buffer_boost_on():
    return ADDON.getSetting('buffer_boost') == 'true'


def _buffer_boost_sync():
    """v3.7.29 — applique/retire le tampon vidéo selon le réglage, à chaque
    ouverture de l'add-on. Silencieux quand l'état est déjà conforme ; ne
    touche jamais à un advancedsettings.xml qui n'est pas de nous."""
    try:
        ours = False
        if xbmcvfs.exists(_ADV_FILE):
            f = xbmcvfs.File(_ADV_FILE)
            try:
                head = f.read(220)
            finally:
                f.close()
            if isinstance(head, bytes):
                head = head.decode('utf-8', 'replace')
            ours = _ADV_MARKER in (head or '')
        if _buffer_boost_on():
            if xbmcvfs.exists(_ADV_FILE):
                # présent : soit le nôtre (déjà fait), soit celui de
                # l'utilisateur (on ne touche pas) — silence dans les deux cas
                return
            f = xbmcvfs.File(_ADV_FILE, 'w')
            f.write(six.ensure_binary(_ADV_XML, encoding='utf-8'))
            f.close()
            control.infoDialog(u"[COLOR skyblue]Tampon vidéo activé — redémarre Kodi pour l'appliquer[/COLOR]",
                               NAME, ICON, 6000)
        elif ours:
            xbmcvfs.deleteFile(_ADV_FILE)
            control.infoDialog(u'[COLOR grey]Tampon vidéo désactivé — redémarre Kodi[/COLOR]',
                               NAME, ICON, 4000)
    except Exception as e:
        log_error('buffer_boost: {0}'.format(e))


def router(paramstring):
    params = dict(parse_qsl(paramstring))
    if params:
        if params['mode'] == 'events':
            # if time_to_update():
            #     fetch_and_store_channel_data()
            #     update_last_update_time()
            # else:
            #     print("Not yet time to check for updates.")
            get_events(params['url'])
        elif params['mode'] == 'get_streams':
            get_stream(params['name'], params['url'])
        elif params['mode'] == 'play_stream':
            resolve2(params['description'], params['url'])
        elif params['mode'] == 'play_mypl':
            # v3.7.28 (bêta) — lecture directe d'un flux de « Ma playlist »
            play_mypl(params['description'], params['url'])
        elif params['mode'] == 'settings':
            Open_settings()
        elif params['mode'] == 'refresh_events':
            # v3.7.8 — menu contextuel « Actualiser la grille »
            refresh_events(params.get('url') or Live_url)
        elif params['mode'] == 'clear':
            control.deleteFile(LAST_UPDATE_FILE)
            # v3.7.6 — le cache disque fastcache fait partie des données :
            # « Vider les données de l'add-on » le supprime aussi.
            try:
                import os as _os
                _fc = _os.path.join(ADDON_DATA, 'fastcache.json')
                if _os.path.exists(_fc):
                    _os.remove(_fc)
                if fastcache is not None:
                    fastcache._items = None   # sera rechargé au prochain accès
            except Exception:
                pass
            # v3.7.28 — cache « Ma playlist » (chaînes de la playlist de
            # l'utilisateur ; le réglage URL lui-même reste dans Kodi)
            try:
                import os as _os
                _mp = _os.path.join(ADDON_DATA, 'mypl.json')
                if _os.path.exists(_mp):
                    _os.remove(_mp)
            except Exception:
                pass
            # v3.7.26 — dossier logos/ des anciennes versions (≤ 3.7.25)
            try:
                import os as _os
                _lg = _os.path.join(ADDON_DATA, 'logos')
                _lfiles, _ldirs = xbmcvfs.listdir(_lg)
                for _f in (_lfiles or []):
                    xbmcvfs.deleteFile(_os.path.join(_lg, _f))
            except Exception:
                pass
            # v3.7.8 — la base de chaînes et son index sont aussi des données
            _DB_CACHE['data'] = None
            _DB_CACHE['mtime'] = None
            _DB_INDEX['built'] = False
            control.infoDialog(u"[COLOR gold]Fichiers effacés[/COLOR]", NAME,
                               ICON, 5000)
        elif params['mode'] == 'version':
            xbmc.executebuiltin('UpdateAddonRepos')
        elif params['mode'] == 'fmov_menu':
            fmov_menu()
        elif params['mode'] == 'fmov_search':
            fmov_search()
        elif params['mode'] == 'site_events':
            site_events_menu(params['url'])
        elif params['mode'] == 'site_streams':
            site_streams_menu(params['url'])
        elif params['mode'] == 'site_play':
            site_play(params['url'])
    else:
        # v3.7.29 — synchronise le tampon vidéo (advancedsettings.xml) à
        # chaque ouverture de l'add-on, puis affiche le menu
        _buffer_boost_sync()
        Main_menu()


if __name__ == '__main__':
    try:
        router(sys.argv[2][1:])
    except Exception as e:
        log_error('router: {0}'.format(e))
        try:
            snippet = six.ensure_text(str(e), encoding='utf-8', errors='replace')
            if len(snippet) > 180:
                snippet = snippet[:180] + u'...'
            # v3.7.8 — message d'erreur plus parlant qu'un simple dump technique
            control.infoDialog(
                u'[COLOR red]Impossible de lire ce flux.[/COLOR] '
                u'[COLOR dimgray]Réessayez ou choisissez un autre lien ({0})[/COLOR]'.format(snippet),
                NAME, ICON, 6000)
        except Exception:
            pass