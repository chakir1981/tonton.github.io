# -*- coding: utf-8 -*-
"""
365scores enrichment source for plugin.video.sporthdme (v3.7.2).

Kodi-agnostic on purpose: the parse/match functions are pure, the fetch
helpers are injectable, everything degrades to [] on any failure.

Why (séance 3 de screening, 2026-11-02):
  The DaddyLive grid misses matches broadcast on a French channel when
  DaddyLive itself does not list that channel for the event. 365scores'
  public web API answers, per country, which TV network carries each game:
      /web/games/current/?...&userCountryId=5&sports=<id>   (games of today)
      /web/game/?...&gameId=<id>&withExpanded=true          (-> tvNetworks)
  userCountryId=5 IS France (21 = Brazil: CazeTV/SporTV would recreate the
  "Denver vs Kansas with FR links" bug). Verified live: API is plain JSON,
  no bot protection, ~9 requests per day-scan.

Only channels from a strict whitelist are accepted (name -> DaddyLive
channel, stable ids verified on thedaddy.pk 2026-11-02). Anything unknown
(Disney+, ESPN, SporTV, Antel TV...) is discarded, so the FR-only menu can
never gain a foreign match. Matches gained this way keep the FR-only rules:
the injected entries are flagged 'v365' so _channels_have_french_trio() can
qualify them as verified French broadcasts (main + repli links).
"""
import json
import re
import time

BASE = 'https://webws.365scores.com/web/'
QS = ('appTypeId=5&locale=fr&timezoneName=Europe/Paris&userCountryId=5'
      '&showOdds=false')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# sport ids of the public API (1000 = "TV", not a sport)
SPORTS = ((1, 'Football'), (2, 'Basketball'), (3, 'Tennis'), (4, 'Hockey'),
          (5, 'Handball'), (6, 'A. Football'), (7, 'Baseball'),
          (8, 'Volleyball'), (9, 'Rugby'))

DAY_TTL = 1800.0     # day scan re-run at most every 30 min
GAME_TTL = 1800.0    # per-game detail cached 30 min
DEAD_TTL = 600.0     # after a network failure, stay quiet for 10 min
WINDOW_MS = 3.5 * 3600000   # max |start diff| between guide and 365scores
PAUSE = 0.4          # seconds between two API calls

# ---------------------------------------------------------------------------
# Whitelist: normalized 365scores channel name -> (DaddyLive name, watch id).
# DaddyLive ids verified on thedaddy.pk 24-7-channels.php + index.php on
# 2026-11-02. None = grid-only channel (Apple TV), resolved at runtime from
# the day schedule (its watch id is shared with other slots, never hardcoded).
# A 365scores channel absent from this table is IGNORED (no foreign links).
# ---------------------------------------------------------------------------
CHAN_365_DL = {
    'apple tv': ('Apple TV', None),
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
    'canal+': ('Canal+ France', 121),
    'canal+ sport': ('Canal+ Sport France', 122),
    'canal+ foot': ('Canal+ Foot France', 463),
    'canal+ formula 1': ('Canal+ Formula 1', 273),
    'canal+ f1': ('Canal+ Formula 1', 273),
    'canal+ motogp': ('CANAL+ MotoGP France', 271),
    'canal+ sport 360': ('Canal+ Sport 360', 464),
    'dazn ligue 1': ('DAZN Ligue 1 France', 960),
    'dazn ligue 1+': ('DAZN Ligue 1 France', 960),
    'ligue 1+': ('DAZN Ligue 1 France', 960),
    'ligue1+': ('DAZN Ligue 1 France', 960),
    'eurosport 1': ('Eurosport 1 France', 772),
    'eurosport 2': ('Eurosport 2 France', 773),
    'rmc sport 1': ('RMC Sport 1 France', 119),
    'rmc sport 2': ('RMC Sport 2 France', 120),
    'france 2': ('France 2', 950),
    'france 3': ('France 3', 951),
    'france 4': ('France 4', 952),
    'france 5': ('France 5', 953),
    'france tv': ('Sport en France', 965),
    'tf1': ('TF1 France', 469),
    'm6': ('M6 France', 470),
    'w9': ('W9 France', 959),
    'tmc': ('TMC France', 955),
    'l equipe': ("L'Équipe France", 645),
    'lequipe': ("L'Équipe France", 645),
    'sport en france': ('Sport en France', 965),
    'automoto': ('Automoto la chaîne', 961),
    '6ter': ('6ter France', 963),
    'arte': ('Arte France', 958),
    'c8': ('C8 France', 956),
}

_day_cache = {'ts': 0.0, 'games': None, 'grid_ts': 0.0, 'grid_ids': None}
_game_cache = {}            # gid -> (ts, channels or [])
_dead = {'ts': 0.0}


def _norm(s):
    """Normalize a team/channel name: lowercase, no accents/punctuation."""
    s = (s or u'').strip().lower()
    try:
        import unicodedata
        s = u''.join(c for c in unicodedata.normalize('NFKD', s)
                     if not unicodedata.combining(c))
    except Exception:
        pass
    s = s.replace(u'’', u"'")
    s = re.sub(r'[^a-z0-9+ ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


_CLUB_SUFFIX = re.compile(r'\s+(cf|fc|sc|afc|sk|if|bk)$')

# alias 365scores -> clé DaddyLive/footao connue (après _norm + suffix strip)
_TEAM_ALIAS = {
    'paris saint germain': 'psg',
    'psg': 'psg',
    'paris fc': 'paris fc',
    'olympique de marseille': 'marseille',
    'olympique marseille': 'marseille',
    'marseille': 'marseille',
    'om': 'marseille',
    'olympique lyonnais': 'lyon',
    'lyon': 'lyon',
    'ol': 'lyon',
    'as monaco': 'monaco',
    'monaco': 'monaco',
    'losc lille': 'lille',
    'lille': 'lille',
    'inter miami': 'inter miami',
    'los angeles fc': 'los angeles fc',
    'la fc': 'los angeles fc',
    'new york red bulls': 'new york red bulls',
    'new york city fc': 'new york city',
    'seattle sounders fc': 'seattle sounders',
    'seattle sounders': 'seattle sounders',
    'atlanta united fc': 'atlanta united',
    'atlanta united': 'atlanta united',
    'nashville sc': 'nashville',
    'nashville': 'nashville',
    'portland timbers': 'portland timbers',
    'houston dynamo': 'houston dynamo',
    'houston dynamo fc': 'houston dynamo',
    'minnesota united fc': 'minnesota united',
    'minnesota united': 'minnesota united',
}


def _team_key(name):
    """Normalized team key with the generic club suffixes removed."""
    t = _norm(name)
    t = _CLUB_SUFFIX.sub(u'', t)
    return _TEAM_ALIAS.get(t, t)


def _chan_key(name):
    """Normalized 365scores channel name for the whitelist lookup."""
    t = _norm(name)
    t = t.replace(u'caze tv', u'cazetv')
    return t


def _iso_ms(s):
    """'2026-11-01T20:45:00+01:00' -> UTC epoch ms (0 si indatable)."""
    if not s:
        return 0
    try:
        from dateutil.parser import parse as _dparse
        dt = _dparse(s)
        import calendar
        return int(calendar.timegm(dt.utctimetuple())) * 1000
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Network helpers (requests like site_daddylive, JSON flavour)
# ---------------------------------------------------------------------------

def _api(url, timeout=12):
    """GET a 365scores JSON endpoint -> (dict, status)."""
    import requests
    hdr = {'User-Agent': UA,
           'Accept': 'application/json',
           'Origin': 'https://www.365scores.com',
           'Referer': 'https://www.365scores.com/',
           'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8'}
    try:
        r = requests.get(url, headers=hdr, timeout=timeout)
    except Exception:
        try:
            r = requests.get(url, headers=hdr, timeout=timeout, verify=False)
        except Exception:
            return None, 0
    if r.status_code != 200:
        return None, r.status_code
    try:
        return json.loads(r.content.decode('utf-8', 'replace')), 200
    except Exception:
        return None, 200


def day_games(fetch=None):
    """Games of the day carrying FR TV data: [{'gid','teams','start_ms',
    'sport','comp'}]. Cached 30 min; silent [] on failure."""
    now = time.time()
    if _day_cache['games'] is not None and now - _day_cache['ts'] < DAY_TTL:
        return _day_cache['games']
    if now - _dead['ts'] < DEAD_TTL:
        return _day_cache['games'] or []
    get = fetch or _api
    games = []
    ok_calls = 0
    for sid, _name in SPORTS:
        try:
            data, code = get('%sgames/current/?%s&sports=%d' % (BASE, QS, sid))
        except Exception:
            data, code = None, 0
        if code == 200 and data:
            ok_calls += 1
            for g in (data.get('games') or []):
                if not g.get('hasTVNetworks'):
                    continue
                home = (g.get('homeCompetitor') or {}).get('name') or u''
                away = (g.get('awayCompetitor') or {}).get('name') or u''
                if not home or not away:
                    continue
                games.append({
                    'gid': g.get('id'),
                    'teams': [home, away],
                    'start_ms': _iso_ms(g.get('startTime')),
                    'sport': _name,
                    'comp': g.get('competitionDisplayName') or u'',
                })
        time.sleep(PAUSE)
    if not ok_calls:
        _dead['ts'] = now          # service down: back off, retry in 10 min
        _day_cache['games'] = []
    else:
        _day_cache['ts'] = now
        _day_cache['games'] = games
    return games


def _grid_channel_ids():
    """Exact channel name -> watch id, from the DaddyLive day schedule
    (shared cache with site_daddylive). Used for grid-only channels
    (Apple TV). Returns {} when DaddyLive is unreachable."""
    if _day_cache['grid_ids'] is not None \
            and time.time() - _day_cache['grid_ts'] < DAY_TTL:
        return _day_cache['grid_ids']
    ids = {}
    try:
        from resources.modules import site_daddylive as _dl
        for e in _dl._schedule_fr_events():
            for name, url in (e.get('servers') or []):
                m = re.search(r'id=(\d+)', url or '')
                if m and name not in ids:
                    ids[name] = m.group(1)
    except Exception:
        ids = {}
    _day_cache['grid_ids'] = ids
    _day_cache['grid_ts'] = time.time()
    return ids


def game_channels(gid, fetch=None):
    """FR-whitelist channels of one game: [(DaddyLive name, watch_id), …].
    Cached 30 min per game. [] on any failure."""
    now = time.time()
    hit = _game_cache.get(gid)
    if hit and now - hit[0] < GAME_TTL:
        return hit[1]
    get = fetch or _api
    try:
        data, code = get('%sgame/?%s&gameId=%s&withExpanded=true'
                         % (BASE, QS, gid))
    except Exception:
        data, code = None, 0
    if code != 200 or not data:
        _game_cache[gid] = (now, [])
        return []
    nets = (data.get('game') or {}).get('tvNetworks') or []
    out, seen = [], set()
    grid = None
    for n in nets:
        dl_name, static_id = CHAN_365_DL.get(_chan_key(n.get('name')), (None, None))
        if not dl_name:
            continue     # hors liste blanche -> ignoré (pas de lien étranger)
        if dl_name in seen:
            continue
        seen.add(dl_name)
        cid = static_id
        if cid is None:
            if grid is None:
                grid = _grid_channel_ids()
            cid = grid.get(dl_name)
            if not cid:
                continue   # Apple TV absent de la grille du jour -> rien
        out.append((dl_name, int(cid)))
    _game_cache[gid] = (now, out)
    return out


def fr_links_for(team1, team2, start_ms=0, fetch=None, today=None):
    """watch.php links for French channels verified by 365scores for this
    match. [[name, url], …], possibly empty, never raises.

    A game qualifies only when BOTH teams match (normalized) and the start
    times sit in the same +/- 3.5 h window (no cross-matching a team that
    plays twice the same day)."""
    try:
        t1, t2 = _team_key(team1 or u''), _team_key(team2 or u'')
        if not t1 or not t2 or t1 == t2:
            return []
        now = time.time()
        if now - _dead['ts'] < DEAD_TTL:
            return []
        best, best_key = None, 0
        for g in day_games(fetch=fetch):
            gts = [_team_key(x) for x in g['teams']]
            inter = len(set(gts).intersection((t1, t2)))
            if inter < 2:
                continue
            dt = abs((g['start_ms'] or 0) - (start_ms or 0))
            if start_ms and g['start_ms'] and dt > WINDOW_MS:
                continue
            if best is None or inter > best_key:
                best, best_key = g, inter
        if not best or not best.get('gid'):
            return []
        chans = game_channels(best['gid'], fetch=fetch)
        dom = u'https://thedaddy.pk'
        try:
            from resources.modules import site_daddylive as _dl
            dom = _dl.DOMAINS[0]
        except Exception:
            pass
        return [[name, u'%s/watch.php?id=%d' % (dom, cid)]
                for name, cid in chans]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Self-test (run outside Kodi: python3 site_365.py)
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # pure parts
    assert _team_key(u'Inter Miami CF') == u'inter miami'
    assert _team_key(u'New England Revolution') == u'new england revolution'
    assert _team_key(u'Paris Saint-Germain') == u'psg'
    assert _team_key(u'Olympique de Marseille') == u'marseille'
    assert _chan_key(u'Apple TV ') == u'apple tv'
    assert _chan_key(u'CazéTV') == u'cazetv'
    assert _iso_ms(u'2026-11-01T20:45:00+01:00') > 0
    assert CHAN_365_DL[_chan_key(u'beIN Sports 1')][1] == 116
    assert CHAN_365_DL[_chan_key(u'Apple TV')][1] is None
    # foreign channels must NOT resolve
    for bad in (u'Disney+', u'SporTV 2', u'Antel TV Internacional', u'ESPN 2',
                u'GE TV', u'Premiere', u'Fanatiz', u'CazéTV', u'DAZN'):
        assert _chan_key(bad) not in CHAN_365_DL, bad
    # offline match: fetch stub -> []
    assert fr_links_for(u'X', u'Y', fetch=lambda url: (None, 0)) == []
    print('site_365 self-test: OK (%d chaines en liste blanche)'
          % len(CHAN_365_DL))
