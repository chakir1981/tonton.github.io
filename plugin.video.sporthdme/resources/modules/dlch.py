# -*- coding: utf-8 -*-
"""Chaînes sport françaises 24/7 — liens de secours (écosystème DaddyLive).

Pour les matchs qui n'ont aucune source française, l'add-on propose les
chaînes FR de la grille 24/7 (beIN Sports 1-3 + MAX 4-10, RMC Sport 1-2,
Canal+ …). Le match est alors visible même avec le réglage « Uniquement
les sources françaises », et les liens s'affichent dans le même style que
les liens français habituels : [COLOR gold]{chaîne}[/COLOR] - FR.

Résolution (même famille que les miroirs vertex/nexa/kora) :
    https://{domaine}/server_lookup.php?channel_id=premium{N}
        -> {"server_key": "..."}          ex. 'x4-cdn' ou 'top1/cdn'
    -> https://{key}new.{cdn}/{key}/premium{N}/mono.m3u8   (+ top1 spécial)

Les domaines de cet écosystème tournent régulièrement : la liste est
modifiable dans les réglages (Chaînes FR 24/7 : domaines Daddylive).
Ce module est volontairement sans dépendance Kodi afin de rester
testable hors de Kodi.
"""

import json
import re

try:
    import six
except ImportError:
    six = None

# UA attendu par les CDN de la grille (mobile, cf. options EXTVLCOPT des flux)
DL_UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_7 like Mac OS X) '
         'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1')

# Domaines proposés par défaut (l'écosystème en publie des dizaines ; les
# vivants changent souvent -> réglage « Chaînes FR 24/7 » dans l'add-on).
DEFAULT_DOMAINS = 'dlhd.so,thedaddy.click'

_LOGOS = 'https://raw.githubusercontent.com/MarkMCFC/tfms.xyz/refs/heads/main/logos/'

# Grille FRANCE (nom affiché -> (clé premium, logo)). IDs stables dans
# l'écosystème ; seuls les domaines/CDN tournent.
CHANNELS = {
    u'beIN SPORTS 1':    ('premium116', _LOGOS + 'bein-sports-1-france.png'),
    u'beIN SPORTS 2':    ('premium117', _LOGOS + 'bein-sports-2-france.png'),
    u'beIN SPORTS 3':    ('premium118', _LOGOS + 'bein-sports-3-france.png'),
    u'beIN Sports MAX 4':  ('premium494', _LOGOS + 'bein-sports-max-4-france.png'),
    u'beIN Sports MAX 5':  ('premium495', _LOGOS + 'bein-sports-max-5-france.png'),
    u'beIN Sports MAX 6':  ('premium496', _LOGOS + 'bein-sports-max-6-france.png'),
    u'beIN Sports MAX 7':  ('premium497', _LOGOS + 'bein-sports-max-7-france.png'),
    u'beIN Sports MAX 8':  ('premium498', _LOGOS + 'bein-sports-max-8-france.png'),
    u'beIN Sports MAX 9':  ('premium499', _LOGOS + 'bein-sports-max-9-france.png'),
    u'beIN Sports MAX 10': ('premium500', _LOGOS + 'bein-sports-max-10-france.png'),
    u'RMC Sport 1':      ('premium119', _LOGOS + 'rmc-sport-1-fr.png'),
    u'RMC Sport 2':      ('premium120', _LOGOS + 'rmc-sport-2-fr.png'),
    u'Canal+':           ('premium121', _LOGOS + 'canal-plus.png'),
    u'Canal+ Sport':     ('premium122', _LOGOS + 'canal-plus-sport-france.png'),
    u'Canal+ Foot':      ('premium463', _LOGOS + 'canal-plus-foot.png'),
    u'Canal+ Sport360':  ('premium464', _LOGOS + 'canal-plus.png'),
    u'Canal+ Formula 1': ('premium273', _LOGOS + 'canal-plus-formula1.png'),
    u'CANAL+ MotoGP':    ('premium271', _LOGOS + 'canal-plus.png'),
    u'Eurosport 1':      ('premium772', _LOGOS + 'eurosport-1.png'),
    u'Eurosport 2':      ('premium773', _LOGOS + 'eurosport-2.png'),
    u'L\'Équipe':        ('premium645', _LOGOS + 'lequipe-france.png'),
    u'Tennis+ 1':        ('premium700', _LOGOS + 'Tennisplus.png'),
    u'Tennis+ 2':        ('premium701', _LOGOS + 'Tennisplus.png'),
    u'Tennis+ 3':        ('premium702', _LOGOS + 'Tennisplus.png'),
    u'TF1':              ('premium469', _LOGOS + 'tf1-fr.png'),
    u'M6':               ('premium470', _LOGOS + 'm6-france.png'),
}

# (motifs de compétition/sport, chaînes à proposer) — testé dans l'ordre,
# la première règle qui colle décide. Volontairement conservateur : sans
# compétition reconnue, aucune suggestion (le match reste filtré).
SPORT_MAP = (
    (r'ligue\s?des\s?champions|champions\s?league|\bucl\b|europa\s?league|conference\s?league',
     (u'Canal+ Sport', u'Canal+')),
    (r'premier\s?league|england|angleterre',
     (u'RMC Sport 1', u'RMC Sport 2')),
    (r'la\s?liga|laliga|espagne|espana|espa\u00f1a|spanish\s?league',
     (u'beIN SPORTS 1', u'beIN SPORTS 2')),
    (r'serie\s?a\b|calcio|italie|italy',
     (u'beIN SPORTS 2', u'beIN SPORTS 3')),
    (r'bundesliga|allemagne|germany',
     (u'beIN Sports MAX 4', u'beIN Sports MAX 5')),
    (r'ligue\s?1\b|ligue1',
     (u'beIN SPORTS 1', u'beIN Sports MAX 4')),
    (r'coupe\s?de\s?france|french\s?cup',
     (u'TF1', u'beIN SPORTS 1')),
    (r'world\s?cup|coupe\s?du\s?monde|euro\s?20|nations\s?league|ligue\s?des\s?nations',
     (u'TF1', u'beIN SPORTS 1')),
    (r'\bnba\b|basket',
     (u'beIN SPORTS 1', u'beIN SPORTS 2')),
    (r'\bnfl\b|american\s?football',
     (u'beIN Sports MAX 4', u'beIN Sports MAX 6')),
    (r'\bnhl\b|hockey',
     (u'beIN SPORTS 3', u'beIN Sports MAX 4')),
    (r'tennis|atp|wta|roland|wimbledon|australian\s?open|us\s?open',
     (u'Tennis+ 1', u'Eurosport 1')),
    (r'\bf1\b|formula\s?1|formule\s?1|grand\s?prix',
     (u'Canal+ Formula 1', u'Canal+')),
    (r'motogp|moto\s?gp|supercross|\bmoto\b',
     (u'CANAL+ MotoGP', u'Canal+')),
    (r'ufc|boxe|boxing',
     (u'RMC Sport 1', u'RMC Sport 2')),
    (r'rugby|top\s?14|tournoi\s?des\s?6',
     (u'Canal+ Sport', u'Canal+')),
    (r'cycl|tour\s?de\s?france|giro|vuelta',
     (u'Eurosport 1', u'Eurosport 2')),
    (r'handball',
     (u'beIN SPORTS 1', u'beIN Sports MAX 4')),
    (r'libertadores|copa\s?(america|libertadores)',
     (u'beIN SPORTS 1', u'beIN SPORTS 2')),
)


def _u(text):
    """Texte uni (str py3 / unicode py2) depuis bytes ou str."""
    if isinstance(text, bytes):
        if six is not None:
            return six.ensure_text(text, encoding='utf-8', errors='replace')
        return text.decode('utf-8', 'replace')
    return text


def suggest(text):
    """Noms de chaînes FR à proposer pour ce libellé de match (max 3)."""
    t = _u(text or '').lower()
    if not t:
        return []
    for rx, names in SPORT_MAP:
        try:
            hit = re.search(rx, t)
        except Exception:
            hit = None
        if hit:
            out = []
            for n in names:
                if n in CHANNELS and n not in out:
                    out.append(n)
            return out[:3]
    return []


def has_suggestion(text):
    """True si au moins une chaîne FR peut être proposée pour ce match."""
    return bool(suggest(text))


def _ensure_text(data):
    if data is None:
        return u''
    if isinstance(data, bytes):
        return _u(data)
    return _u(str(data))


def resolve(premium_id, domains, client_mod, timeout=12):
    """Résout une clé premium{N} en flux mono.m3u8.

    Pour chaque domaine (server_lookup.php?channel_id=…) on récupère le
    server_key, puis on sonde les familles de CDN connues avec le bon
    referer. Renvoie (url_m3u8, domaine) ou None si tout échoue.
    client_mod = le module client de l'add-on (injection -> testable).
    """
    cid = premium_id if str(premium_id).startswith('premium') else 'premium{0}'.format(premium_id)
    cid = str(cid)
    for dom in domains or []:
        dom = _u(dom).strip().replace('https://', '').replace('http://', '').strip('/')
        if not dom:
            continue
        base = 'https://' + dom
        ref = base + '/'
        try:
            body = client_mod.request(base + '/server_lookup.php?channel_id=' + cid,
                                      referer=ref)
        except Exception:
            continue
        body = _ensure_text(body)
        server_key = ''
        try:
            server_key = (json.loads(body).get('server_key') or '').strip()
        except Exception:
            m = re.search(r'"server_key"\s*:\s*"([^"]+)"', body)
            if m:
                server_key = m.group(1).strip()
        if not server_key:
            continue
        sk = server_key.strip('/')
        candidates = []
        if sk in ('top1/cdn', 'top1'):
            candidates.append('https://top1.newkso.ru/top1/cdn/{0}/mono.m3u8'.format(cid))
        else:
            hp = sk.replace('/', '.')
            for cdn in ('newkso.ru', 'koskoros.ru', 'iosplayer.ru'):
                candidates.append('https://{0}.new.{1}/{2}/{3}/mono.m3u8'.format(hp, cdn, sk, cid))
        candidates.append('https://new.newkso.ru/{0}/{1}/mono.m3u8'.format(sk, cid))
        for url_ in candidates:
            url_ = re.sub(r'(?<!:)/{2,}', '/', url_)
            try:
                chunk = client_mod.request(url_, referer=ref)
            except Exception:
                continue
            s = _ensure_text(chunk)
            if s.startswith('#EXTM3U') or len(s) > 50:
                return (url_, dom)
    return None
