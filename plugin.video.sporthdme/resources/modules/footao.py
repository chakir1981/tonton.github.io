# -*- coding: utf-8 -*-
"""footao.tv — le guide du foot à la TV (aucun flux, que du programme).

Pour un match qui n'a aucune source française, ce module retrouve le match
dans le programme TV français de footao.tv et indique LA chaîne qui le
diffuse vraiment (au lieu de deviner par la compétition). La chaîne est
ensuite résolue en flux par dlch.py (beIN, Canal+, RMC, TF1, M6, Ligue 1+…) ;
les chaînes sans flux connu (DAZN, YouTube, Apple TV, Disney+…) servent
quand même à prouver la diffusion française (match laissé visible) et le
programme Daddylive du jour (dlsched.py) fournit alors les liens réels.

Pages utilisées (HTML balisé schema.org/SportsEvent, sans anti-bot) :
    https://www.footao.tv/                       aujourd'hui
    https://www.footao.tv/match-foot.php?v=demain  demain

Ce module est volontairement sans dépendance Kodi afin de rester testable
hors de Kodi.
"""

import json
import re
import time
import unicodedata

BASE = 'https://www.footao.tv/'
TODAY_URL = BASE
TOMORROW_URL = BASE + 'match-foot.php?v=demain'
CACHE_AGE = 3600          # 1 h : le programme du jour ne bouge presque pas
THRESHOLD = 0.7           # score minimal pour retenir un rapprochement

# html -> lignes ------------------------------------------------------------

_BLOCK = re.compile(r'<div itemscope itemtype="https://schema\.org/SportsEvent">(.*?)</div>', re.DOTALL)
_TIME = re.compile(r'<time itemprop="startDate" content="([^"]+)"')
_CHAN = re.compile(r'alt="match ([^"]+?)(?: foot)?(?: programme soir)?"')
_TEAMS = re.compile(r'<span itemprop="name">([^<]+)</span>')
_COMP = re.compile(r'<span class="ap"><a[^>]*>([^<]+)</a>')
_ENTITIES = ((u'&#183;', u'\xb7'), (u'&amp;', u'&'), (u'&#039;', u"'"), (u'&apos;', u"'"),
             (u'&nbsp;', u' '), (u'&quot;', u'"'), (u'&lt;', u'<'), (u'&gt;', u'>'))


def _unescape(s):
    for ent, ch in _ENTITIES:
        s = s.replace(ent, ch)
    return s


def parse(html):
    """Analyse une page footao -> liste de matchs {day, hour, t1, t2, comp, channel}."""
    rows = []
    for b in _BLOCK.findall(html or ''):
        t, ch, tm, comp = _TIME.search(b), _CHAN.search(b), _TEAMS.search(b), _COMP.search(b)
        if not (t and ch and tm):
            continue
        teams = _unescape(tm.group(1)).split(u'\xb7')
        if len(teams) != 2:
            continue
        dt = t.group(1)
        rows.append({
            'dt': dt,
            'day': dt[:10],
            'hour': dt[11:16],
            't1': teams[0].strip(),
            't2': teams[1].strip(),
            'comp': _unescape(comp.group(1)).strip() if comp else u'',
            'channel': _unescape(ch.group(1)).strip(),
        })
    return rows


# rapprochement des noms d'équipes ------------------------------------------

_DROP = {'fc', 'cf', 'sc', 'ac', 'ud', 'cd', 'cp', 'bk', 'if', 'aif', 'sk', 'club',
         'foot', 'calcio', 'women', 'fem', 'feminin', 'kf', 'fk', 'as', 'ss', 'ssc',
         'afc', 'cfc', 'racing', 'atletico', 'sporting', 'de', 'the'}


_TOK_CACHE = {}


def _tokens(s):
    """Jetons significatifs d'un nom d'équipe (mémoïsé : la même grille est
    comparée à chaque match de la liste, inutile de re-tokeniser)."""
    ts = _TOK_CACHE.get(s)
    if ts is not None:
        return ts
    s = unicodedata.normalize('NFKD', s or u'')
    s = u''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r'[^a-z0-9 ]', u' ', s)
    ts = [t for t in s.split() if t not in _DROP]
    _TOK_CACHE[s] = ts
    return ts


def _tsim(a, b):
    """Similarité d'un token : égal, préfixe, préfixe commun long."""
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


def score(row, team1, team2):
    """Score du rapprochement, orientation cohérente (domicile/extérieur)."""
    if not team1 or not team2:
        return 0.0
    direct = (_tsim(row['t1'], team1) + _tsim(row['t2'], team2)) / 2
    swap = (_tsim(row['t1'], team2) + _tsim(row['t2'], team1)) / 2
    return max(direct, swap)


def find(rows, team1, team2, day=''):
    """Meilleur match footao pour (team1, team2), même jour de préférence."""
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


# chaîne footao -> chaîne résolvable par dlch --------------------------------

# Libellés footao (alt des images) -> nom de chaîne de dlch.CHANNELS.
# Les chaînes absentes d'ici (DAZN, Canal+ Live N, Ligue 1+ add. N, YouTube,
# Apple TV, Disney+, W9, France TV…) n'ont pas de flux premium connu :
# channel_of renvoie '' (le programme Daddylive du jour les couvre quand
# une chaîne de sa grille porte le match).
_CANON = (
    (r'^beIN SPORTS MAX (\d+)$', lambda m: u'beIN Sports MAX {0}'.format(m.group(1))),
    (r'^beIN SPORTS ([123])$', lambda m: u'beIN SPORTS {0}'.format(m.group(1))),
    (r'^Canal\+ Sport 360$', u'Canal+ Sport360'),
    (r'^Canal\+ Sport$', u'Canal+ Sport'),
    (r'^Canal\+ Foot$', u'Canal+ Foot'),
    (r'^Canal\+$', u'Canal+'),
    (r'^Ligue\s?1\+$', u'Ligue1+ France'),
    (r"^L'Equipe$", u"L'Équipe"),
    (r'^RMC Sport ([12])$', lambda m: u'RMC Sport {0}'.format(m.group(1))),
    (r'^Eurosport ([12])$', lambda m: u'Eurosport {0}'.format(m.group(1))),
    (r'^(TF1|M6)$', lambda m: m.group(1)),
)


def channel_of(row):
    """Clé de dlch.CHANNELS pour la chaîne du match, ou '' si non résolvable."""
    lbl = (row.get('channel') or u'').strip()
    for rx, out in _CANON:
        m = re.match(rx, lbl, re.I)
        if m:
            return out(m) if callable(out) else out
    return u''


# chargement (avec cache disque) ---------------------------------------------

def _ensure_text(data):
    if data is None:
        return u''
    if isinstance(data, bytes):
        return data.decode('utf-8', 'replace')
    return data


def load(client_mod, cache_path='', force=False):
    """Grille footao (aujourd'hui + demain) + journal du jour. Cache ~1 h.

    footao retire les matchs commencés de sa page du jour ; le journal
    conserve chaque grille déjà vue (fusion par jour, purgée des jours
    passés) pour qu'un match en cours reste rattaché à sa chaîne tant
    qu'il n'est pas terminé.

    client_mod : module client de l'add-on (méthode request) — injection
    pour rester testable. cache_path vide = pas de cache disque.
    """
    now = time.time()
    doc = None
    if cache_path and not force:
        try:
            fh = open(cache_path, 'rb')
            doc = json.loads(fh.read().decode('utf-8'))
            fh.close()
            if now - float(doc.get('ts', 0)) < CACHE_AGE and doc.get('rows'):
                return doc['rows']
        except Exception:
            doc = None
    fresh = []
    for url in (TODAY_URL, TOMORROW_URL):
        try:
            fresh += parse(_ensure_text(client_mod.request(url)))
        except Exception:
            continue
    journal = (doc or {}).get('journal') or {}
    if fresh:
        rows = _merge_journal(fresh, journal)
    elif journal:
        # pages momentanément indisponibles : resservir la grille connue
        rows = [journal[d][k] for d in sorted(journal) for k in sorted(journal[d])]
    else:
        rows = []
    if rows and cache_path:
        try:
            fh = open(cache_path, 'wb')
            fh.write(json.dumps({'ts': now, 'rows': rows, 'journal': journal},
                                ensure_ascii=False).encode('utf-8'))
            fh.close()
        except Exception:
            pass
    return rows


def _journal_key(r):
    return u'|'.join((r['day'], r['hour'], r['t1'], r['t2']))


def _merge_journal(fresh, journal):
    """Fusionne les lignes fraîchement parsées dans le journal du jour
    (une ligne déjà vue garde sa chaîne d'origine), purge les jours passés
    et renvoie la liste plate triée par jour puis heure."""
    today = time.strftime('%Y-%m-%d')
    for r in fresh:
        day = journal.setdefault(r['day'], {})
        k = _journal_key(r)
        if k not in day:
            day[k] = r
    for d in [x for x in journal if x < today]:
        del journal[d]
    return [journal[d][k] for d in sorted(journal) for k in sorted(journal[d])]
