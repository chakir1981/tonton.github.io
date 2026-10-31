# -*- coding: utf-8 -*-
"""Chaînes Vavoo France for plugin.video.sporthdme.

Same NAME/KEY/DESC/list_events()/resolve() contract as the other extra
sites (site_iptv, site_futbolmov). Each "event" IS a live channel:
start_ms=0 (default.py hides the time slot), status 'live' (cyan),
TAG_LAST (label "Titre [Vavoo]"), NO_FR_FILTER (the server names are
generic "Serveur N" and every channel is French by definition -- the
"Uniquement les sources françaises" setting must not hide them).

How it works (reverse-engineered from the current plugin.video.vavooto
2026.10.29.1, michaz1988's repo, and verified live):

  1. catalog        : POST https://vavoo.to/mediahubmx-catalog.json
     with filter {"group": <group>} and cursor pagination -> items
     {name, url ("https://vavoo.to/vavoo-iptv/play/<id>"), group, logo}.
     Asked WITHOUT a signature first: vavoo.to currently answers
     anonymously, and requiring the vypn.net ping for listing made the
     two lists empty on boxes where that host is unreachable (ISP/DNS
     blocks). A signed request is kept as a fallback in case the
     anonymous access gets re-restricted.
  2. auth signature : POST https://www.vypn.net/api/app/ping with an
     "app-focus" device payload -> JSON {"sig": "<base64 envelope>"}
     whose decoded payload carries a validUntil (ms). The signature is
     passed to Vavoo in the "mediahubmx-signature" header. OPTIONAL:
     only tried when the anonymous catalogue/resolve failed.
  3. playback       : POST https://vavoo.to/mediahubmx-resolve.json
     with {"url": <play link>} -> [{"url": "<session m3u8>"}] played
     with User-Agent "MediaHubMX/2". Anonymous first, signed retry.

The resolved m3u8 is a short-lived session URL, so resolve() is only
called at play time (never at listing time). The signature and the
channel catalogs are cached in the add-on profile (vavoo_cache.json):
the signature until ~5 min before its own validUntil, catalogs for
VAVOO_CACHE_H hours; the last good copy is reused when offline.

Some channel names in the catalog carry a source suffix ("BEIN SPORTS 1
.b", ".c", ".s") or "(BACKUP)". Suffixes are stripped and variants are
grouped as several servers of the same channel, so if one source fails
the user just picks the next one in the servers menu.

Match fallback: fallback_channels(match_title) picks the French channels
most likely broadcasting the match (sport/competition keyword rules) so
default.py can offer them as "dépannage" sources for matches that have
no French channel of their own.
"""

import base64
import json
import os
import re
import time
import uuid

import six
from six.moves.urllib.parse import quote_plus

NAME = u'Chaînes Vavoo (France)'
DESC = (u'[B]Chaînes françaises de la plateforme Vavoo[/B]\n\n'
        u'Les bouquets français complets : beIN Sports, RMC Sport, '
        u'Canal+, L\'Équipe, DAZN, Ligue 1, TF1, M6, ARTE… Plus de '
        u'900 chaînes en français, dont une sélection « Sport » regroupant '
        u'les chaînes de matchs. Les variantes d\'une même chaîne sont '
        u'proposées comme plusieurs serveurs. Flux issus de l\'API de '
        u'l\'application Vavoo ; certaines chaînes peuvent être '
        u'momentanément indisponibles ou instables.')
UA = 'MediaHubMX/2'  # required by catalog, resolve AND playback

URL_PING = 'https://www.vypn.net/api/app/ping'
URL_CATALOG = 'https://vavoo.to/mediahubmx-catalog.json'
URL_RESOLVE = 'https://vavoo.to/mediahubmx-resolve.json'

LAST_ERROR = u''   # dernière cause d'échec (listes/lecture), pour le diagnostic
UA_BROWSER = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0.0.0 Safari/537.36')
VAVOO_CACHE_H = 4
SIG_SAFETY_S = 300          # refresh the signature 5 min before expiry
SIG_FALLBACK_S = 6 * 3600   # if its validUntil can't be decoded

# Catalog groups holding French channels.
GROUP_SPORT = 'France Sport'
GROUP_ALL = 'France'

# Substrings (upper-cased) picking the sport channels out of the big
# "France" group for the "Sport France" subsite.
_SPORT_WORDS = ('BEIN', 'RMC', 'CANAL+ SPORT', 'CANAL SPORT', 'CANAL+ FOOT',
                'FOOT+', 'FOOT 24', "L'EQUIPE", 'LEQUIPE', 'EQUIPE 21',
                'EQUIDIA', 'DAZN', 'PRIME SPORT', 'PRIME VIDEO',
                'INFO SPORT', 'INFOSPORT', 'EUROSPORT', 'GOLF+', 'GOLF PLUS',
                'MOTORS', 'MOTORVISION', 'AUTOMOTO', 'MOTO ', 'MOTOR ',
                'LIGUE 1', 'LIGUE 2', 'TIPIK', 'SPORT', 'XTRA', 'MULTI')


# ---- name helpers (pure, testable) ----------------------------------------

_SRC_RE = re.compile(r'\s+\.[a-z]\s*$', re.I)
_BACKUP_RE = re.compile(r'\s+\(BACKUP\)\s*$', re.I)


def clean_name(name):
    """'BEIN SPORTS 1 .b' -> 'BEIN SPORTS 1' ; 'X (BACKUP) .s' -> 'X'."""
    name = (name or '').strip()
    name = _SRC_RE.sub('', name)
    name = _BACKUP_RE.sub('', name)
    return name.strip() or u'Chaîne Vavoo'


def _sort_key(item):
    return clean_name(item['name']).lower()


def group_channels(items):
    """Catalog items -> channel dicts grouped by display name.

    Returns a sorted list of {'title': str, 'logo': str, 'urls': [str]}.
    Variants (.b/.c/.s/(BACKUP)) become extra servers of one channel.
    """
    order = []
    by_name = {}
    for it in items:
        title = clean_name(it.get('name'))
        url = it.get('url') or ''
        if not url:
            continue
        if title not in by_name:
            by_name[title] = {'title': title, 'logo': it.get('logo') or '',
                              'urls': []}
            order.append(title)
        ch = by_name[title]
        if url not in ch['urls']:
            ch['urls'].append(url)
        if not ch['logo'] and it.get('logo'):
            ch['logo'] = it['logo']
    return sorted(by_name.values(), key=lambda c: c['title'].lower())


def is_sport(name):
    up = ' ' + clean_name(name).upper() + ' '
    return any(w in up for w in _SPORT_WORDS)


# ---- match -> probable French channels (fallback feature) -------------------
#
# Les matchs sans aucune chaîne française reçoivent des chaînes Vavoo en
# dépannage : on choisit les bouquets français les plus probables pour le
# sport / la compétition du match. C'est une aide au zapping, pas une
# grille TV : le match n'est pas garanti sur la chaîne proposée.

def _norm(s):
    """Minuscules/accents/ponctuation retirés : 'CANAL+ FOOT' -> 'CANAL FOOT'."""
    try:
        import unicodedata
        s = s if isinstance(s, six.text_type) else six.ensure_text(
            s or u'', encoding='utf-8', errors='replace')
        s = s.upper()
        s = u''.join(c for c in unicodedata.normalize('NFD', s)
                     if unicodedata.category(c) != 'Mn')
        s = re.sub(r'[^\w\s]', ' ', s, flags=re.UNICODE)
        return u' '.join(s.split())
    except Exception:
        return u''


# (regex sur le titre normalisé du match -> chaînes probables, par ordre de
# préférence). Les noms sont des préfixes de noms de chaînes normalisés.
_FALLBACK_RULES = (
    # compétitions françaises / européennes reprises par Canal+/DAZN/beIN
    (u'LIGUE 1|LIGUE 2|DOM TOM', (u'CANAL LIGUE 1', u'DAZN LIGUE 1',
                                  u'LIGUE 1', u'BEIN SPORTS 1')),
    (u'CHAMPIONS|EUROPA|CONFERENCE|UCL|UEL',
     (u'BEIN SPORTS 1', u'BEIN SPORTS 2', u'CANAL FOOT', u'CANAL SPORT',
      u'RMC SPORT 1')),
    (u'COUPE DE FRANCE|COUPE DE LA LIGUE|TROPH',
     (u'CANAL FOOT', u'BEIN SPORTS 1', u'FRANCE 3')),
    # championnats étrangers
    (u'PREMIER LEAGUE|CHAMPIONSHIP|ANGLI|ENGLAND',
     (u'RMC SPORT 1', u'RMC SPORT 2')),
    (u'LALIGA|LA LIGA|ESPANI|ESPAGNE|SPAIN|CALCIO ITALIA',
     (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'SERIE A|ITALI|ITALY', (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'BUNDESLIGA|ALLEMAGNE|GERMAN', (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'PRIMEIRA|PORTUG|EREDIVISIE|PAYS BAS|BRASIL|BRESIL|ARGENTIN|MEXIC',
     (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'MLS|SAUDI|QATAR|EMIRAT', (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    # sélections nationales : TF1/M6 et beIN
    (u'COUPE DU MONDE|WORLD CUP|NATIONS LEAGUE|QUALIFIC|AMICAL|FRIENDLY|'
     u'EURO 202|EURO 202[0-9]|JO |OLYMPIC|FRANCE -|- FRANCE|FRANCE VS|VS FRANCE',
     (u'TF1', u'M6', u'BEIN SPORTS 1', u'CANAL FOOT')),
    # autres sports (avant les noms de clubs : un « Paris Masters » est du
    # tennis, pas un match du PSG)
    (u'RUGBY|TOP 14|SIX NATIONS|TOURNOI|PRO D2|CHALLENGE CUP',
     (u'CANAL SPORT', u'CANAL', u'FRANCE 2', u'FRANCE 3')),
    (u'TENNIS|ATP|WTA|ROLAND|WIMBLEDON|US OPEN|DAVIS|BNP PARIBAS',
     (u'EUROSPORT 1', u'CANAL SPORT')),
    (u'CYCLISME|TOUR DE FRANCE|VUELTA|GIRO|UCI|CYCLING|DAUPHINE|CLASSIQUE',
     (u'EUROSPORT 1', u'FRANCE 2', u'FRANCE 3')),
    (u'FORMULE|FORMULA|F1 |GRAND PRIX|MOTOGP|WRC|RALLYE|MOTO GP|ELF',
     (u'CANAL SPORT', u'AUTOMOTO')),
    (u'BASKET|NBA|EUROLEAGUE|BETCLIC|WNBA',
     (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'HANDBALL|VOLLEY|JUDO|ESCRIME|NATATION|ATHLETIS|HALLE',
     (u'BEIN SPORTS 1', u'BEIN SPORTS 2')),
    (u'BOXE|MMA|UFC|COMBAT|KICK|MUAY|BELLATOR',
     (u'CANAL SPORT', u'RMC SPORT 1', u'RMC SPORT 2')),
    (u'GOLF', (u'GOLF', u'GOLF CHANNEL')),
    (u'TROT|PMU|HIPPIQUE|QUINTE|COURSE HIPPIQUE|EQUESTRE|EQUITA',
     (u'EQUIDIA',)),
    (u'SNOOKER|DARTS|FLECHETTE|BILLARD|BOWLING',
     (u'EUROSPORT 1',)),
    (u'AMERICAIN|NFL |XFL|BASEBALL|MLB |HOCKEY|NHL |GLADIATOR',
     (u'BEIN SPORTS 1', u'MULTISPORTS 1')),
    (u'ESPORT|E-SPORT|GAMING|FORTNITE|LOL |LEAGUE OF LEGEND',
     (u'BEIN SPORTS 1', u'MULTISPORTS 1')),
    # grands clubs français (en dernier : les noms de villes comme Lyon ou
    # Nice peuvent aussi désigner un lieu de compétition d'un autre sport)
    (u'\\bPSG\\b|PARIS SG|PARIS SAINT|\\bMARSEILLE\\b|\\bOM\\b|\\bOL\\b|'
     u'\\bLYON\\b|\\bMONACO\\b|\\bLENS\\b|\\bLILLE\\b|\\bNICE\\b|\\bRENNES\\b',
     (u'CANAL LIGUE 1', u'DAZN LIGUE 1', u'LIGUE 1', u'BEIN SPORTS 1')),
)
# aucun motif reconnu -> bouquets football/sport les plus larges
_FOOT_DEFAULT = (u'BEIN SPORTS 1', u'CANAL FOOT', u'CANAL SPORT',
                 u'RMC SPORT 1', u'L EQUIPE', u'MULTISPORTS 1')


def _fallback_patterns(label):
    """Motifs de chaînes probables pour un match, ordre de préférence."""
    text = _norm(label)
    pats = []
    for rx, rule_pats in _FALLBACK_RULES:
        try:
            hit = re.search(rx, text)
        except Exception:
            hit = None
        if hit:
            for p in rule_pats:
                if p not in pats:
                    pats.append(p)
    if not pats:
        pats = list(_FOOT_DEFAULT)
    return pats


def fallback_channels(label, limit=6):
    """Chaînes Vavoo françaises les plus probables pour un match.

    label : titre du match (équipe, compétition...). Retourne une liste
    (max `limit`) de dicts {'title', 'logo', 'urls'} — la même structure
    que group_channels() — ou [] si le catalogue Vavoo est inaccessible.
    Les chaînes sont choisies par préfixe de nom normalisé ( tokens),
    variante la plus courte d'abord (BEIN SPORTS 1 plutôt que ... HD).
    """
    if not label:
        return []
    pats = _fallback_patterns(label)
    items = []
    for group in (GROUP_SPORT, GROUP_ALL):
        try:
            items += _cached_channels(group)
        except Exception:
            pass
    if not items:
        return []
    by_norm = {}
    for ch in group_channels(items):
        n = _norm(ch['title'])
        if not n:
            continue
        cur = by_norm.get(n)
        if cur is None or len(ch['urls']) > len(cur['urls']):
            by_norm[n] = ch
    out, used = [], set()
    for pat in pats:
        pt = _norm(pat).split()
        if not pt:
            continue
        best = None
        for n, ch in by_norm.items():
            if n in used:
                continue
            tt = n.split()
            if len(pt) <= len(tt) and tt[:len(pt)] == pt:
                key = (len(tt), -len(ch['urls']))
                if best is None or key < best[0]:
                    best = (key, n, ch)
        if best is not None:
            used.add(best[1])
            out.append(best[2])
            if len(out) >= limit:
                break
    return out


# ---- auth signature ---------------------------------------------------------

def _build_payload(reason='app-focus'):
    ts = int(time.time() * 1000)
    return {
        'reason': reason, 'locale': 'en', 'theme': 'dark',
        'metadata': {
            'device': {'type': 'desktop', 'uniqueId': str(uuid.uuid4())},
            'os': {'name': 'win32', 'version': 'Windows 10 Pro',
                   'abis': ['x64'], 'host': 'Lenovo'},
            'app': {'platform': 'electron'},
            'version': {'package': 'net.vypn.app', 'binary': '3.1.0',
                        'js': '3.1.0'},
        },
        'appFocusTime': 0, 'playerActive': False, 'playDuration': 0,
        'devMode': False, 'hasAddon': True, 'castConnected': False,
        'package': 'net.vypn.app', 'version': '3.1.0', 'process': 'app',
        'firstAppStart': ts, 'lastAppStart': ts, 'ipLocation': None,
        'adblockEnabled': True,
        'proxy': {'supported': ['ss'], 'engine': 'Mu',
                  'enabled': False, 'autoServer': True},
        'iap': {'supported': False},
    }


def _b64pad(s):
    pad = b'=' if isinstance(s, bytes) else '='
    return s + pad * (-len(s) % 4)


def sig_valid_until(signature):
    """validUntil (ms) decoded from the base64 signature envelope, or 0."""
    try:
        if not isinstance(signature, (str, bytes)):
            return 0
        raw = signature if isinstance(signature, bytes) \
            else signature.encode('ascii')
        envelope = json.loads(
            base64.urlsafe_b64decode(_b64pad(raw)).decode('utf-8'))
        payload = envelope.get('data')
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return 0
        vu = int(payload.get('validUntil') or 0)
        if 0 < vu < 100000000000:   # seconds -> milliseconds
            vu *= 1000
        return vu
    except Exception:
        return 0


# ---- cache ------------------------------------------------------------------

def _cache_path():
    try:
        import xbmcaddon
        import xbmcvfs
        profile = xbmcaddon.Addon().getAddonInfo('profile')
        try:
            return os.path.join(xbmcvfs.translatePath(profile),
                                'vavoo_cache.json')
        except AttributeError:  # Kodi 18
            import xbmc
            return os.path.join(xbmc.translatePath(profile),
                                'vavoo_cache.json')
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), 'sporthd_vavoo_cache.json')


def _cache_load():
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(cache):
    try:
        with open(_cache_path(), 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass


# ---- network ----------------------------------------------------------------

def _post_json(url, data, headers, timeout=15, retries=1):
    import requests
    try:
        requests.packages.urllib3.disable_warnings()
    except Exception:
        pass
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=data, headers=headers,
                              timeout=timeout, verify=False)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5)
    raise last


def _get_signature(force=False):
    """MediaHubMX signature, cached until ~5 min before its validUntil."""
    cache = _cache_load()
    entry = cache.get('sig') or {}
    if not force and entry.get('sig'):
        until = sig_valid_until(entry['sig']) or \
            entry.get('t', 0) + SIG_FALLBACK_S * 1000
        if until > (time.time() + SIG_SAFETY_S) * 1000:
            return entry['sig']
    try:
        data = _post_json(
            URL_PING, _build_payload(),
            {'accept': '*/*', 'user-agent': UA_BROWSER}, timeout=15, retries=0)
        sig = (data.get('sig') or data.get('addonSig')
               or data.get('signature') or '')
        if sig:
            cache['sig'] = {'t': int(time.time()), 'sig': sig}
            _cache_save(cache)
            return sig
    except Exception:
        pass
    return entry.get('sig') or ''  # last good copy when offline


def _catalog(sig, group):
    """All catalog items of one group (cursor pagination).

    sig may be '': the header is only sent when a signature is actually
    available, so listing never depends on the vypn.net ping.
    """
    headers = {'user-agent': UA, 'accept': 'application/json',
               'content-type': 'application/json; charset=utf-8',
               'accept-encoding': 'gzip'}
    if sig:
        headers['mediahubmx-signature'] = sig
    data = {'language': 'de', 'region': 'AT', 'catalogId': 'iptv',
            'id': 'iptv', 'adult': False, 'search': '', 'sort': 'name',
            'filter': {'group': group}, 'cursor': 0,
            'clientVersion': '3.1.0'}
    items, cursor = [], 0
    while cursor is not None:
        data['cursor'] = cursor
        j = _post_json(URL_CATALOG, data, headers, timeout=20)
        items += j.get('items', [])
        cursor = j.get('nextCursor')
    return items


def _errstr(exc):
    """Compact one-line description of an exception (ASCII-safe)."""
    try:
        s = '{0}'.format(exc)
    except Exception:
        s = ''
    s = (s or exc.__class__.__name__).strip()
    s = s.splitlines()[0] if s else exc.__class__.__name__
    return s[:140]


def _cached_channels(group):
    """Channels of one group, cached VAVOO_CACHE_H hours (offline fallback).

    Anonymous catalogue request first (vavoo.to answers without the
    mediahubmx-signature); the signed request is only tried when the
    anonymous one failed AND a signature is obtainable, so an
    unreachable vypn.net can no longer empty the lists. The reason of a
    total failure is kept in LAST_ERROR for the on-screen diagnostic.
    """
    global LAST_ERROR
    cache = _cache_load()
    key = 'channels_' + group
    entry = cache.get(key) or {}
    if time.time() - entry.get('t', 0) < VAVOO_CACHE_H * 3600 \
            and entry.get('items'):
        LAST_ERROR = u''
        return entry['items']
    items, errs = [], []
    for attempt in range(2):
        sig = '' if attempt == 0 else _get_signature()
        try:
            items = _catalog(sig, group)
        except Exception as exc:
            items = []
            errs.append(u'catalogue {0} : {1}'.format(
                u'anonyme' if attempt == 0 else u'signé', _errstr(exc)))
        if items:
            break
    if not items:
        if entry.get('items'):
            LAST_ERROR = u''
            return entry['items']   # cache périmé vaut mieux que liste vide
        LAST_ERROR = u' ; '.join(errs) or \
            u'catalogue vide (vavoo.to ne renvoie aucune chaîne)'
        return []
    LAST_ERROR = u''
    cache[key] = {'t': int(time.time()), 'items': items}
    _cache_save(cache)
    return items


# ---- module contract --------------------------------------------------------

def _event(ch):
    return {
        'title': ch['title'],
        'code': u'Vavoo',     # orange [Vavoo] tag, shown at the end (TAG_LAST)
        'league': '',
        'start_ms': 0,        # live 24/7
        'poster': ch.get('logo') or '',
        'status': 'live',
        'servers': [[u'{0} {1}'.format(u'Serveur', i + 1), url]
                    for i, url in enumerate(ch['urls'])],
    }


def _resolve(server_url):
    """site_play contract: resolve the play link to a fresh session m3u8.

    The m3u8 is short-lived, so this runs at play time only. Returns the
    m3u8 URL (site.UA = MediaHubMX/2 is sent by the player) or None.
    Anonymous resolve first; if it fails, one more attempt with a
    freshly refreshed signature (if the vypn.net ping answers).
    """
    data = {'language': 'de', 'region': 'AT', 'url': server_url,
            'clientVersion': '3.1.0'}
    global LAST_ERROR
    errs = []
    for attempt in range(2):
        sig = '' if attempt == 0 else _get_signature(force=True)
        headers = {'user-agent': UA,
                   'content-type': 'application/json; charset=utf-8',
                   'accept-encoding': 'gzip'}
        if sig:
            headers['mediahubmx-signature'] = sig
        try:
            j = _post_json(URL_RESOLVE, data, headers, timeout=15, retries=1)
            if isinstance(j, list) and j and j[0].get('url'):
                return j[0]['url']
        except Exception as exc:
            errs.append(_errstr(exc))
            continue
    LAST_ERROR = (u'lecture : ' + u' ; '.join(errs)
                  if errs else u'lecture : aucun flux renvoyé')
    return None


def clear_cache():
    """Delete the Vavoo cache file (diagnostic action)."""
    global LAST_ERROR
    LAST_ERROR = u''
    try:
        os.remove(_cache_path())
        return True
    except Exception:
        return False


def diagnostic():
    """[(label, ok, detail)] for the on-screen Vavoo troubleshooter."""
    out = []
    # 1) catalogue anonyme
    try:
        items = _catalog('', GROUP_SPORT)
        out.append((u'Catalogue Vavoo (sans signature)', bool(items),
                    u'{0} chaînes « {1} »'.format(len(items), GROUP_SPORT)
                    if items else u'vavoo.to répond mais aucune chaîne'))
    except Exception as exc:
        out.append((u'Catalogue Vavoo (sans signature)', False,
                    _errstr(exc)))
    # 2) signature vypn.net (secours uniquement depuis la 2.0.8)
    try:
        sig = _get_signature(force=True)
        out.append((u'Signature vypn.net (secours)', bool(sig),
                    u'reçue ({0} caractères)'.format(len(sig)) if sig
                    else u'injoignable — sans incidence depuis la 2.0.8'))
    except Exception as exc:
        out.append((u'Signature vypn.net (secours)', False, _errstr(exc)))
    # 3) cache local
    try:
        cache = _cache_load()
        chans = [(k, v) for k, v in cache.items()
                 if k.startswith('channels_') and v.get('items')]
        if chans:
            age_min = min(int(time.time() - v.get('t', 0)) // 60
                          for _, v in chans)
            total = sum(len(v['items']) for _, v in chans)
            out.append((u'Cache local', True,
                        u'{0} groupe(s), {1} chaînes, plus récent : {2} min'
                        .format(len(chans), total, age_min)))
        else:
            out.append((u'Cache local', False,
                        u'vide (normal au premier essai)'))
    except Exception as exc:
        out.append((u'Cache local', False, _errstr(exc)))
    # 4) lecture test (resolve) sur la première chaîne du catalogue
    try:
        items = _catalog('', GROUP_SPORT)
        url = items[0].get('url') if items else ''
        name = clean_name(items[0].get('name')) if items else u'?'
        m3u8 = _resolve(url) if url else None
        out.append((u'Lecture test (resolve)', bool(m3u8),
                    u'{0} : flux obtenu'.format(name) if m3u8
                    else u'aucun flux renvoyé pour {0}'.format(name)))
    except Exception as exc:
        out.append((u'Lecture test (resolve)', False, _errstr(exc)))
    return out


class _Sub(object):
    TAG_LAST = True      # label "Chaîne [Vavoo]" -- no time slot for 24/7
    NO_FR_FILTER = True  # everything here is French by definition

    def __init__(self, key, name, desc, getter):
        self.KEY, self.NAME, self.DESC, self._get = key, name, desc, getter
        self.UA = UA

    def list_events(self):
        return self._get()

    def resolve(self, server_url):
        return _resolve(server_url)


def _events_sport():
    chans, seen = [], set()
    # 1) the whole "France Sport" catalog group
    for ch in group_channels(_cached_channels(GROUP_SPORT)):
        if ch['title'].lower() not in seen:
            seen.add(ch['title'].lower())
            chans.append(ch)
    # 2) sport channels picked out of the big "France" group
    for it in _cached_channels(GROUP_ALL):
        if is_sport(it.get('name')):
            for ch in group_channels([it]):
                if ch['title'].lower() not in seen:
                    seen.add(ch['title'].lower())
                    chans.append(ch)
    return [_event(c) for c in sorted(chans,
                                      key=lambda c: c['title'].lower())]


def _events_all():
    chans, seen = [], set()
    for group in (GROUP_ALL, GROUP_SPORT):
        for ch in group_channels(_cached_channels(group)):
            if ch['title'].lower() not in seen:
                seen.add(ch['title'].lower())
                chans.append(ch)
    return [_event(c) for c in chans]


SUBSITES = [
    _Sub('vavoo_sport', u'Sport France (Vavoo)',
         u'[B]Chaînes sportives françaises Vavoo[/B]\n\n'
         u'beIN Sports, RMC Sport, Canal+ Sport, L\'Équipe, DAZN, Ligue 1, '
         u'Info Sport, chaînes événementielles… Tous les bouquets sport '
         u'français de la plateforme Vavoo. Les variantes d\'une chaîne '
         u'sont proposées comme plusieurs serveurs.',
         _events_sport),
    _Sub('vavoo_all', u'Chaînes France (Vavoo)',
         u'[B]Toutes les chaînes françaises Vavoo[/B]\n\n'
         u'Plus de 900 chaînes du bouquet français : TF1, M6, ARTE, Canal+, '
         u'Ciné, jeunesse, locales… Les matchs passent aussi sur les '
         u'chaînes généralistes. Classement alphabétique.',
         _events_all),
]

NAME_KEY = SUBSITES[0].KEY  # unused; kept for symmetry with other modules


if __name__ == '__main__':
    # ---- offline self-test ----------------------------------------------------
    assert clean_name('BEIN SPORTS 1 .b') == 'BEIN SPORTS 1'
    assert clean_name('X (BACKUP) .s') == 'X'
    assert clean_name('LEQUIPE 21 HD .s') == 'LEQUIPE 21 HD'
    assert clean_name('  ARTE .c  ') == 'ARTE'
    assert clean_name('') == u'Chaîne Vavoo'
    assert is_sport('BEIN SPORTS 1 .b')
    assert is_sport('RMC SPORT 1 HD .s')
    assert is_sport("L'EQUIPE 21 HD .s")
    assert not is_sport('TF1 .b')
    assert not is_sport('6TER .c')
    _items = [
        {'name': 'BEIN SPORTS 1 .b', 'url': 'u1', 'logo': 'l1'},
        {'name': 'BEIN SPORTS 1 .c', 'url': 'u2', 'logo': ''},
        {'name': 'BEIN SPORTS 1 (BACKUP) .b', 'url': 'u3', 'logo': ''},
        {'name': 'TF1 .s', 'url': 'u4', 'logo': ''},
        {'name': 'TF1 .b', 'url': 'u4', 'logo': ''},   # same url -> no dup
        {'name': 'NO URL', 'url': '', 'logo': 'l9'},
    ]
    _chans = group_channels(_items)
    assert [c['title'] for c in _chans] == ['BEIN SPORTS 1', 'TF1'], _chans
    assert _chans[0]['urls'] == ['u1', 'u2', 'u3']
    assert _chans[0]['logo'] == 'l1'
    _ev = _event(_chans[0])
    assert _ev['title'] == 'BEIN SPORTS 1' and _ev['status'] == 'live'
    assert _ev['start_ms'] == 0 and _ev['code'] == u'Vavoo'
    assert _ev['servers'] == [[u'Serveur 1', 'u1'], [u'Serveur 2', 'u2'],
                              [u'Serveur 3', 'u3']]
    # signature validity decoding (crafted envelope, validUntil = year 2099)
    _payload = json.dumps({'validUntil': 4070899200000, 'app': {'ok': True}})
    _sig = base64.urlsafe_b64encode(
        json.dumps({'signature': 'x', 'data': _payload}).encode('utf-8'))
    assert sig_valid_until(_sig.decode('ascii')) == 4070899200000
    assert sig_valid_until('garbage!') == 0
    assert sig_valid_until(None) == 0
    # ---- fallback matching (pure parts) ------------------------------------
    assert _norm(u'CANAL+ FOOT') == u'CANAL FOOT'
    assert _norm(u'CANAL + LIGUE 1') == u'CANAL LIGUE 1'
    assert _norm(u"L'ÉQUIPE") == u'L EQUIPE'
    assert _norm(u'GOLF+') == u'GOLF'
    assert _norm('') == u''
    # Ligue 1 -> Canal + Ligue 1 en premier
    _p = _fallback_patterns(u'PSG vs Marseille - Ligue 1')
    assert _p[0] == u'CANAL LIGUE 1' and u'DAZN LIGUE 1' in _p, _p
    # Ligue des Champions -> beIN en premier
    _p = _fallback_patterns(u'Real Madrid vs Man City - Liga des Campeones UCL')
    assert _p[0] == u'BEIN SPORTS 1' and u'CANAL FOOT' in _p, _p
    # Premier League -> RMC
    _p = _fallback_patterns(u'Arsenal vs Liverpool - Premier League')
    assert _p[0] == u'RMC SPORT 1', _p
    # Rugby -> Canal+ Sport
    _p = _fallback_patterns(u'Top 14 Rugby: Toulouse vs Stade Français')
    assert _p[0] == u'CANAL SPORT', _p
    # Tennis -> Eurosport 1
    _p = _fallback_patterns(u'Tennis ATP Masters 1000')
    assert _p[0] == u'EUROSPORT 1', _p
    # « Paris » dans un titre de tennis ne doit pas déclencher la règle PSG
    _p = _fallback_patterns(u'Tennis - ATP Paris Masters')
    assert _p[0] == u'EUROSPORT 1', _p
    # sélection nationale -> TF1 d'abord
    _p = _fallback_patterns(u'France - Belgique, match amical')
    assert _p[0] == u'TF1', _p
    # rien de reconnu -> bouquets football génériques
    assert _fallback_patterns(u'Club A vs Club B')[0] == u'BEIN SPORTS 1'
    # fallback_channels sur un catalogue factice
    _fake = [
        {'name': 'BEIN SPORTS 1 .b', 'url': 'u1', 'logo': 'l'},
        {'name': 'BEIN SPORTS 1 .c', 'url': 'u2', 'logo': ''},
        {'name': 'BEIN SPORTS 1 HD', 'url': 'u3', 'logo': ''},
        {'name': 'BEIN SPORTS MAX 4', 'url': 'u4', 'logo': ''},
        {'name': 'RMC SPORT 1', 'url': 'u5', 'logo': ''},
        {'name': 'RMC SPORT 1 UHD', 'url': 'u6', 'logo': ''},
        {'name': 'CANAL+ FOOT', 'url': 'u7', 'logo': ''},
        {'name': 'CANAL SPORT', 'url': 'u8', 'logo': ''},
        {'name': 'CANAL + LIGUE 1', 'url': 'u9', 'logo': ''},
        {'name': 'DAZN LIGUE 1 LIVE 1 HD', 'url': 'u10', 'logo': ''},
        {'name': 'LIGUE 1 FHD', 'url': 'u17', 'logo': ''},
        {'name': 'L EQUIPE', 'url': 'u11', 'logo': ''},
        {'name': 'EUROSPORT 1', 'url': 'u12', 'logo': ''},
        {'name': 'TF1 FHD', 'url': 'u13', 'logo': ''},
        {'name': 'M6', 'url': 'u14', 'logo': ''},
        {'name': 'MULTISPORTS 1', 'url': 'u15', 'logo': ''},
        {'name': 'CANAL+ CINEMA', 'url': 'u16', 'logo': ''},
    ]
    _old = _cached_channels
    _cached_channels = lambda g: list(_fake)  # noqa: E731
    try:
        _fb = fallback_channels(u'PSG vs Marseille - Ligue 1')
        assert [_c['title'] for _c in _fb] == [
            u'CANAL + LIGUE 1', u'DAZN LIGUE 1 LIVE 1 HD', u'LIGUE 1 FHD',
            u'BEIN SPORTS 1'], _fb
        # la variante la plus courte gagne (pas 'BEIN SPORTS 1 HD', pas MAX)
        assert _fb[3]['urls'] == ['u1', 'u2'], _fb[3]
        _fb = fallback_channels(u'Tennis Roland Garros')
        assert _fb[0]['title'] == u'EUROSPORT 1', _fb
        _fb = fallback_channels(u'Match inconnu A vs B')
        assert _fb[0]['title'] == u'BEIN SPORTS 1' and \
            u'CANAL+ CINEMA' not in [_c['title'] for _c in _fb], _fb
        _fb = fallback_channels(u'France - Belgique, amical')
        assert _fb[0]['title'] == u'TF1 FHD', _fb
        assert len(fallback_channels(u'PSG vs OM', limit=2)) == 2
        assert fallback_channels(u'') == []
        _cached_channels = lambda g: []  # noqa: E731
        assert fallback_channels(u'PSG vs OM') == []
    finally:
        _cached_channels = _old
    print('ok')
