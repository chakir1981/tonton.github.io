# -*- coding: utf-8 -*-
"""Programme TV sport de Téléobs (programme-tv.nouvelobs.com/sport).

Complète footao.tv (football uniquement) pour les autres sports : NBA, NFL,
hockey, rugby, tennis, handball, moto, snooker… Pour un match sans source
française, ce module retrouve le match dans le programme TV sport du jour
et indique LA chaîne qui le diffuse vraiment (beIN Sports 1-3, Canal+ /
Foot / Sport / Sport 360, Eurosport 1-2, L'Équipe, TF1, M6…), ensuite
résolue en flux par dlch.py. Les chaînes sans flux connu (TFX, TMC,
France TV…) servent quand même à prouver la diffusion française.

Page utilisée (HTML simple, sans anti-bot, encodage windows-1252) :
    https://programme-tv.nouvelobs.com/sport   aujourd'hui (tous sports)

Ce module est volontairement sans dépendance Kodi afin de rester testable
hors de Kodi.
"""

import json
import re
import time
import unicodedata

BASE = 'https://programme-tv.nouvelobs.com/sport'
CACHE_AGE = 3600          # 1 h : le programme du jour ne bouge presque pas
THRESHOLD = 0.7           # score minimal pour retenir un rapprochement

# html -> lignes -------------------------------------------------------------

_TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_SPORT = re.compile(r'class="noir b">([^<]+)</a>')
_TIME_CHAN = re.compile(r'à\s*(\d{1,2}h\d{2})\s*sur\s+([^<]+?)(?:</span>|<br|<a )')
_CHAN_ALT = re.compile(r'alt="Programme ([^"]+)"')
_TITLE = re.compile(r'<span class="bleu">([^<]+)</span><br/>?([^<]*)')
_DAY = re.compile(r'(\d{1,2})\s+'
                  r'(janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|'
                  r'septembre|octobre|novembre|d[eé]cembre)\s+(\d{4})', re.I)
_MONTHS = {'janvier': 1, 'fevrier': 2, u'février': 2, 'mars': 3, 'avril': 4,
           'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, u'août': 8,
           'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12,
           u'décembre': 12}
_ENTITIES = ((u'&amp;', u'&'), (u'&#039;', u"'"), (u'&apos;', u"'"),
             (u'&nbsp;', u' '), (u'&quot;', u'"'), (u'&lt;', u'<'), (u'&gt;', u'>'))


def _unescape(s):
    for ent, ch in _ENTITIES:
        s = s.replace(ent, ch)
    return s


def _page_day(html):
    """Date affichée par la page (AAAA-MM-JJ), sinon aujourd'hui."""
    try:
        m = _DAY.search(html or u'')
        if m:
            mo = _MONTHS.get(m.group(2).lower())
            if mo:
                return u'{0}-{1:02d}-{2:02d}'.format(m.group(3), mo, int(m.group(1)))
    except Exception:
        pass
    return time.strftime('%Y-%m-%d')


def parse(html):
    """Analyse la page -> liste d'événements {day, hour, sport, t1, t2, comp, channel}."""
    if not html:
        return []
    if isinstance(html, bytes):
        html = _ensure_text(html)
    day = _page_day(html)
    rows = []
    for tr in _TR.findall(html):
        if 'sur ' not in tr and 'Programme ' not in tr:
            continue
        mt, mc, ma, ms = _TITLE.search(tr), _TIME_CHAN.search(tr), _CHAN_ALT.search(tr), _SPORT.search(tr)
        if not (mt and (mc or ma)):
            continue
        title = _unescape(mt.group(1)).strip()
        if u' - ' in title:
            t1, t2 = title.split(u' - ', 1)
        else:
            t1, t2 = title, u''
        rows.append({
            'day': day,
            'hour': mc.group(1) if mc else u'',
            'sport': _unescape(ms.group(1)).strip() if ms else u'',
            't1': t1.strip(),
            't2': t2.strip(),
            'comp': _unescape(mt.group(2)).strip(),
            'channel': _unescape(mc.group(2) if mc else ma.group(1)).strip(),
        })
    return rows


# rapprochement des noms d'équipes (même méthode que footao.tv) --------------

_DROP = {'fc', 'cf', 'sc', 'ac', 'ud', 'cd', 'cp', 'bk', 'if', 'aif', 'sk', 'club',
         'foot', 'calcio', 'women', 'fem', 'feminin', 'kf', 'fk', 'as', 'ss', 'ssc',
         'afc', 'cfc', 'racing', 'atletico', 'sporting', 'de', 'the'}

# noms internationaux (tvsport.guide) -> noms francisés (programme TV FR)
_ALIAS = {u'napoli': u'naples', u'roma': u'rome', u'psg': u'paris',
          u'm\xfcnchen': u'munich', u'munchen': u'munich', u'koln': u'cologne',
          u'koeln': u'cologne', u'wolverhampton': u'wolves',
          u'internazionale': u'inter', u'istasbul': u'istanbul'}


def _tokens(s):
    s = unicodedata.normalize('NFKD', s or u'')
    s = u''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r'[^a-z0-9 ]', u' ', s)
    return [t for t in s.split() if t not in _DROP]


def _tsim(a, b):
    """Similarité d'un token : égal, préfixe, préfixe commun long."""
    ta = [_ALIAS.get(t, t) for t in _tokens(a)]
    tb = [_ALIAS.get(t, t) for t in _tokens(b)]
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


def score(row, team1, team2):
    """Score du rapprochement, orientation cohérente (domicile/extérieur)."""
    if not team1 or not team2:
        return 0.0
    direct = (_tsim(row['t1'], team1) + _tsim(row['t2'], team2)) / 2
    swap = (_tsim(row['t1'], team2) + _tsim(row['t2'], team1)) / 2
    return max(direct, swap)


def find(rows, team1, team2, day=''):
    """Meilleur événement du programme pour (team1, team2), même jour d'abord."""
    team1, team2 = team1 or u'', team2 or u''
    if not team1 or not team2 or not rows:
        return None
    if day:
        for r in rows:
            if r['day'] == day and score(r, team1, team2) >= THRESHOLD:
                return r
    best, bf = 0.0, None
    for r in rows:
        s = score(r, team1, team2)
        if s > best:
            best, bf = s, r
    if bf is not None and best >= THRESHOLD:
        return bf
    return None


# chaîne Téléobs -> chaîne résolvable par dlch -------------------------------

# Libellés du programme (« BEIN SPORTS 1 », « CANAL + FOOT », alt du logo…).
# Les chaînes absentes d'ici (TFX, TMC, France TV, DAZN…) n'ont pas de flux
# premium connu : channel_of renvoie ''.
_CANON = (
    (r'^bein sports max (\d+)$', lambda m: u'beIN Sports MAX {0}'.format(m.group(1))),
    (r'^bein sports (\d+)$', lambda m: u'beIN SPORTS {0}'.format(m.group(1))),
    (r'^canal \+ sport 360$', u'Canal+ Sport360'),
    (r'^canal \+ sport$', u'Canal+ Sport'),
    (r'^canal \+ foot$', u'Canal+ Foot'),
    (r'^canal \+ formula 1$', u'Canal+ Formula 1'),
    (r'^canal \+ motogp$', u'CANAL+ MotoGP'),
    (r'^canal \+$', u'Canal+'),
    (r"^l['\u2019] ?equipe$", u"L'Équipe"),
    (r'^rmc sport ([12])$', lambda m: u'RMC Sport {0}'.format(m.group(1))),
    (r'^eurosport 2$', u'Eurosport 2'),
    (r'^eurosport$', u'Eurosport 1'),
    (r'^(tf1|m6)$', lambda m: m.group(1).upper()),
)


def channel_of(row):
    """Clé de dlch.CHANNELS pour la chaîne de l'événement, ou '' si non résolvable."""
    lbl = (row.get('channel') or u'').strip().lower().replace(u'\u2019', u"'")
    for rx, out in _CANON:
        m = re.match(rx, lbl, re.I)
        if m:
            return out(m) if callable(out) else out
    return u''


# chargement (avec cache disque) ---------------------------------------------

def _ensure_text(data):
    """Décodage robuste : utf-8, puis cp1252 (Téléobs est en windows-1252)."""
    if data is None:
        return u''
    if isinstance(data, bytes):
        for enc in ('utf-8', 'cp1252', 'latin-1'):
            try:
                return data.decode(enc)
            except Exception:
                continue
        return data.decode('utf-8', 'replace')
    return data


def load(client_mod, cache_path='', force=False):
    """Programme TV sport du jour (1 requête). Cache disque JSON ~1 h.

    client_mod : module client de l'add-on (méthode request) — injection
    pour rester testable. cache_path vide = pas de cache disque.
    """
    now = time.time()
    if cache_path and not force:
        try:
            fh = open(cache_path, 'rb')
            doc = json.loads(fh.read().decode('utf-8'))
            fh.close()
            if now - float(doc.get('ts', 0)) < CACHE_AGE and doc.get('rows'):
                return doc['rows']
        except Exception:
            pass
    rows = []
    try:
        rows = parse(_ensure_text(client_mod.request(BASE)))
    except Exception:
        rows = []
    if rows and cache_path:
        try:
            fh = open(cache_path, 'wb')
            fh.write(json.dumps({'ts': now, 'rows': rows},
                                ensure_ascii=False).encode('utf-8'))
            fh.close()
        except Exception:
            pass
    return rows
