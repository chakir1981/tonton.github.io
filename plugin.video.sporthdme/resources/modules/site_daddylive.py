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
_domain = None          # working domain, remembered for the session

MAX_EVENT_AGE_H = 6     # hide events that started more than 6h ago
LIVE_WINDOW_MIN = 240   # lime "live" colour window

# ---------------------------------------------------------------------------
# Filtre « chaîne française » STRICT — les variantes étrangères du même
# groupe (Canal+ Sport SK/CZ/Poland, DAZN Spain/DE/IT, beIN Arabic/Malaysia,
# Eurosport Greece/NL...) ne comptent PAS comme françaises.
# ---------------------------------------------------------------------------
_ENDS_FR = re.compile(r'France$', re.IGNORECASE)
_BRANDS_FR = re.compile(
    r'^(Canal\+ ?(France|Sport( ?\d+| ?360| ?Afrique)*|Foot|Premium|D[ée]cal[ée]|Cinema|Series|'
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

def _get(url, referer=None, timeout=20):
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
    try:
        r = requests.get(url, headers=hdr, timeout=timeout)
    except Exception:
        r = requests.get(url, headers=hdr, timeout=timeout, verify=False)
    return r.content.decode('utf-8', 'replace'), r.status_code


def _working_domain():
    """Primary domain first, then seeds; remember what worked."""
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


def resolve(server_url):
    """watch.php?id=N -> playable m3u8 (+ player page URL for Referer).

    Returns (m3u8_url, player_page_url) so the shared site_play() binds
    Referer/Origin to the player host -- what these CDNs check.
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

    # a) window._econfig (Plaza4756/dlhd-kodi layout)
    try:
        from resources.modules import econfig as _econfig
        if _econfig.find_econfig(txt3):
            fl, cfg = _econfig.extract_stream_from_html(txt3)
            if fl:
                return fl, player_url
    except Exception:
        pass

    # b) const SRC / CHANNEL / EDGE -> playlist-token (StepDaddyLiveHD layout)
    try:
        src = re.search(r'const\s+SRC\s*=\s*"((?:[^"\\]|\\.)*)"', txt3).group(1)
        chan = re.search(r'const\s+CHANNEL\s*=\s*"((?:[^"\\]|\\.)*)"', txt3).group(1)
        edge = re.search(r'const\s+EDGE\s*=\s*"((?:[^"\\]|\\.)*)"', txt3).group(1)
        src = src.replace('\\/', '/')
        chan = chan.replace('\\/', '/')
        edge = edge.replace('\\/', '/')
        tok_url = '%s/api/playlist-token?slug=%s' % (edge, chan)
        tok_txt, tok_code = _get(tok_url, referer=player_url)
        tok = json.loads(tok_txt)
        token = tok.get('token') or ''
        if token:
            ts = int(time.time())
            return '%s?t=%s&_=%s' % (src, token, ts), player_url
    except Exception:
        pass

    # c) last resort: bare file:/source: m3u8 in the player page
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
