# -*- coding: utf-8 -*-
"""
DaddyLive (thedaddy.pk) source for plugin.video.sporthdme.

Kodi-agnostic on purpose: the parse/decode functions take raw text so they run
(and self-test) outside Kodi. Only the fetch helpers touch the network.

Flow:
  https://thedaddy.pk/index.php -> today's schedule (static HTML):
      .schedule__dayTitle  "Sunday 01st Nov 2026 - Schedule Time UK GMT"
      .schedule__catHeader "Football" / "Other Sports" ... (sport per block)
      .schedule__event     time (HH:MM, UK) + title + channels:
          <a href="/watch.php?id=116" title="beIN Sports 1 France">
  https://thedaddy.pk/24-7-channels.php -> ~900 always-on channels (~40 FR).

Resolution (2026 layout, verified against Plaza4756/dlhd-kodi and
gookie-dev/StepDaddyLiveHD, both active in Oct 2026):
  /watch.php?id=N
    -> /stream/stream-N.php   (same origin, carries the player iframe)
    -> player iframe on a rotating host (tossequip.net, dembed.st, ...)
       with two known layouts:
       a) window._econfig = '<outer b64>'  -> resources.modules.econfig
          (4 chunks, reorder [2,0,3,1], drop char #3) -> JSON with
          stream_url_nop2p      [primary path]
       b) const SRC / CHANNEL / EDGE      -> {EDGE}/api/playlist-token?slug=...
          -> {SRC}?t={token}&_={ts}         [fallback path, StepDaddyLiveHD]
       c) bare file:/source: m3u8 regex   [last resort]

Notes:
  - The schedule day title says "UK GMT": times are read as UTC when the
    label says GMT, Europe/London when it says BST.
  - The final player host 403s datacenter IPs on purpose; from a residential
    Kodi box it answers. The public forks above run the same chain from
    home IPs -- only the last HTTP hop needs the user's network.
"""

from __future__ import absolute_import, print_function

import re
import json
import time
import calendar

try:
    from html import unescape as _unescape  # Py3
except ImportError:
    from HTMLParser import HTMLParser  # Py2

    def _unescape(s):
        return HTMLParser().unescape(s or u'')

try:
    string_types = (str, unicode)  # noqa: F821 (Python 2)
except NameError:
    string_types = (str,)

NAME = u'Matchs du jour (FR)'
KEY = 'dlhd'
DESC = (u'[B]Matchs du jour (FR)[/B]\n\n'
        u'Planning DaddyLive du jour filtré sur les chaînes françaises : '
        u'beIN Sports France, Canal+, Canal+ Sport/Foot, RMC Sport, '
        u'Ligue1+ (DAZN), Eurosport France, L\'Équipe, Sport en France…\n'
        u'Chaque match affiche ses chaînes FR cliquables (résolution '
        u'automatique vers le flux HLS).')
TAG_LAST = False

DESC_247 = (u'[B]Chaînes FR (24/7)[/B]\n\n'
            u'Chaînes françaises disponibles en continu sur DaddyLive : '
            u'beIN Sports 1/2/3 + MAX 4-10 France, Canal+, Canal+ Sport/Foot, '
            u'Canal+ F1/MotoGP, RMC Sport 1/2, DAZN Ligue 1+, Eurosport 1/2 '
            u'France, L\'Équipe, Sport en France, Tennis+…\n'
            u'Et pour les grands événements : TF1, M6, W9, France 2-5, '
            u'C8, TMC, 6ter, Arte.')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

# Domain order: primary + seeds that redirect to a live mirror.
DOMAINS = ('https://thedaddy.pk', 'https://dlive.sx', 'https://dlhd.pk')

# v3.7.0 — configuration distante : la liste des domaines DaddyLive de
# secours est publiée sur le site GitHub Pages de l'addon. Si les trois
# domaines intégrés tombent, l'addon essaie ceux du fichier distant
# (cache 6 h) : on peut réparer l'add-on en éditant un JSON sur le dépôt,
# sans republier de nouvelle version. Panne réseau -> [] silencieux.
REMOTE_CFG_URL = 'https://chakir1981.github.io/tonton.github.io/sporthdme_remote.json'
REMOTE_CFG_TTL = 6 * 3600
_remote_cfg = {'ts': 0.0, 'domains': None}


def remote_domains():
    """Domaines de secours depuis sporthdme_remote.json (jamais de raise)."""
    now = time.time()
    if _remote_cfg['domains'] is not None and now - _remote_cfg['ts'] < REMOTE_CFG_TTL:
        return _remote_cfg['domains']
    doms = []
    try:
        txt, code = _get(REMOTE_CFG_URL, timeout=10)
        if code == 200:
            data = json.loads(txt)
            for d in (data.get('domains') or []):
                d = str(d).strip().rstrip('/')
                if d.startswith('http') and d not in DOMAINS and d not in doms:
                    doms.append(d)
    except Exception:
        doms = []
    _remote_cfg['ts'] = now
    _remote_cfg['domains'] = doms
    return doms

_domain = None          # working domain, remembered for the session

MAX_EVENT_AGE_H = 6     # hide events that started more than 6h ago
LIVE_WINDOW_MIN = 240   # lime "live" colour window

# ---------------------------------------------------------------------------
# Filtre « chaîne française » STRICT — les variantes étrangères du même
# groupe (Canal+ Sport SK/CZ/Poland, DAZN Spain/DE/IT, beIN Arabic/Malaysia,
# Eurosport Greece/NL...) ne comptent PAS comme françaises.
# ---------------------------------------------------------------------------
_ENDS_FR = re.compile(r'France$', re.IGNORECASE)
# v3.7.1 — « Canal+ Sport 2/3/4/5 » et « Canal+ 1 » sont des chaînes
# ÉTRANGÈRES (Pologne…) : la France n'a que Canal+ Sport (sans numéro),
# Canal+ Sport 360 et Canal+ Sport Afrique. On a donc retiré « ?\d+ » de la
# branche Sport pour ne plus accepter les variantes numérotées.
_BRANDS_FR = re.compile(
    r'^(Canal\+ ?(France|Sport( ?360| ?Afrique)?|Foot|Premium|D[ée]cal[ée]|Cinema|Series|'
    r'Formula ?1|Moto ?GP|Golf|Tennis)'
    r'|beIN Sports (MAX )?\d+ France'
    r'|RMC Sport ?\d+'
    r'|DAZN ?(Ligue ?1\+?|France)'
    r'|Ligue ?1\+ ?\d* ?France'
    r'|Eurosport ?\d+ ?(France)?$'
    r"|L'Équipe|L'Equipe"
    r'|Sport en France'
    r'|France [2-5]'
    r'|\+?foot\+( ?\d+)?'
    r'|Tennis\+( ?\d+)?'
    r'|Golf\+( ?\d+)?'
    r'|Automoto( la cha[iî]ne)?'
    r'|Moto ?GP\+'
    r'|Infosport\+?)$',
    re.IGNORECASE)
_NOT_FR = re.compile(
    r'(SK|CZ|Poland|Spain|Deutschland|Germany|Portugal|Italy|Belgium|'
    r'Netherlands|Nederland|Romania|T[uü]rkiye|Turkey|Arabia|Arabic|Mena|'
    r'English|Deutsch|Espa[nñ]ol|Italiano|Portugu[eè]s|Nederlands|'
    r'Scandinavia|Finland|Sweden|Norway|Denmark|Greece|Hungary|Bulgaria|'
    r'Serbia|Croatia|Slovenia|Slovakia|Ukraine|Russia|Kazakhstan|Israel|'
    r'India|Pakistan|Asia|Brasil|Brazil|Mexico|Argentina|Chile|'
    r'Australia|Qatar|USA|Malaysia|Espa[nñ]ol|HD Q)',
    re.IGNORECASE)


def is_fr_channel(name):
    """True pour une vraie chaîne française (nom exact, pas une variante)."""
    n = _unescape((name or u'')).strip()
    if not n:
        return False
    if _NOT_FR.search(n):
        return False
    return bool(_ENDS_FR.search(n) or _BRANDS_FR.match(n))


# ---------------------------------------------------------------------------
# Pure parsers (testable without network)
# ---------------------------------------------------------------------------

_DAY_RE = re.compile(r'schedule__dayTitle">\s*([^<]+)', re.DOTALL)
_CAT_RE = re.compile(r'schedule__catHeader[^>]*>\s*'
                     r'<div class="card__meta">([^<]+)</div>', re.DOTALL)
_EV_RE = re.compile(
    r'data-title="([^"]*)"[^>]*>\s*'
    r'<span class="schedule__time" data-time="(\d{2}:\d{2})">\2</span>\s*'
    r'<span class="schedule__eventTitle">(.*?)</span>',
    re.DOTALL)
_CH_RE = re.compile(r'href="/watch\.php\?id=(\d+)" title="([^"]+)"')
_ORD_RE = re.compile(r'(\d{1,2})(?:st|nd|rd|th) ', re.IGNORECASE)


def _day_info(html):
    """(date_str 'YYYY-MM-DD', tz_name) from the schedule day title."""
    m = _DAY_RE.search(html)
    if not m:
        return None, 'UTC'
    title = _unescape(m.group(1))
    tz_name = 'Europe/London' if 'BST' in title else 'UTC'  # label says "UK GMT"
    clean = _ORD_RE.sub(r'\1 ', title)
    dm = re.search(r'(\d{1,2} \w+ \d{4})', clean)
    if not dm:
        return None, tz_name
    from dateutil.parser import parse as _dtparse
    try:
        return _dtparse(dm.group(1)).strftime('%Y-%m-%d'), tz_name
    except Exception:
        return None, tz_name


def _start_ms(date_str, hhmm, tz_name):
    """Event start as UTC epoch ms (0 if undatable). Py2/Py3 safe."""
    if not date_str or not hhmm:
        return 0
    try:
        from dateutil.tz import gettz
        from datetime import datetime
        dt = datetime.strptime('%s %s' % (date_str, hhmm), '%Y-%m-%d %H:%M')
        dt = dt.replace(tzinfo=gettz(tz_name) or gettz('UTC'))
        return int(calendar.timegm(dt.utctimetuple())) * 1000
    except Exception:
        return 0


def parse_schedule(html, domain):
    """Flatten the schedule HTML into normalized event dicts.

    Every event keeps ALL its channels (FR and foreign): the addon's shared
    « sources françaises » filter decides what stays visible, so switching
    french_only off reveals the full 600+ events agenda.
    """
    date_str, tz_name = _day_info(html)

    # (position, sport) landmarks so each event inherits its section sport
    marks = []
    for m in _CAT_RE.finditer(html):
        sport = _unescape(m.group(1)).strip()
        if sport:
            marks.append((m.start(), sport))
    marks.append((len(html), u''))  # sentinel

    def sport_at(pos):
        cur = u''
        for p, s in marks:
            if p > pos:
                break
            cur = s
        return cur

    events = []
    ev_matches = list(_EV_RE.finditer(html))
    for i, m in enumerate(ev_matches):
        time_hhmm = m.group(2)
        title = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        title = _unescape(title)
        # channels: from the end of this header to the next event header
        nxt = ev_matches[i + 1].start() if i + 1 < len(ev_matches) else len(html)
        seg = html[m.end():nxt]
        seen, chans = set(), []
        for cid, cname in _CH_RE.findall(seg):
            cname = _unescape(cname).strip()
            if cname in seen:
                # same channel mirrored under a second watch id -> keep it,
                # clearly labelled, as a built-in fallback feed
                dup = [c for c in chans if c[0].startswith(cname)]
                if dup and not cname + u' (2)' in [c[0] for c in chans]:
                    chans.append([cname + u' (2)',
                                  u'%s/watch.php?id=%s' % (domain, cid)])
                continue
            seen.add(cname)
            chans.append([cname, u'%s/watch.php?id=%s' % (domain, cid)])
        if not chans:
            continue
        events.append({
            'title': title,
            'code': u'',
            'start_ms': _start_ms(date_str, time_hhmm, tz_name),
            'sport': sport_at(m.start()),
            'league': u'',
            'poster': u'',
            'status': 'soon',
            'servers': chans,
        })
    return events


def _status(start_ms):
    """'live' | 'soon' | 'done' from the absolute start instant."""
    if not start_ms:
        return 'soon'
    now_ms = time.time() * 1000
    if now_ms < start_ms:
        return 'soon'
    if now_ms <= start_ms + LIVE_WINDOW_MIN * 60 * 1000:
        return 'live'
    return 'live'  # still listed until MAX_EVENT_AGE_H; colour stays lime


def parse_channels_247(html, domain):
    """All 24/7 channels as (name, watch_url) pairs."""
    out, seen = [], set()
    for block in re.finditer(r'<a class="card"[^>]*>(.*?)</a>', html, re.DOTALL):
        head = block.group(0)
        mid = re.search(r'href="/watch\.php\?id=(\d+)"', head)
        if not mid:
            continue
        t = re.search(r'data-title="([^"]+)"', head)
        if not t:
            t = re.search(r'class="card__title">\s*(.*?)\s*</div>', head,
                          re.DOTALL)
            if not t:
                continue
            name = re.sub(r'<[^>]+>', '', t.group(1))
        else:
            name = t.group(1)
        name = _unescape(name).strip().lstrip('#').strip()
        if not name or mid.group(1) in seen:
            continue
        seen.add(mid.group(1))
        out.append((name, u'%s/watch.php?id=%s' % (domain, mid.group(1))))
    return out


# ---------------------------------------------------------------------------
# Network (Kodi / real use)
# ---------------------------------------------------------------------------

def _get(url, referer=None, timeout=20, origin=None):
    import requests
    import requests.packages.urllib3 as _u3
    try:
        _u3.disable_warnings()
    except Exception:
        pass
    hdr = {'User-Agent': UA,
           'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
           'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8'}
    if referer:
        hdr['Referer'] = referer
    if origin:
        hdr['Origin'] = origin
    try:
        r = requests.get(url, headers=hdr, timeout=timeout)
    except Exception:
        r = requests.get(url, headers=hdr, timeout=timeout, verify=False)
    return r.content.decode('utf-8', 'replace'), r.status_code


def _working_domain():
    """Primary domain first, then seeds, then the remote config's domains
    (v3.7.0 : miroirs de secours publiés sur le GitHub Pages de l'addon,
    modifiables sans nouvelle version). Remember what worked."""
    global _domain
    if _domain:
        return _domain
    for d in DOMAINS:
        try:
            txt, code = _get(d + '/index.php', timeout=15)
            if code == 200 and 'schedule__event' in txt:
                _domain = d.rstrip('/')
                return _domain
        except Exception:
            continue
    # v3.7.0 — domaines de secours du fichier distant (fetché seulement si
    # les domaines intégrés ont tous échoué)
    for d in remote_domains():
        try:
            txt, code = _get(d + '/index.php', timeout=15)
            if code == 200 and 'schedule__event' in txt:
                _domain = d.rstrip('/')
                return _domain
        except Exception:
            continue
    return None


def list_events():
    domain = _working_domain()
    if not domain:
        raise Exception('DaddyLive injoignable (%s)' % ', '.join(DOMAINS))
    txt, code = _get(domain + '/index.php', referer=domain + '/')
    events = parse_schedule(txt, domain)
    now_ms = time.time() * 1000
    out = []
    for e in events:
        if e['start_ms'] and now_ms > e['start_ms'] + MAX_EVENT_AGE_H * 3600 * 1000:
            continue  # finished / stale
        e['status'] = _status(e['start_ms'])
        out.append(e)
    out.sort(key=lambda e: e['start_ms'])
    return out


def list_channels_247():
    domain = _working_domain()
    if not domain:
        raise Exception('DaddyLive injoignable (%s)' % ', '.join(DOMAINS))
    txt, code = _get(domain + '/24-7-channels.php', referer=domain + '/')
    return parse_channels_247(txt, domain)


# ---------------------------------------------------------------------------
# v3.6.2 — enrichissement footao (footao.tv) : quand la grille DaddyLive ne
# liste qu'une seule chaîne FR pour un match, footao en donne souvent une
# seconde (diffuseur1/diffuseur2). On interroge la page club footao du match,
# on le retrouve par équipes, et on ajoute les chaînes FR manquantes via les
# IDs DaddyLive connus de la liste 24/7.
# Technique issue de developpeurbox/hass-footao (maj 2026-10-29) :
#   programmetv.php?eq=<club>  ->  redirection JS window.location.replace
#   ->  page club avec <div itemscope SportsEvent> (matchs « riches »,
#       content="YYYY-MM-DDTHH:MM", icône diffuseur
#       match-canal-foot-direct-live-ce-soir) et <div class="fin"> (du jour).
# ---------------------------------------------------------------------------

FOOTAO_BASE = 'https://www.footao.tv'
FOOTAO_TTL = 3600          # cache club = 1 h (comme hass-footao)
_ftao_cache = {}           # eq -> (horloge, [matchs])

# clubs.json de developpeurbox/hass-footao (Ligue 1, Ligue 2, Liga, Serie A,
# Premier League, Bundesliga, Europe, Équipe Nationale) — nom affiché -> slug
# footao « eq » (les valeurs à « | » sont des alias acceptés par le site).
FOOTAO_CLUBS = {
    "AC Milan": "Milan", "Alaves": "Alaves", "Algérie": "Algerie",
    "Allemagne": "Allemagne", "Anderlecht": "Anderlecht",
    "Angers": "Angers", "Angleterre": "Angleterre", "Annecy": "Annecy",
    "Ajaccio": "Ajaccio", "Amiens": "Amiens", "Argentine": "Argentine", "Arsenal": "Arsenal",
    "Aston Villa": "Aston Villa", "Atalanta": "Atalanta",
    "Athletic Bilbao": "Bilbao|Athletic Bilbao",
    "Atletico Madrid": "Atlético", "Augsbourg": "Augsbourg",
    "Australie": "Australie", "Autriche": "Autriche", "Auxerre": "Auxerre",
    "Barcelona": "Barcelone", "Bayern Munich": "Bayern",
    "Belgique": "Belgique", "Besiktas": "Besiktas", "Bologna": "Bologne",
    "Bosnie": "Bosnie", "Boulogne": "Boulogne", "Bastia": "Bastia",
    "Bournemouth": "Bournemouth", "Brentford": "Brentford",
    "Bresil": "Bresil", "Brest": "Brest", "Brighton": "Brighton",
    "Bétis": "Bétis", "Cagliari": "Cagliari", "Canada": "Canada",
    "CapVert": "CapVert", "Celta Vigo": "Celta Vigo", "Chelsea": "Chelsea",
    "Clermont": "Clermont", "Cologne": "Cologne", "Colombie": "Colombie",
    "Como": "Como", "Coventry": "Coventry City", "Croatie": "Croatie",
    "Crystal Palace": "Crystal P.|Crystal Palace", "Curacao": "Curacao",
    "Côte d'Ivoire": "Côte d’Ivoire|Cote d'Ivoire|Côte d'Ivoire",
    "Deportivo": "Deportivo La Corogne|Deportivo de A Coruña",
    "Dijon": "Dijon", "Dortmund": "Dortmund", "Dunkerque": "Dunkerque",
    "Elche": "Elche", "Elversberg": "Elversberg", "Espagne": "Espagne",
    "Espanyol": "Espanyol B.|Espanyol Barcelone", "Everton": "Everton",
    "Fiorentina": "Fiorentina", "France": "France",
    "Francfort": "Francfort E.|Eintracht Francfort",
    "Fribourg": "Fribourg", "Frosinone": "Frosinone", "Fulham": "Fulham",
    "Genoa": "Genoa||G&ecirc;nes", "Getafe": "Getafe", "Ghana": "Ghana",
    "Grenoble": "Grenoble", "Guingamp": "Guingamp", "Hamburg": "Hambourg",
    "Hoffenheim": "Hoffenheim", "Hull City": "Hull City",
    "Inter Milan": "Inter", "Ipswich": "Ipswich Town", "Iran": "Iran",
    "Italie": "Italie", "Japon": "Japon", "Juventus": "Juventus",
    "Laval": "Laval", "Lazio": "Lazio", "Le Havre": "Le Havre",
    "Le Mans": "Le Mans", "Lecce": "Lecce", "Leeds": "Leeds",
    "Leipzig": "Leipzig", "Lens": "Lens", "Levante": "Levante UD",
    "Leverkusen": "Leverkusen|Bayer Leverkusen", "Lille": "Lille",
    "Liverpool": "Liverpool", "Lorient": "Lorient", "Lyon": "Lyon OL",
    "Malaga": "Malaga", "Manchester City": "Manchester C.",
    "Manchester Utd": "Manchester U.", "Maroc": "Maroc",
    "Marseille": "Marseille OM", "Mayence": "Mayence FSV", "Metz": "Metz",
    "Mexique": "Mexique", "Monaco": "Monaco",
    "Monchengladbach": "Monchengladbach", "Montpellier": "Montpellier",
    "Monza": "Monza", "Nancy": "Nancy", "Nantes": "Nantes",
    "Naples": "Naples", "Newcastle": "Newcastle", "Nice": "Nice",
    "Norvège": "Norvege", "Nottingham": "Nottingham", "Osasuna": "Osasuna",
    "Paderborn": "Paderborn", "Paris FC": "Paris FC", "Parme": "Parme",
    "Pau": "Pau", "Pays-Bas": "Pays-Bas%7C%7CHollande",
    "Portugal": "Portugal", "PSG": "Paris PSG",
    "Racing Santander": "Racing Santander|Racing de Santander",
    "Rayo Vallecano": "Rayo V.|Rayo Vallecano",
    "Real Madrid": "Real Madrid",
    "Real Sociedad": "R. Sociedad|Real Sociedad", "Red Star": "Red Star",
    "Reims": "Reims", "Rennes": "Rennes", "Rodez": "Rodez", "Roma": "Roma",
    "Saint-Etienne": "Saint-Etienne", "Sassuolo": "Sassuolo",
    "Schalke": "Schalke", "Sochaux": "Sochaux", "Strasbourg": "Strasbourg",
    "Stuttgart": "Stuttgart", "Suisse": "Suisse",
    "Sunderland": "Sunderland", "Sénégal": "Senegal",
    "Séville": "Séville FC", "Tchéquie": "Tchequie", "Torino": "Torino",
    "Tottenham": "Tottenham", "Toulouse": "Toulouse", "Troyes": "Troyes",
    "Tunisie": "Tunisie", "Turquie": "Turquie", "Udinese": "Udinese",
    "Union Berlin": "Union B.|Union Berlin", "Uruguay": "Uruguay",
    "Valenciennes": "Valenciennes", "Versailles": "Versailles",
    "Villefranche": "Villefranche", "Quevilly": "Quevilly",
    "Nîmes": "Nimes", "Orléans": "Orleans", "Châteauroux": "Chateauroux",
    "Valencia": "Valence CF", "Venezia": "Venezia",
    "Villarreal": "Villarreal",
    "Werder Brême": "Werder|Werder Br&ecirc;me", "Égypte": "Egypte",
    "États-Unis": "Etats-Unis%7C%7CUSA%20Am%26eacute%3Brique",
}

# alias DaddyLive -> clé de FOOTAO_CLUBS (normalisés sans accents)
_FTAO_ALIAS = {
    'om': 'marseille', 'ol': 'lyon', 'ogc nice': 'nice',
    'stade brestois': 'brest', 'stade brestois 29': 'brest',
    'paris saint germain': 'psg', 'olympique marseille': 'marseille',
    'olympique de marseille': 'marseille', 'olympique lyonnais': 'lyon',
    'as monaco': 'monaco', 'rc lens': 'lens', 'losc': 'lille',
    'losc lille': 'lille', 'stade rennais': 'rennes', 'fc rennes': 'rennes',
    'atletico': 'atletico madrid', 'atletico madrid': 'atletico madrid',
    'eintracht frankfurt': 'francfort', 'koln': 'cologne',
    'fc koln': 'cologne', 'hamburger sv': 'hamburg',
    'bayer leverkusen': 'leverkusen', 'newcastle united': 'newcastle',
    'west ham united': 'west ham', 'leeds united': 'leeds',
    'stade rennais fc': 'rennes',
    'bayern': 'bayern munich', 'man city': 'manchester city',
    'man utd': 'manchester utd', 'manchester united': 'manchester utd',
    'tottenham hotspur': 'tottenham', 'inter': 'inter milan',
    'milan': 'ac milan', 'psg paris': 'psg', 'sporting cp': 'sporting',
}

# nom footao (normalisé) -> (nom DaddyLive, id watch)
FOOTAO_CHAN_ID = {
    'canal+ foot': ('Canal+ Foot France', 463),
    'canal+': ('Canal+ France', 121),
    'canal+ sport': ('Canal+ Sport France', 122),
    'canal+ sport 360': ('Canal+ Sport 360', 464),
    'canal+ sport360': ('Canal+ Sport 360', 464),
    'canal+ f1': ('Canal+ Formula 1', 273),
    'canal+ formula 1': ('Canal+ Formula 1', 273),
    'canal+ motogp': ('CANAL+ MotoGP France', 271),
    'bein sports 1': ('beIN Sports 1 France', 116),
    'bein sports 2': ('beIN Sports 2 France', 117),
    'bein sports 3': ('beIN Sports 3 France', 118),
    'bein sports max 4': ('beIN Sports MAX 4 France', 494),
    'bein sports max 5': ('beIN Sports MAX 5 France', 495),
    'bein sports max 6': ('beIN Sports MAX 6 France', 496),
    'bein sports max 7': ('beIN Sports MAX 7 France', 497),
    'bein sports max 8': ('beIN Sports MAX 8 France', 498),
    'bein sports max 9': ('beIN Sports MAX 9 France', 499),
    'bein sports max 10': ('beIN Sports MAX 10 France', 500),
    'rmc sport 1': ('RMC Sport 1 France', 119),
    'rmc sport 2': ('RMC Sport 2 France', 120),
    'ligue1+': ('DAZN Ligue 1 France', 960),
    'ligue 1+': ('DAZN Ligue 1 France', 960),
    'dazn ligue 1': ('DAZN Ligue 1 France', 960),
    'dazn ligue1+': ('DAZN Ligue 1 France', 960),
    'eurosport 1': ('Eurosport 1 France', 772),
    'eurosport 2': ('Eurosport 2 France', 773),
    "l'equipe": ("L'Équipe France", 645),
    'lequipe': ("L'Équipe France", 645),
    'sport en france': ('Sport en France', 965),
    'tennis+': ('Tennis+ 10', 709),
    'automoto': ('Automoto la chaîne', 961),
    'tf1': ('TF1 France', 469),
    'm6': ('M6 France', 470),
    'w9': ('W9 France', 959),
    'tmc': ('TMC France', 955),
    'c8': ('C8 France', 956),
    'france 2': ('France 2', 950),
    'france 3': ('France 3', 951),
    'france 4': ('France 4', 952),
    'france 5': ('France 5', 953),
    '6ter': ('6ter France', 963),
    'arte': ('Arte France', 958),
}

_FTAO_SIMPLE_RE = re.compile(
    r'<div class="fin">\s*<time[^>]*>(\d{2}:\d{2})</time>(.*?)</div>', re.DOTALL)
_FTAO_RICH_RE = re.compile(
    r'<div itemscope itemtype="[^"]*SportsEvent">(.*?)</div>', re.DOTALL)
_FTAO_DT_RE = re.compile(r'content="(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})"')
_FTAO_CHAN_RE = re.compile(
    r'/match-([a-z0-9-]*?)-direct-live[^"]*"[^>]*>\s*<img[^>]*'
    r'alt="match ([^"]+?)(?:\s*foot)?\s*programme soir"', re.IGNORECASE)
_FTAO_RC_RE = re.compile(r'<a[^>]*class="rc"[^>]*>(.*?)</a>', re.DOTALL)
_FTAO_TEAM_SEP_RE = re.compile(r'\s*(?:&#183;|&middot;|·)\s*')
_FTAO_SUFFIX_RE = re.compile(
    r'\s*\b(f[ée]m\.?|fem(?:mes|inine)?\.?|hope?es|r[ée]serv[ée]s?|'
    r'(?:u|under)[- ]?\d{1,2}|jeunes)\s*$', re.IGNORECASE)


def _norm_team(s):
    """clé de comparaison : minuscules, sans accents ni ponctuation."""
    s = _unescape((s or u'')).strip().lower()
    try:
        import unicodedata
        s = u''.join(c for c in unicodedata.normalize('NFKD', s)
                     if not unicodedata.combining(c))
    except Exception:
        pass
    s = re.sub(r'[^a-z0-9+ ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _club_key(team_norm):
    """normalise un nom d'équipe vers une clé FOOTAO_CLUBS (alias inclus)."""
    t = _FTAO_ALIAS.get(team_norm, team_norm)
    return t


def teams_from_title(title):
    """['⚽ France - Ligue 1 : Paris FC 🇫🇷 vs PSG 🇫🇷'] -> ['Paris FC', 'PSG'].

    Renvoie [] pour les événements sans affrontement (finales WTA, GP…).
    """
    t = _unescape(title or u'')
    if u'vs' not in t.lower():
        return []
    m = re.search(r':\s*(.+)$', t, re.DOTALL)  # saute « Championnat : »
    if m:
        t = m.group(1)
    parts = re.split(r'\s+vs\.?\s+', t, flags=re.IGNORECASE)
    if len(parts) != 2:
        return []
    out = []
    for p in parts:
        p = re.sub(r'[^\w\s\'’-]+', ' ', p, flags=re.UNICODE)  # drapeaux/emoji
        p = _FTAO_SUFFIX_RE.sub(u'', p.strip())
        if p:
            out.append(p)
    return out if len(out) == 2 else []


def parse_footao_page(html, today=None):
    """Page club footao -> [{'dt': 'YYYY-MM-DD', 'time': 'HH:MM',
    'teams': [A, B], 'chans': ['Canal+ Foot', …]}]."""
    out = []
    seen = set()

    def add(dt, hhmm, blob):
        rc = _FTAO_RC_RE.search(blob)
        if not rc:
            return
        name = re.sub(r'<[^>]+>', '', rc.group(1))
        name = _unescape(name).strip()
        name = _FTAO_SUFFIX_RE.sub(u'', name).strip()
        if not name:
            return
        teams = [x.strip() for x in _FTAO_TEAM_SEP_RE.split(name) if x.strip()]
        if len(teams) < 2:
            return
        chans = [_unescape(c[1]).strip() for c in _FTAO_CHAN_RE.findall(blob)]
        chans = [c for c in chans if c]
        key = (dt or '', hhmm or '', _norm_team(u'|'.join(teams)))
        if key in seen:
            return
        seen.add(key)
        out.append({'dt': dt or '', 'time': hhmm or '',
                    'teams': teams[:2], 'chans': chans})

    for m in _FTAO_RICH_RE.finditer(html):
        blob = m.group(1)
        dm = _FTAO_DT_RE.search(blob)
        tm = re.search(r'<time[^>]*>(\d{2}:\d{2})</time>', blob)
        add(dm.group(1) if dm else u'',
            (dm.group(2) if dm else (tm.group(1) if tm else u'')), blob)
    for m in _FTAO_SIMPLE_RE.finditer(html):
        add(today or u'', m.group(1), m.group(2))
    return out


def _footao_fetch(eq):
    """Page club (suivant la redirection JS) -> HTML, avec cache 1 h."""
    now = time.time()
    hit = _ftao_cache.get(eq)
    if hit and now - hit[0] < FOOTAO_TTL:
        return hit[1]
    try:
        from urllib.parse import quote as _q
    except ImportError:
        from urllib import quote as _q
    url = '%s/programmetv.php?eq=%s' % (FOOTAO_BASE, _q(eq, safe='%|&'))
    txt, code = _get(url, referer=FOOTAO_BASE + '/', timeout=12)
    if code == 200:
        red = re.search(r'window\.location\.replace\("([^"]+)"\)', txt)
        if red:
            target = red.group(1)
            if target.startswith('/'):
                target = FOOTAO_BASE + target
            txt, code = _get(target, referer=url, timeout=12)
    if code != 200:
        raise Exception('footao %s -> HTTP %s' % (eq, code))
    _ftao_cache[eq] = (now, txt)
    return txt


def _footao_chan_lookup(name):
    """'Canal+ Foot' -> ('Canal+ Foot France', 463) ; None si inconnu.

    Repli préfixe uniquement quand le nom footao est PLUS précis que la
    table (« beIN Sports 1 UHD » -> beIN 1) ; jamais l'inverse : un
    « DAZN » générique ne doit pas devenir « DAZN Ligue 1 ».
    """
    n = _norm_team(name)
    if n in FOOTAO_CHAN_ID:
        return FOOTAO_CHAN_ID[n]
    best = None
    for key in FOOTAO_CHAN_ID:
        if n.startswith(key + ' '):
            if not best or len(key) > len(best[0]):
                best = (key, FOOTAO_CHAN_ID[key])
    return best[1] if best else None


def enrich_servers(title, servers, today=None, fetch=None):
    """Ajoute les diffuseurs FR footao manquants, libellés « (footao) ».

    servers : [[nom, url_watch], …] de la grille DaddyLive. Pure sauf le
    fetch réseau (injectable pour les tests). Tolérant aux pannes : en cas
    de doute on renvoie `servers` inchangé.
    """
    teams = teams_from_title(title)
    if len(teams) != 2 or not servers:
        return servers
    tnorms = [_club_key(_norm_team(t)) for t in teams]
    # les DEUX équipes doivent exister chez footao : une seule correspondance
    # risque d'apparier un match d'un autre sport (hand « Paris SG » -> foot PSG)
    eq_list = []          # (eq, clé côté event)
    k2eq = {}             # nom footao normalisé (eq ou club) -> clé event
    for t in tnorms:
        for cname, ceq in FOOTAO_CLUBS.items():
            if _club_key(_norm_team(cname)) == t:
                if all(e != ceq for e, _t in eq_list):
                    eq_list.append((ceq, t))
                k2eq[_norm_team(ceq)] = t
                k2eq[_norm_team(cname)] = t
    if len(eq_list) != 2:
        return servers
    today = today or time.strftime('%Y-%m-%d')
    matches = []
    for eq, _t in eq_list:
        try:
            html = (fetch or _footao_fetch)(eq)
        except Exception:
            continue
        matches.extend(parse_footao_page(html, today=today))
    if not matches:
        return servers

    def canon(team_key):
        """nom footao -> clé côté event (« Paris PSG » -> « psg »)."""
        return k2eq.get(team_key, team_key)

    def score(m):
        mts = set(canon(_club_key(_norm_team(t))) for t in m['teams'])
        inter = len(mts.intersection(tnorms))
        # départage : même jour que la grille > futur > passé
        if m['dt'] == today:
            dist = 0
        elif m['dt'] and m['dt'] > today:
            dist = 1
        else:
            dist = 2
        return (inter, -dist)

    best = max(matches, key=score)
    if score(best)[0] < 2:   # les deux équipes du match footao doivent matcher
        return servers

    domain = DOMAINS[0]
    for _n, u in servers:
        mu = re.search(r'watch\.php\?id=', u or '')
        if u and mu:
            p = u.split('//', 1)
            if len(p) > 1:
                domain = 'https://' + p[1].split('/', 1)[0]
            break
    have_names = set(_norm_team(n) for n, _u in servers)
    have_ids = set()
    for _n, u in servers:
        mi = _ID_RE.search(u or '')
        if mi:
            have_ids.add(int(mi.group(1)))
    out = list(servers)
    for cname in best['chans']:
        hit = _footao_chan_lookup(cname)
        if not hit:
            continue
        dl_name, cid = hit
        if cid in have_ids or _norm_team(dl_name) in have_names:
            continue
        have_ids.add(cid)
        out.append([u'%s (footao)' % dl_name,
                    u'%s/watch.php?id=%d' % (domain, cid)])
    return out


# ---------------------------------------------------------------------------
# v3.6.3 — liens FR de la grille pour le menu « ÉVÉNEMENTS EN DIRECT » :
# fr_links_for(team1, team2) retrouve le match sur la grille DaddyLive du
# jour (cache 10 min), renvoie ses chaînes FR, enrichies par footao.
# Toute panne renvoie [] : le menu garde alors le comportement 3.6.0.
# ---------------------------------------------------------------------------

SCHED_TTL = 600              # grille du jour rechargée au plus 1x/10 min
_sched_cache = {'ts': 0.0, 'events': None}


def _schedule_fr_events():
    """Grille du jour (liste d'événements normalisés) avec cache 10 min."""
    now = time.time()
    if _sched_cache['events'] is not None and now - _sched_cache['ts'] < SCHED_TTL:
        return _sched_cache['events']
    domain = _working_domain()
    if not domain:
        raise Exception('DaddyLive injoignable (%s)' % ', '.join(DOMAINS))
    txt, code = _get(domain + '/index.php', referer=domain + '/')
    if code != 200:
        raise Exception('grille DaddyLive -> HTTP %s' % code)
    events = parse_schedule(txt, domain)
    _sched_cache['ts'] = now
    _sched_cache['events'] = events
    return events


def fr_links_for(team1, team2, start_ms=0, today=None):
    """Chaînes FR de la grille DaddyLive pour un match, enrichies footao.

    Renvoie [[nom, url_watch], …] (peut être vide). Ne lève jamais :
    en cas de problème on renvoie [] et le menu reste comme en 3.6.0.
    """
    try:
        t1, t2 = _club_key(_norm_team(team1 or u'')), _club_key(_norm_team(team2 or u''))
        if not t1 or not t2:
            return []
        events = _schedule_fr_events()

        def ev_score(e):
            eteams = [_club_key(_norm_team(x)) for x in teams_from_title(e['title'])]
            if not eteams:
                return (0, 0)
            inter = len(set(eteams).intersection((t1, t2)))
            dt = 0
            if start_ms and e.get('start_ms'):
                dt = -abs(e['start_ms'] - start_ms) // 3600000  # heures d'écart
            return (inter, dt)

        best, best_key = None, (0, 0)
        for e in events:
            key = ev_score(e)
            if key > best_key:
                best, best_key = e, key
        if not best or best_key[0] < 2:
            return []
        fr = [c for c in best['servers'] if is_fr_channel(c[0])]
        if not fr:
            return []
        fr = [list(c) for c in fr]
        # v3.6.2 — enrichissement footao : 2e diffuseur manquant
        try:
            fr = enrich_servers(best['title'], fr, today=today)
        except Exception:
            pass
        return fr
    except Exception:
        return []


# ---------------------------------------------------------------------------
# v3.7.3 — mini-serveur local « jeton renouvelé ».
#
# L'edge DaddyLive (const SRC/CHANNEL/EDGE des pages premium) n'ouvre ses
# playlists qu'avec un jeton /api/playlist-token : lié à l'IP du demandeur,
# exigeant les en-têtes Referer+Origin, et expirant après ~2 min (ttl=120).
# Le player web renouvelle son jeton en continu (à ttl/2) ; un lecteur Kodi
# rejoue la même URL et se fait couper au bout de 2 min — quand il obtient
# un jeton. On sert donc le manifest depuis 127.0.0.1 : à CHAQUE chargement
# de playlist (un lecteur recharge un live toutes les ~7 s) le serveur
# demande un jeton neuf avec les bons en-têtes puis renvoie le manifest ;
# les segments, ouverts, restent servis directement par leur CDN.
# Le processus d'addon héberge ce serveur : default.py appelle
# proxy_keepalive() pour le maintenir en vie pendant la lecture.
# ---------------------------------------------------------------------------
try:
    from urllib.parse import urljoin as _urljoin_compat  # Py3
except ImportError:
    from urlparse import urljoin as _urljoin_compat      # Py2

_PROXY_PORT = 45798            # port préféré ; occupé -> port éphémère
_proxy_state = {'srv': None, 'base': '', 'routes': {}, 'counter': 0}
_tok_cache = {}                # (edge, chan) -> (token, expires_at)
_edge_session = None           # session dédiée : jeton + manifest même flux

_EDGE_TTL_S = 120              # ttl annoncé par l'edge
_EDGE_RENEW_S = 55             # on re-minte après 55 s (le web : ttl/2)


def _edge_sess():
    """Session HTTP dédiée à l'edge (garde le même flux jeton+manifest)."""
    global _edge_session
    if _edge_session is None:
        import requests as _rq
        try:
            import requests.packages.urllib3 as _u3
            _u3.disable_warnings()
        except Exception:
            pass
        _edge_session = _rq.Session()
        _edge_session.headers.update({'User-Agent': UA})
    return _edge_session


def _mint_token(edge, chan, referer, origin):
    """Jeton /api/playlist-token (en cache _EDGE_RENEW_S, comme le web).

    v3.7.3 : l'edge refuse la demande (403 forbidden) sans l'en-tête
    Origin EN PLUS du Referer — c'est l'origine du bug « erreur » sur les
    liens ajoutés : l'ancien code n'envoyait que le Referer.
    """
    if not (edge and chan and origin):
        return ''
    key = (edge, chan)
    hit = _tok_cache.get(key)
    if hit and time.time() < hit[1]:
        return hit[0]
    hdr = {'Referer': referer, 'Origin': origin,
           'Accept': 'application/json, text/plain, */*'}
    for attempt in (1, 2):     # le player web retente après 1 s : pareil
        try:
            r = _edge_sess().get('%s/api/playlist-token?slug=%s' % (edge, chan),
                                 headers=hdr, timeout=10)
            token = (r.json() or {}).get('token') or ''
        except Exception:
            token = ''
        if token:
            _tok_cache[key] = (token, time.time() + _EDGE_RENEW_S)
            return token
        if attempt == 1:
            time.sleep(1.2)
    return ''


_CLEAN_QUERY_RE = re.compile(r'[?&](t|_)=[^&]*')


def _fetch_edge_playlist(edge_url, edge, chan, referer, origin):
    """Playlist edge avec jeton frais : nettoie t/_ puis ?t=…&_=ms."""
    token = _mint_token(edge, chan, referer, origin)
    if not token:
        return '', 0
    clean = _CLEAN_QUERY_RE.sub('', edge_url)
    sep = '&' if '?' in clean else '?'
    # Date.now() du lecteur web = MILLISECONDES ; jeton+manifest demandés
    # dos à dos sur la même session (même IP auprès de l'edge).
    url = '%s%st=%s&_=%s' % (clean, sep, token, int(time.time() * 1000))
    try:
        r = _edge_sess().get(url, headers={'Referer': referer,
                                           'Origin': origin}, timeout=12)
        return r.content.decode('utf-8', 'replace'), r.status_code
    except Exception:
        return '', 0


_ABS_URL_RE = re.compile(r'^https?://', re.IGNORECASE)
_URI_ATTR_RE = re.compile(r'(URI=")([^"]+)(")')


def _serve_manifest(body, base_url, creds):
    """Prépare le manifest pour Kodi : sous-playlists (*.m3u8) routées par
    le proxy local (jeton), URI="…" et lignes relatives absolutisées, les
    segments (ouverts) laissés directs vers leur CDN."""
    out = []
    for line in body.splitlines():
        ls = line.strip()
        if not ls:
            continue
        if ls.startswith('#'):
            def _fix_uri(m):
                u = m.group(2)
                if not _ABS_URL_RE.match(u):
                    u = _urljoin_compat(base_url, u)
                return m.group(1) + u + m.group(3)
            ls = _URI_ATTR_RE.sub(_fix_uri, ls)
            out.append(ls)
            continue
        if not _ABS_URL_RE.match(ls):
            ls = _urljoin_compat(base_url, ls)
        if ls.lower().split('?')[0].endswith('.m3u8'):
            ls = _register_route(ls, creds)
        out.append(ls)
    return u'\n'.join(out) + u'\n'


def start_proxy():
    """Mini-serveur HTTP 127.0.0.1 (1×/processus) -> base URL ou ''."""
    st = _proxy_state
    if st['srv'] is not None:
        return st['base']
    try:
        try:
            from http.server import BaseHTTPRequestHandler   # Py3
            from http.server import HTTPServer as _HTTPServer
            try:
                from http.server import ThreadingHTTPServer as _Srv
            except ImportError:
                import socketserver as _ss

                class _Srv(_ss.ThreadingMixIn, _HTTPServer):
                    daemon_threads = True
        except ImportError:                                   # Py2
            from BaseHTTPServer import BaseHTTPRequestHandler
            import SocketServer as _ss

            class _Srv(_ss.ThreadingMixIn, _ss.HTTPServer):
                daemon_threads = True

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):   # silencieux : kodi.log reste lisible
                pass

            def do_GET(self):
                route = st['routes'].get(self.path)
                code, ctype = 404, 'text/plain'
                body = u'route inconnue'
                if route:
                    txt, sc = _fetch_edge_playlist(
                        route['url'], route['edge'], route['chan'],
                        route['referer'], route['origin'])
                    if sc == 200 and '#EXTM3U' in txt:
                        code, ctype = 200, 'application/vnd.apple.mpegurl'
                        body = _serve_manifest(txt, route['url'], route)
                    elif sc:
                        code = 502
                        body = u'edge HTTP %s' % sc
                    else:
                        code = 502
                        body = u'jeton indisponible'
                data = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except Exception:
                    pass

        srv = None
        for port in (_PROXY_PORT, 0):
            try:
                srv = _Srv(('127.0.0.1', port), _Handler)
                break
            except Exception:
                continue
        if srv is None:
            return ''
        import threading
        th = threading.Thread(target=srv.serve_forever)
        th.daemon = True
        th.start()
        st['srv'] = srv
        st['base'] = 'http://127.0.0.1:%d' % srv.server_address[1]
        return st['base']
    except Exception:
        return ''


def _register_route(target_url, creds):
    """Enregistre une playlist edge -> chemin local /dl/<n>.m3u8."""
    st = _proxy_state
    if not st['base']:
        if not start_proxy():
            return ''
    route = dict(creds)
    route['url'] = target_url
    st['counter'] += 1
    path = '/dl/%d.m3u8' % st['counter']
    st['routes'][path] = route
    return st['base'] + path


def proxy_manifest_url(src, chan, edge, player_url, player_origin):
    """URL locale du manifest à jeton renouvelé ; '' si serveur impossible."""
    if not start_proxy():
        return ''
    return _register_route(src, {'edge': edge, 'chan': chan,
                                 'referer': player_url,
                                 'origin': player_origin})


def proxy_keepalive():
    """Garde le processus d'addon (et donc le serveur) vivant pendant la
    lecture. Kodi-safe : sort dès l'arrêt de la lecture ou de Kodi."""
    try:
        import xbmc
        monitor = xbmc.Monitor()
        player = xbmc.Player()
        deadline = time.time() + 45      # délai de démarrage de la lecture
        while not monitor.abortRequested():
            if player.isPlaying():
                while not monitor.abortRequested() and player.isPlaying():
                    monitor.waitForAbort(2)
                break
            if time.time() > deadline:
                break
            monitor.waitForAbort(1)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

_IFRAME_RE = re.compile(r'iframe src="([^"]+)" width')
_ID_RE = re.compile(r'[?&]id=(\d+)')


def _absolutise(src, base):
    if src.startswith('//'):
        return 'https:' + src
    if src.startswith('/'):
        return base + src
    return src


def resolve(server_url, prefer_fallback=False):
    """watch.php?id=N -> playable m3u8 (+ player page URL for Referer).

    Returns (m3u8_url, player_page_url) so the shared site_play() binds
    Referer/Origin to the player host -- what these CDNs check.
    prefer_fallback=True (v3.6.2 « lien (repli) ») saute la voie principale
    _econfig et passe directement par playlist-token puis la regex m3u8 :
    si l'un des deux résolveurs casse, l'autre reste disponible.
    """
    m = _ID_RE.search(server_url or '')
    if not m:
        raise Exception('URL DaddyLive illisible : %s' % server_url)
    cid = m.group(1)
    p = server_url.split('//', 1)
    domain = 'https://' + (p[1].split('/', 1)[0] if len(p) > 1 else p[0])
    watch_ref = '%s/watch.php?id=%s' % (domain, cid)

    # 1) watch page -> first same-origin stream page (stream/ or cast/)
    page = '%s/stream/stream-%s.php' % (domain, cid)
    try:
        txt, code = _get(watch_ref, referer=domain + '/')
        if code == 200:
            m1 = _IFRAME_RE.search(txt)
            if m1:
                src = _absolutise(m1.group(1), domain)
                if '//' + domain.split('//', 1)[1] in src:
                    page = src   # same-origin hop -> use it
    except Exception:
        pass

    # 2) stream page -> external player iframe (rotating hosts)
    txt2, code2 = _get(page, referer=watch_ref)
    if code2 != 200:
        # variant used by some mirrors
        alt = '%s/cast/stream-%s.php' % (domain, cid)
        txt2, code2 = _get(alt, referer=watch_ref)
        if code2 != 200:
            raise Exception('page stream %s -> HTTP %s' % (page, code2))
        page = alt
    m2 = _IFRAME_RE.search(txt2)
    if not m2:
        raise Exception('iframe joueur introuvable sur %s' % page)
    player_url = _absolutise(m2.group(1), domain)
    player_origin = player_url.split('/', 3)
    player_origin = '/'.join(player_origin[:3]) if len(player_origin) > 2 else player_url

    # 3) player page -> m3u8 (two 2026 layouts + last-resort regex)
    txt3, code3 = _get(player_url, referer=page)
    if code3 != 200:
        raise Exception('hébergeur joueur %s -> HTTP %s '
                        '(réessaie dans quelques secondes)'
                        % (player_origin, code3))

    # a) window._econfig (Plaza4756/dlhd-kodi layout) — voie principale ;
    #    sautée en mode repli pour tester d'abord playlist-token
    if not prefer_fallback:
        try:
            from resources.modules import econfig as _econfig
            if _econfig.find_econfig(txt3):
                fl, cfg = _econfig.extract_stream_from_html(txt3)
                if fl:
                    return fl, player_url
        except Exception:
            pass

    # b) const SRC / CHANNEL / EDGE -> playlist-token (layout 2026 : tous les
    #    canaux premium). v3.7.3 — l'edge exige DÉSORMAIS l'en-tête Origin
    #    sur /api/playlist-token : le player web est servi depuis un autre
    #    domaine, son fetch part donc toujours avec Origin+Referer ; sans
    #    Origin la demande de jeton prend 403 « forbidden » et l'ancien code
    #    retombait silencieusement sur l'URL m3u8 nue de la page, que l'edge
    #    refuse avec « token required » — d'où l'erreur systématique sur les
    #    liens ajoutés ([DaddyLive]/(footao)/(repli)) alors que les liens du
    #    guide (vertex/nexa/kora, autre CDN) marchaient. Le jeton est lié à
    #    l'IP du demandeur et vit ~2 min (ttl) : demandé à la dernière
    #    seconde, comme le player web qui le renouvelle lui-même.
    m_src = re.search(r'const\s+SRC\s*=\s*"((?:[^"\\]|\\.)*)"', txt3)
    m_chan = re.search(r'const\s+CHANNEL\s*=\s*"((?:[^"\\]|\\.)*)"', txt3)
    m_edge = re.search(r'const\s+EDGE\s*=\s*"((?:[^"\\]|\\.)*)"', txt3)
    if m_src and m_chan and m_edge:
        src = m_src.group(1).replace('\\/', '/')
        chan = m_chan.group(1).replace('\\/', '/')
        edge = m_edge.group(1).replace('\\/', '/')
        # v3.7.3 — jeton demandé AVEC Origin (sinon 403 « forbidden »),
        # puis manifest servi par le mini-serveur local qui redemande un
        # jeton neuf à chaque chargement de playlist : sans renouvellement
        # l'edge coupe la lecture après ~2 min (ttl du jeton).
        token = _mint_token(edge, chan, player_url, player_origin)
        if not token:
            # pas de jeton = l'URL SRC est morte de toute façon (« token
            # required ») : erreur claire plutôt qu'un lien qui échoue
            # en lecture chez l'utilisateur.
            raise Exception('jeton de flux refusé par %s — réessaie dans '
                            'quelques secondes' % edge)
        local = proxy_manifest_url(src, chan, edge, player_url, player_origin)
        if local:
            return local, player_url
        # serveur local impossible (rare) : URL directe à jeton, valable
        # ~2 min seulement — mieux que rien
        ts = int(time.time() * 1000)   # le lecteur web : Date.now() en ms
        return '%s?t=%s&_=%s' % (src, token, ts), player_url

    # c) dernière chance : m3u8 nu dans la page — seulement sur les pages
    #    SANS constellation EDGE (les pages premium à jeton ne tombent plus
    #    ici : leur m3u8 nu est refusé par l'edge, voir ci-dessus).
    for rx in (r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
               r'source:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
               r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)'):
        mm = re.search(rx, txt3)
        if mm:
            return mm.group(1), player_url

    raise Exception('flux introuvable sur %s (page %s octets)'
                    % (player_origin, len(txt3)))


# ---- self-check ----------------------------------------------------------

if __name__ == '__main__':
    # offline: saved fixtures from 2026-11-01
    html = open('/tmp/pk_home_1102.html', encoding='utf-8', errors='replace').read()
    date_str, tz_name = _day_info(html)
    print('day:', date_str, tz_name)
    evs = parse_schedule(html, 'https://thedaddy.pk')
    assert len(evs) > 600, len(evs)
    assert all(e['servers'] and e['title'] for e in evs)
    assert all(e['sport'] for e in evs), 'sport category missing'
    fr = [e for e in evs if any(is_fr_channel(c[0]) for c in e['servers'])]
    print('events:', len(evs), '| with FR channel:', len(fr))
    assert 35 < len(fr) < 70, len(fr)
    l1 = [e for e in fr if 'Ligue 1' in e['title']]
    assert l1, 'no Ligue 1 found'
    print('Ligue 1:', ', '.join(e['title'][:40] for e in l1[:3]))
    bein1 = [c for e in fr for c in e['servers'] if c[0] == 'beIN Sports 1 France']
    assert bein1 and bein1[0][1].endswith('watch.php?id=116')

    h247 = open('/tmp/dl_247.html', encoding='utf-8', errors='replace').read()
    chans = parse_channels_247(h247, 'https://thedaddy.pk')
    fr_ch = [n for n, u in chans if is_fr_channel(n)]
    fr_low = [n.lower() for n in fr_ch]
    print('24/7 channels:', len(chans), '| FR:', len(fr_ch))
    assert 600 < len(chans) < 1000 and 30 < len(fr_ch) < 60
    assert not any('arabic' in n for n in fr_low)
    assert 'bein sports 1 france' in fr_low and 'dazn ligue 1 france' in fr_low
    assert 'tennis+ 10' in fr_low and 'canal+ foot france' in fr_low

    # strictness spot checks
    assert is_fr_channel('beIN Sports MAX 4 France')
    assert is_fr_channel("Ligue1+ 4 France")
    assert is_fr_channel('Tennis+ 10')
    assert is_fr_channel('beIN Sports 1 France')  # casse du planning (title case)
    assert not is_fr_channel('Canal+ Sport SK')
    assert not is_fr_channel('DAZN2 Spain')
    assert not is_fr_channel('beIN Sports 1 Arabic')
    assert not is_fr_channel('beIN Sports 1 Malaysia')
    assert not is_fr_channel('Eurosport 1 Greece')
    assert not is_fr_channel('Canal 11 Portugal')
    print('OK')

    # v3.6.2 — enrichissement footao (fixture page club PSG si présente)
    import os
    if os.path.exists('/tmp/ftao_psg3.html'):
        fhtml = open('/tmp/ftao_psg3.html', encoding='utf-8',
                     errors='replace').read()
        fms = parse_footao_page(fhtml, today='2026-11-01')
        assert fms, 'aucun match parsé sur la page footao'
        assert any(u'Villarreal' in u' '.join(x['teams']) for x in fms)
        out = enrich_servers(
            u'⚽ UEFA Champions League : Villarreal vs PSG',
            [[u'Canal+ France', u'https://thedaddy.pk/watch.php?id=121']],
            today='2026-11-03', fetch=lambda eq: fhtml)
        assert any(u'(footao)' in n for n, u in out), 'enrichissement raté'
        # anti faux positif : le hand « Paris SG » ne doit pas matcher le foot
        srv_hb = [[u'beIN Sports 1 France', u'https://thedaddy.pk/watch.php?id=116']]
        assert enrich_servers(u'🤾 Starligue : Paris SG vs Montpellier',
                              srv_hb, today='2026-11-01',
                              fetch=lambda eq: fhtml) == srv_hb
        assert teams_from_title(u'⚽ France - Ligue 1 : Paris FC vs PSG') == \
            [u'Paris FC', u'PSG']
        assert teams_from_title(u'🎾 ATP Basel Tournament Final') == []
        print('footao enrich: OK (%d matchs page club)' % len(fms))
