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
"""

import base64
import json
import os
import re
import time
import uuid

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
            {'accept': '*/*', 'user-agent': UA_BROWSER}, timeout=15)
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


def _cached_channels(group):
    """Channels of one group, cached VAVOO_CACHE_H hours (offline fallback).

    Anonymous catalogue request first (vavoo.to answers without the
    mediahubmx-signature); the signed request is only tried when the
    anonymous one failed AND a signature is obtainable, so an
    unreachable vypn.net can no longer empty the lists.
    """
    cache = _cache_load()
    key = 'channels_' + group
    entry = cache.get(key) or {}
    if time.time() - entry.get('t', 0) < VAVOO_CACHE_H * 3600 \
            and entry.get('items'):
        return entry['items']
    items = []
    for attempt in range(2):
        sig = '' if attempt == 0 else _get_signature()
        try:
            items = _catalog(sig, group)
        except Exception:
            items = []
        if items:
            break
    if not items:
        return entry.get('items') or []
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
        except Exception:
            continue
    return None


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
    print('ok')
