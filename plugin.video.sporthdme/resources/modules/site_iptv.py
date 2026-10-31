# -*- coding: utf-8 -*-
"""
Chaînes IPTV en direct (projet iptv-org) for plugin.video.sporthdme.

Same NAME/KEY/DESC/list_events()/resolve() contract as the other extra
sites. Each "event" IS a live channel: start_ms=0 (default.py shows an
empty time slot), status 'live' (cyan). resolve() returns the stream URL
as-is (it is already a direct m3u8), except that it decodes the Kodi
pipe headers that list_events() embedded in the server URL.

Playlists come from the iptv-org community project, which aggregates
publicly available streams from official sources:
    https://github.com/iptv-org/iptv
    https://iptv-org.github.io/iptv/languages/fra.m3u      (chaînes en français, tous pays)
    https://iptv-org.github.io/iptv/categories/sports.m3u  (chaînes sportives, monde entier)

The raw text is cached in the add-on profile for IPTV_CACHE_H hours so
opening the menu does not re-download ~100 KB each time (and works
offline with the last copy). Playlist directives #EXTVLCOPT
(http-referrer / http-user-agent) are carried inside the server URL with
Kodi's "url|Referer=...&User-Agent=..." convention and decoded by
resolve(): site_play then sends Referer/Origin/User-Agent matching what
the CDN expects.
"""

import json
import os
import re
import time

from six.moves.urllib.parse import urlencode, parse_qsl

NAME = u'Chaînes en direct (IPTV)'
DESC = (u'[B]Chaînes TV en direct du monde entier[/B]\n\n'
        u'Sport en français, chaînes des pays francophones (France, '
        u'Belgique, Suisse, Canada, Maghreb, Afrique…) et chaînes '
        u'sportives d\'autres pays. Listes publiques du projet '
        u'communautaire iptv-org, mises à jour en continu. Certaines '
        u'chaînes peuvent être momentanément hors service ou '
        u'géobloquées hors de leur pays.')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

URL_FRA = 'https://iptv-org.github.io/iptv/languages/fra.m3u'
URL_SPORTS = 'https://iptv-org.github.io/iptv/categories/sports.m3u'
IPTV_CACHE_H = 12

# Countries first in every list: francophone countries, then the rest.
_FR_ORDER = ['fr', 'be', 'ch', 'ca', 'ma', 'dz', 'tn', 'sn', 'ci', 'cm',
             'cd', 'cg', 'gn', 'ml', 'bf', 'ne', 'bj', 'tg', 'td', 'cf',
             'ga', 'ht', 'lu', 'mc', 'gf', 'gp', 'mq', 'pf', 'nc', 'rw',
             'bi', 'km', 'mg', 'mu', 'dj']

COUNTRY = {
    'ae': u'Émirats arabes unis', 'al': u'Albanie', 'ar': u'Argentine',
    'at': u'Autriche', 'au': u'Australie', 'az': u'Azerbaïdjan',
    'bd': u'Bangladesh', 'be': u'Belgique', 'bf': u'Burkina Faso',
    'bg': u'Bulgarie', 'bh': u'Bahreïn', 'bi': u'Burundi', 'bj': u'Bénin',
    'bo': u'Bolivie', 'br': u'Brésil', 'by': u'Bélarus',
    'ca': u'Canada', 'cd': u'RD Congo', 'cf': u'Centrafrique',
    'cg': u'Congo', 'ch': u'Suisse', 'ci': u'Côte d\'Ivoire',
    'cl': u'Chili', 'cm': u'Cameroun', 'cn': u'Chine',
    'co': u'Colombie', 'cr': u'Costa Rica', 'cy': u'Chypre',
    'cz': u'Tchéquie', 'de': u'Allemagne', 'dj': u'Djibouti',
    'do': u'République dominicaine', 'dz': u'Algérie',
    'ee': u'Estonie', 'es': u'Espagne', 'fr': u'France',
    'ge': u'Géorgie', 'gf': u'Guyane française', 'gh': u'Ghana',
    'gn': u'Guinée', 'gp': u'Guadeloupe', 'gr': u'Grèce',
    'gt': u'Guatemala', 'hn': u'Honduras', 'ht': u'Haïti',
    'hu': u'Hongrie', 'id': u'Indonésie', 'ie': u'Irlande',
    'il': u'Israël', 'in': u'Inde', 'iq': u'Irak', 'ir': u'Iran',
    'it': u'Italie', 'jo': u'Jordanie', 'kg': u'Kirghizistan',
    'kp': u'Corée du Nord', 'kr': u'Corée du Sud', 'kw': u'Koweït',
    'kz': u'Kazakhstan', 'lb': u'Liban', 'lk': u'Sri Lanka',
    'lt': u'Lituanie', 'lu': u'Luxembourg', 'ma': u'Maroc',
    'mc': u'Monaco', 'mg': u'Madagascar', 'mk': u'Macédoine du Nord',
    'ml': u'Mali', 'mm': u'Birmanie', 'mn': u'Mongolie',
    'mo': u'Macao', 'mq': u'Martinique', 'mv': u'Maldives',
    'mx': u'Mexique', 'my': u'Malaisie', 'nc': u'Nouvelle-Calédonie',
    'ne': u'Niger', 'nl': u'Pays-Bas', 'om': u'Oman', 'pe': u'Pérou',
    'pf': u'Polynésie française', 'ph': u'Philippines', 'pk': u'Pakistan',
    'pl': u'Pologne', 'pt': u'Portugal', 'py': u'Paraguay',
    'ro': u'Roumanie', 'rs': u'Serbie', 'ru': u'Russie',
    'rw': u'Rwanda', 'sa': u'Arabie saoudite', 'se': u'Suède',
    'si': u'Slovénie', 'sk': u'Slovaquie', 'sm': u'Saint-Marin',
    'sn': u'Sénégal', 'sv': u'Salvador', 'sx': u'Saint-Martin',
    'sy': u'Syrie', 'td': u'Tchad', 'th': u'Thaïlande',
    'tj': u'Tadjikistan', 'tm': u'Turkménistan', 'tr': u'Turquie',
    'tw': u'Taïwan', 'ua': u'Ukraine', 'uk': u'Royaume-Uni',
    'us': u'États-Unis', 'uz': u'Ouzbékistan', 've': u'Venezuela',
    'vn': u'Vietnam',
}


# ---- pure helpers (testable without network) ------------------------------

_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_QUALITY_RE = re.compile(r'\s*\((\d{3,4})p\)\s*')


def _comma_outside_quotes(s):
    """Index of the first comma outside double quotes, or -1."""
    in_q = False
    for i, c in enumerate(s):
        if c == '"':
            in_q = not in_q
        elif c == ',' and not in_q:
            return i
    return -1


def parse_m3u(text):
    """Parse an M3U playlist into channel dicts.

    Keys: name (cleaned), tvg_id, country (lower-case code, ''),
    logo, groups (raw group-title), url, referer, user_agent, quality (int).
    Handles #EXTVLCOPT http-referrer / http-user-agent and #EXTHTTP JSON
    headers.
    """
    out = []
    cur = None
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXTINF'):
            cur = {'name': '', 'tvg_id': '', 'logo': '', 'groups': '',
                   'url': '', 'referer': '', 'user_agent': '', 'quality': 0}
            m = re.match(r'#EXTINF:\s*[-\d.]+', line)
            rest = line[m.end():] if m else line[len('#EXTINF:'):]
            comma = _comma_outside_quotes(rest)
            attrs, title = (rest[:comma], rest[comma + 1:]) if comma >= 0 \
                else (rest, '')
            for k, v in _ATTR_RE.findall(attrs):
                k = k.lower()
                if k == 'tvg-id':
                    cur['tvg_id'] = v
                elif k == 'tvg-logo':
                    cur['logo'] = v
                elif k == 'group-title':
                    cur['groups'] = v
            cur['name'] = title.strip()
        elif cur is None:
            continue
        elif line.startswith('#EXTVLCOPT'):
            k, _, v = line[len('#EXTVLCOPT:'):].partition('=')
            k = k.strip().lower()
            if k == 'http-referrer':
                cur['referer'] = v.strip()
            elif k == 'http-user-agent':
                cur['user_agent'] = v.strip()
        elif line.startswith('#EXTHTTP:'):
            try:
                for k, v in json.loads(line[len('#EXTHTTP:'):]).items():
                    if k.lower() == 'referer':
                        cur['referer'] = v
                    elif k.lower() == 'user-agent':
                        cur['user_agent'] = v
            except Exception:
                pass
        elif line.startswith('#'):
            continue
        else:
            cur['url'] = line
            out.append(cur)
            cur = None
    return out


def country_of(ch):
    """Lower-case country code from tvg-id ('Foo.ma@SD' -> 'ma')."""
    tid = (ch.get('tvg_id') or '').split('@')[0]
    if '.' in tid:
        return tid.rsplit('.', 1)[-1].lower()
    return ''


def clean_name(ch):
    """Display name: drop '(1080p)', translate [Geo-blocked]/[Not 24/7]."""
    name = ch.get('name') or u'Serveur'
    name = _QUALITY_RE.sub(' ', name)
    name = name.replace('[Geo-blocked]', u'[géobloquée]')
    name = name.replace('[Not 24/7]', u'[non 24/7]')
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip() or u'Serveur'


def _sort_key(ch):
    cc = country_of(ch)
    try:
        pri = _FR_ORDER.index(cc)
    except ValueError:
        pri = len(_FR_ORDER)
    return (pri, COUNTRY.get(cc, cc or u'zzz'), clean_name(ch).lower())


def dedup(chans):
    """Drop same-name duplicates, keeping the highest quality variant,
    then sort by (country priority, country, name)."""
    best = {}
    for ch in chans:
        k = clean_name(ch).lower()
        if k not in best or ch.get('quality', 0) > best[k].get('quality', 0):
            best[k] = ch
    return sorted(best.values(), key=_sort_key)


def _server_url(ch):
    """Stream URL with the playlist headers embedded using Kodi's
    'url|key=val&...' pipe convention (decoded by resolve())."""
    url = ch['url']
    extra = {}
    if ch.get('referer'):
        extra['Referer'] = ch['referer']
    if ch.get('user_agent'):
        extra['User-Agent'] = ch['user_agent']
    if extra:
        url = '{0}|{1}'.format(url, urlencode(extra))
    return url


def _event(ch):
    tag = COUNTRY.get(country_of(ch), country_of(ch))
    return {
        'title': clean_name(ch),
        'code': tag,          # shown as the orange [Pays] tag by default.py
        'league': '',
        'start_ms': 0,        # live 24/7: no start time
        'poster': ch.get('logo') or '',
        'status': 'live',
        'servers': [[clean_name(ch), _server_url(ch)]],
    }


def _events(chans, sports_only=False):
    picked = []
    for ch in chans:
        if sports_only and 'Sports' not in (ch.get('groups') or '').split(';'):
            continue
        picked.append(ch)
    return [_event(ch) for ch in dedup(picked)]


def _resolve(server_url):
    """site_play contract: server URLs are direct m3u8 links, optionally
    carrying Kodi pipe headers. With a Referer, return
    (naked_url, referer_url) so site_play derives Referer/Origin from it;
    otherwise the naked URL (site.UA is sent anyway)."""
    if '|' not in server_url:
        return server_url
    naked, _, qs = server_url.partition('|')
    params = dict(parse_qsl(qs))
    ref = params.get('Referer')
    return (naked, ref) if ref else naked


# ---- network + cache -------------------------------------------------------

def _cache_path():
    try:
        import xbmcaddon
        import xbmcvfs
        profile = xbmcaddon.Addon().getAddonInfo('profile')
        try:
            return os.path.join(xbmcvfs.translatePath(profile),
                                'iptv_cache.json')
        except AttributeError:  # Kodi 18
            import xbmc
            return os.path.join(xbmc.translatePath(profile),
                                'iptv_cache.json')
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), 'sporthd_iptv_cache.json')


def _fetch_text(url):
    import requests
    r = requests.get(url, headers={'User-Agent': UA}, timeout=30)
    r.raise_for_status()
    data = r.content
    if isinstance(data, bytes):
        data = data.decode('utf-8', 'replace')
    return data


def _cached_text(url):
    """Playlist text, cached IPTV_CACHE_H hours in the add-on profile;
    falls back to the last copy when offline."""
    now = int(time.time())
    path = _cache_path()
    cache = {}
    try:
        with open(path) as f:
            cache = json.load(f)
    except Exception:
        pass
    entry = cache.get(url) or {}
    if now - entry.get('t', 0) < IPTV_CACHE_H * 3600 and entry.get('d'):
        return entry['d']
    try:
        text = _fetch_text(url)
    except Exception:
        text = ''
    if not text:
        return entry.get('d') or ''
    cache[url] = {'t': now, 'd': text}
    try:
        with open(path, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass
    return text


def _all_channels(url):
    return parse_m3u(_cached_text(url))


# ---- subsites (same contract as site_futbolmov.SUBSITES) -------------------

class _Sub(object):
    TAG_LAST = True    # menu label: "Chaîne [Pays]" -- no time slot for 24/7
    NO_FR_FILTER = True  # the whole point: French channels from OTHER countries

    def __init__(self, key, name, desc, getter):
        self.KEY, self.NAME, self.DESC, self._get = key, name, desc, getter
        self.UA = UA

    def list_events(self):
        return self._get()

    def resolve(self, server_url):
        return _resolve(server_url)


SUBSITES = [
    _Sub('iptv_fr_sport', u'Sport en français',
         u'[B]Chaînes sportives en français[/B]\n\n'
         u'L\'Équipe, Equidia, FIFA+, Africa 24 Sport, et les chaînes '
         u'sportives des pays francophones (Arryadia, El-Heddaf, CRTV…). '
         u'Direct 24h/24, listes publiques iptv-org.',
         lambda: _events(_all_channels(URL_FRA), sports_only=True)),
    _Sub('iptv_fr_all', u'Chaînes en français (tous pays)',
         u'[B]Toutes les chaînes en français[/B]\n\n'
         u'France, Belgique, Suisse, Canada, Maroc, Algérie, Sénégal, '
         u'Côte d\'Ivoire… Les matchs passent aussi sur les chaînes '
         u'généralistes nationales. Triées par pays.',
         lambda: _events(_all_channels(URL_FRA))),
    _Sub('iptv_world_sport', u'Sport du monde entier',
         u'[B]Chaînes sportives du monde entier[/B]\n\n'
         u'Chaque pays et ses chaînes sportives : Europe, Amériques, '
         u'Afrique, Asie. Pays francophones listés en premier.',
         lambda: _events(_all_channels(URL_SPORTS))),
]

NAME_KEY = SUBSITES[0].KEY  # unused; kept for symmetry with other modules


if __name__ == '__main__':
    _m3u = (
        '#EXTM3U\n'
        '#EXTINF:-1 tvg-id="Foo.ma@SD" tvg-logo="http://l/1.png" '
        'group-title="Sports",Foo TV (720p)\n'
        '#EXTVLCOPT:http-referrer=http://www.foo.ma/\n'
        '#EXTVLCOPT:http-user-agent=Mozilla/5.0 X\n'
        'https://cdn.example.com/foo.m3u8\n'
        '#EXTINF:-1 tvg-id="Bar.fr@HD" group-title="News",Bar TV (1080p)\n'
        'https://cdn.example.com/bar.m3u8\n'
        '#EXTINF:-1 tvg-id="Baz.fr@HD" group-title="News",Bar TV (480p)\n'
        'https://cdn.example.com/baz.m3u8\n'
        '#EXTINF:-1 group-title="Sports",Geo TV (1080p) [Geo-blocked]\n'
        'https://cdn.example.com/geo.m3u8\n')
    _chans = parse_m3u(_m3u)
    assert len(_chans) == 4, len(_chans)
    _foo = _chans[0]
    assert _foo['name'] == 'Foo TV (720p)' and _foo['tvg_id'] == 'Foo.ma@SD'
    assert _foo['logo'] == 'http://l/1.png' and _foo['groups'] == 'Sports'
    assert _foo['referer'] == 'http://www.foo.ma/' \
        and _foo['user_agent'] == 'Mozilla/5.0 X'
    assert country_of(_foo) == 'ma'
    # dedup keeps the 1080p Bar TV, drops the 480p one; sort: francophone
    # countries first (fr < ma), no-country last
    _ded = dedup(_chans)
    assert [c['name'] for c in _ded] == \
        ['Bar TV (1080p)', 'Foo TV (720p)', 'Geo TV (1080p) [Geo-blocked]'], \
        [c['name'] for c in _ded]
    assert country_of(_ded[0]) == 'fr'
    # name cleaning
    assert clean_name(_chans[0]) == 'Foo TV'
    assert clean_name(_chans[3]) == u'Geo TV [géobloquée]'
    # sports filter
    assert [e['title'] for e in _events(_chans, sports_only=True)] == \
        [u'Foo TV', u'Geo TV [géobloquée]']
    # header embedding + resolve round-trip
    _ev = _events(_chans)[1]  # Foo TV (Maroc) comes after Bar TV (France)
    _srv = _ev['servers'][0][1]
    assert _srv.startswith('https://cdn.example.com/foo.m3u8|Referer=')
    _r = _resolve(_srv)
    assert _r == ('https://cdn.example.com/foo.m3u8', 'http://www.foo.ma/'), _r
    # no headers -> plain URL, resolve returns it untouched
    _r2 = _resolve('https://cdn.example.com/bar.m3u8')
    assert _r2 == 'https://cdn.example.com/bar.m3u8'
    # country tag is French
    assert _events(_chans)[0]['code'] == u'France'
    assert _events(_chans)[1]['code'] == u'Maroc'
    assert _events(_chans)[2]['code'] == u''
    print('ok')
