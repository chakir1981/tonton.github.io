# -*- coding: utf-8 -*-
"""Programme Daddylive — la vraie chaîne de chaque match du jour.

footao.tv dit quelle chaîne FRANCE diffuse un match ; Daddylive sait quelle
chaîne de SA grille porte réellement chaque événement du jour, avec
l'identifiant premium à résoudre en flux (server_lookup.php). Ce module lit
le programme du jour (page d'accueil du site, tenue à jour en continu) et
retrouve l'événement correspondant à un match :

    https://{domaine}/   ->  HTML : .schedule__event (titre, heure) puis
                             .schedule__channels (nom de chaîne + id premium)

Exemples (grille réelle) :
    Lazio vs Cagliari (Serie A)  -> DAZN ZONA 1 IT (premium877), DAZN IT (premium109)
    Marseille vs Toulouse (L1)   -> Ligue1+ France (premium960)

On propose ainsi les BONS liens pour les matchs que footao place sur DAZN,
Ligue 1+… au lieu des liens beIN génériques qui ne diffusent pas ces
matchs. Le programme couvre le jour même (heure UK) ; sans événement
retrouvé, l'add-on retombe sur la chaîne footao ou sur un message honnête.

Le domaine du programme est modifiable dans les réglages (« Programme
Daddylive : domaines ») : les domaines de l'écosystème tournent et certains
sont protégés par un challenge navigateur — celui-ci sert la page
d'accueil sans challenge. Module volontairement sans dépendance Kodi afin
de rester testable hors de Kodi.
"""

import json
import re
import time
import unicodedata

# Domaine du programme du jour (page d'accueil, sans challenge).
DEFAULT_DOMAINS = 'thedaddy.pk'

CACHE_AGE = 1800          # 30 min : la grille vit avec les événements
THRESHOLD = 0.7           # score minimal pour retenir un rapprochement

# html -> événements ---------------------------------------------------------

_DAY_TITLE = re.compile(r'class="schedule__dayTitle">([^<]+)<')
_EVENT = re.compile(
    r'data-title="[^"]*"[^>]*>\s*'
    r'<span class="schedule__time" data-time="(\d{2}:\d{2})">[^<]*</span>\s*'
    r'<span class="schedule__eventTitle">(.*?)</span>',
    re.DOTALL)
_CHANNEL = re.compile(r'href="/watch\.php\?id=(\d+)"[^>]*?title="([^"]*)"')

_ENTITIES = ((u'&amp;', u'&'), (u'&#039;', u"'"), (u'&apos;', u"'"), (u'&quot;', u'"'),
             (u'&nbsp;', u' '), (u'&lt;', u'<'), (u'&gt;', u'>'))

# Canaux « génériques » (re-stream ponctuel du site) passés en fin de liste.
_GENERIC = re.compile(r'event|stream\b|backup|alt\b|\bsd\b|\bhd\b', re.I)


def _unescape(s):
    for ent, ch in _ENTITIES:
        s = s.replace(ent, ch)
    return s


def _clean(text):
    """Texte d'un événement sans balise ni entité HTML."""
    return _unescape(re.sub(r'<[^>]+>', u'', text or u'')).strip()


def _teams(title):
    """(équipe1, équipe2) depuis un titre « Compét : T1 vs T2 » (ou '')."""
    t = _clean(title)
    head = t.split(u' : ')[-1].split(u': ')[-1] if u' vs ' in t else t
    if u' vs ' in head:
        t1, t2 = head.split(u' vs ', 1)
        return t1.strip(), t2.strip()
    return t, u''


def parse(html):
    """Page d'accueil -> liste d'événements du jour.

    Chaque événement : {'day', 'time', 'title', 't1', 't2', 'channels'}
    avec channels = [{'name', 'id'}] dans l'ordre de la grille (le premier
    est la chaîne principale). Seule la section du jour courant (heure UK)
    est conservée — les pages contiennent parfois des sections périmées.
    """
    if not html:
        return []
    # Sections par jour : le titre de jour ouvre la section suivante.
    marks = [(m.start(), m.group(1)) for m in _DAY_TITLE.finditer(html)]
    sections = []
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(html)
        sections.append((title, html[pos:end]))
    if not sections:
        sections = [(u'', html)]
    wanted = _pick_day(sections)
    if wanted is None:
        return []
    title, seg = wanted
    day = _day_key_from_title(title) or time.strftime('%Y-%m-%d', time.gmtime())
    events = []
    for m in _EVENT.finditer(seg):
        nxt = seg.find('class="schedule__eventHeader"', m.end())
        sub = seg[m.end():nxt if nxt > 0 else len(seg)]
        chans = [{'name': _unescape(n).strip(), 'id': int(i)}
                 for i, n in _CHANNEL.findall(sub) if n and int(i) > 0]
        t1, t2 = _teams(m.group(2))
        events.append({
            'day': day,
            'time': m.group(1),
            'title': _clean(m.group(2)),
            't1': t1,
            't2': t2,
            'channels': chans,
        })
    return events


# choix du jour ---------------------------------------------------------------

_DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _ordinal(n):
    if 4 <= n % 100 <= 20:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def _today_uk_titles():
    """Titres plausibles du jour courant (heure UK), ex. 'Sunday 01st Nov 2026'."""
    gm = time.gmtime()
    d, y = gm.tm_mday, gm.tm_year
    day, mon = _DAYS[gm.tm_wday], _MONTHS[gm.tm_mon - 1]
    return (u'{0} {1:02d}{2} {3} {4}'.format(day, d, _ordinal(d), mon, y),
            u'{0} {1} {2} {3}'.format(day, d, mon, y))


def _day_key_from_title(title):
    """'Sunday 01st Nov 2026 - Schedule Time UK GMT' -> '2026-11-01' (ou '')."""
    t = _u(title or u'')
    m = re.search(r'(\d{1,2})\w{2}\s+(' + u'|'.join(_MONTHS) + r')\s+(\d{4})', t)
    if not m:
        return u''
    try:
        mon = _MONTHS.index(m.group(2)) + 1
        return u'{0:04d}-{1:02d}-{2:02d}'.format(int(m.group(3)), mon, int(m.group(1)))
    except Exception:
        return u''


def _pick_day(sections):
    """La section du jour courant (heure UK), sinon la plus fournie."""
    keys = [_day_key_from_title(t) for t, _s in sections]
    today = time.strftime('%Y-%m-%d', time.gmtime())
    for k, sec in zip(keys, sections):
        if k == today:
            return sec
    titles = _today_uk_titles()
    for t, sec in sections:
        for want in titles:
            if want in _u(t):
                return sec
    if not sections:
        return None
    return max(sections, key=lambda s: s[1].count('schedule__eventHeader'))


def _u(text):
    if isinstance(text, bytes):
        return text.decode('utf-8', 'replace')
    return text


# chaînes d'un événement ------------------------------------------------------

def channel_links(event, limit=5):
    """Canaux diffusés (nom, id premium) dans l'ordre de la grille.

    Doublons fusionnés (l'ID le plus petit est la chaîne principale), les
    re-streams génériques (« Event SD Stream »…) passent en dernier.
    """
    order, ids = [], {}
    for c in (event or {}).get('channels') or []:
        name = _u(c.get('name') or u'').strip()
        try:
            pid = int(c.get('id') or 0)
        except Exception:
            pid = 0
        if not name or pid <= 0:
            continue
        key = re.sub(r'[^a-z0-9]+', u'', name.lower())
        if key in ids:
            if pid < ids[key][1]:
                ids[key] = (name, pid)
        else:
            order.append(key)
            ids[key] = (name, pid)
    main = [ids[k] for k in order if not _GENERIC.search(ids[k][0] or u'')]
    extra = [ids[k] for k in order if _GENERIC.search(ids[k][0] or u'')]
    return (main + extra)[:max(0, int(limit))]


# rapprochement des noms d'équipes (même logique que footao.py) ---------------

_DROP = {'fc', 'cf', 'sc', 'ac', 'ud', 'cd', 'cp', 'bk', 'if', 'aif', 'sk', 'club',
         'foot', 'calcio', 'women', 'fem', 'feminin', 'kf', 'fk', 'as', 'ss', 'ssc',
         'afc', 'cfc', 'racing', 'atletico', 'sporting', 'de', 'the', 'w'}

_TOK_CACHE = {}


def _tokens(s):
    ts = _TOK_CACHE.get(s)
    if ts is not None:
        return ts
    s = unicodedata.normalize('NFKD', _u(s or u''))
    s = u''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r'[^a-z0-9 ]', u' ', s)
    ts = [t for t in s.split() if t not in _DROP]
    _TOK_CACHE[s] = ts
    return ts


def _tsim(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    best = 0.0
    for x in ta:
        for y in tb:
            if x == y:
                best = max(best, 1.0)
            elif (len(x) >= 3 and y.startswith(x)) or (len(y) >= 3 and x.startswith(y)):
                best = max(best, 0.9)
            else:
                p = 0
                while p < min(len(x), len(y)) and x[p] == y[p]:
                    p += 1
                if p >= 5:
                    best = max(best, 0.85)
    return best


def score(event, team1, team2):
    """Score du rapprochement, orientation cohérente (domicile/extérieur)."""
    if not team1 or not team2 or not (event or {}).get('t1'):
        return 0.0
    direct = (_tsim(event['t1'], team1) + _tsim(event['t2'], team2)) / 2
    swap = (_tsim(event['t1'], team2) + _tsim(event['t2'], team1)) / 2
    return max(direct, swap)


def find(events, team1, team2):
    """Événement du jour le plus proche de (team1, team2), ou None."""
    team1, team2 = _u(team1 or u''), _u(team2 or u'')
    if not team1 or not team2 or not events:
        return None
    best, bf = 0.0, None
    for ev in events:
        s = score(ev, team1, team2)
        if s > best:
            best, bf = s, ev
    if bf is not None and best >= THRESHOLD:
        return bf
    return None


# chargement (avec cache disque) ----------------------------------------------

def _ensure_text(data):
    if data is None:
        return u''
    if isinstance(data, bytes):
        return data.decode('utf-8', 'replace')
    return data


def load(client_mod, cache_path='', domains=None):
    """Grille du jour Daddylive. Cache disque ~30 min, valable le même jour.

    client_mod : module client de l'add-on (méthode request) — injection
    pour rester testable. cache_path vide = pas de cache disque.
    domains : liste de domaines à essayer (défaut : DEFAULT_DOMAINS).
    """
    now = time.time()
    today = time.strftime('%Y-%m-%d', time.gmtime())
    doc = None
    if cache_path:
        try:
            fh = open(cache_path, 'rb')
            doc = json.loads(fh.read().decode('utf-8'))
            fh.close()
        except Exception:
            doc = None
    if doc and doc.get('day') == today and doc.get('events') \
            and now - float(doc.get('ts', 0)) < CACHE_AGE:
        return doc['events']
    events = []
    for dom in domains or [d for d in DEFAULT_DOMAINS.split(',') if d.strip()]:
        dom = _ensure_text(dom).strip().replace('https://', '').replace('http://', '').strip('/')
        if not dom:
            continue
        try:
            events = parse(_ensure_text(client_mod.request('https://' + dom + '/')))
        except Exception:
            events = []
        if events:
            break
    if not events and doc and doc.get('day') == today and doc.get('events'):
        # site momentanément indisponible : resservir la grille déjà vue
        # aujourd'hui (une grille d'un autre jour induirait des erreurs)
        return doc['events']
    if events and cache_path:
        try:
            fh = open(cache_path, 'wb')
            fh.write(json.dumps({'ts': now, 'day': today, 'events': events},
                                ensure_ascii=False).encode('utf-8'))
            fh.close()
        except Exception:
            pass
    return events
