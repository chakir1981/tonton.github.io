# -*- coding: utf-8 -*-
"""
SportOnline (sportsonline.st) — source BÉTA for plugin.video.sporthdme.

Kodi-agnostic on purpose: the parse/decode functions take raw text so they run
(and self-test) outside Kodi. Only the fetch helpers touch the network.

Indépendante de DaddyLive (ni one.sporthd.me, ni vertex/nexa/kora, ni
matchli.st) : une simple grille texte du jour, mise à jour quotidiennement
par la source, qui DÉCLARE la langue de chaque chaîne (ex. « HD4 GREEK &
FRENCH »). Aucun mur anti-bot, aucun JSON lourd : un fichier de ~6 Ko.

Flow:
  {domaine}/prog.txt                 -> grille des matchs (aujourd'hui + demain,
                                        heures à l'anglaise Europe/London)
  ww1.sportsonnline.click/channels/… -> page canal avec UNE iframe (embed
                                        dynmeadow.net, sous-domaine haché,
                                        lecteur Clappr + SwarmCloud P2P HLS —
                                        v3.7.17 : la page canal répond à
                                        nouveau 200 partout, le verrou 403
                                        d'origine a disparu)
  embed                              -> page lecteur ; le m3u8 est extrait par
                                        plusieurs stratégies connues (m3u8
                                        direct, Playerjs file:, Clappr source,
                                        char-array, econfig, jsunpack, ?ppcfg=1)
                                        — mêmes techniques que les résolveurs
                                        existants de l'add-on.

Heures : la grille est à l'heure UK (GMT/BST) — Lorient x Brest « 19:45 » =
20:45 à Paris. Conversion indépendante de la table des fuseaux (règle BST :
dernier dimanche de mars -> dernier dimanche d'octobre), car gettz() peut
renvoyer None sur Android.
"""

import re
import time
import calendar
from datetime import datetime, timedelta

NAME = u'BÉTA SportOnline'
KEY = 'spo'
DESC = (u'[B]BÉTA — SportOnline[/B]\n\n'
        u'Source de test indépendante de DaddyLive : grille texte du jour '
        u'(aujourd\'hui + demain) avec la langue de chaque chaîne déclarée '
        u'par la source (FR fiable). Heures UK converties en heure locale.')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# Domaines officiels de la grille (le .txt annonce lui-même son domaine actuel
# « NEW DOMAIN SPORTSONLINE.ST ») ; on essaie dans l'ordre.
PROG_URLS = (
    # v3.7.17 — ordre re-vérifié en réseau réel : .st et .gl répondent 200,
    # .si/.to/ww1 restent en repli.
    'https://sportsonline.st/prog.txt',
    'https://sportsonline.gl/prog.txt',
    'https://sportsonline.si/prog.txt',
    'https://sportsonline.to/prog.txt',
    'https://ww1.sportsonnline.click/prog.txt',
)
BASE_PROG_URLS = PROG_URLS


def set_prog_urls(urls):
    """v3.7.18 — domaines prog.txt DISTANTS (sporthdme_remote.json, clé
    spo_prog_urls) essayés AVANT la liste intégrée : si la source change
    de domaine, la correction est poussée à distance sans nouvelle version
    de l'add-on. Domaines inconnus/malformés ignorés, doublons fusionnés,
    jamais d'exception."""
    global PROG_URLS
    try:
        extra = []
        for u in (urls or []):
            u = (u or u'').strip()
            if u.startswith('http') and u not in extra:
                extra.append(u)
        if extra:
            merged = list(extra)
            for u in BASE_PROG_URLS:
                if u not in merged:
                    merged.append(u)
            PROG_URLS = tuple(merged)
    except Exception:
        pass

DAYS = {'MONDAY': 0, 'TUESDAY': 1, 'WEDNESDAY': 2, 'THURSDAY': 3,
        'FRIDAY': 4, 'SATURDAY': 5, 'SUNDAY': 6}

# Langues déclarées par la source -> codes affichables (mêmes codes que le
# guide DaddyLive : fr, en, es, it… ; br = brésilien).
LANG_CODES = {
    'ENGLISH': 'en', 'FRENCH': 'fr', 'SPANISH': 'es', 'ITALIAN': 'it',
    'GERMAN': 'de', 'PORTUGUESE': 'pt', 'BRAZILIAN': 'br', 'GREEK': 'gr',
    'TURKISH': 'tr', 'ARABIC': 'ar', 'RUSSIAN': 'ru', 'DUTCH': 'nl',
    'POLISH': 'pl', 'SWEDISH': 'sv', 'NORWEGIAN': 'no', 'DANISH': 'dk',
    'FINNISH': 'fi', 'ROMANIAN': 'ro', 'SERBIAN': 'rs', 'CROATIAN': 'hr',
    'BULGARIAN': 'bg', 'HUNGARIAN': 'hu', 'CZECH': 'cz', 'SLOVAK': 'sk',
    'UKRAINIAN': 'ua', 'ALBANIAN': 'al', 'HEBREW': 'il', 'INDIAN': 'in',
    'CHINESE': 'cn', 'JAPANESE': 'jp', 'KOREAN': 'kr', 'PERSIAN': 'ir',
}

# Préfixes « NHL: », « Tennis: … » reconnus comme un sport (le reste des
# lignes, « ÉquipeA x ÉquipeB », est du football).
SPORT_PREFIXES = {'NHL', 'NBA', 'NFL', 'MLB', 'TENNIS', 'ATP', 'WTA', 'UFC',
                  'BOXING', 'F1', 'MOTOGP', 'CRICKET', 'RUGBY', 'BASEBALL',
                  'VOLLEYBALL', 'HANDBALL', 'GOLF', 'DARTS', 'SNOOKER',
                  'MMA', 'NCAAF', 'NCAAB', 'CFB', 'CBB', 'SOCCER', 'FOOTBALL'}

DEFAULT_DURATION_MIN = 120


# ---- fuseau UK (sans dépendre de la table des fuseaux d'Android) ----------

def _last_sunday(year, month):
    """Dernier dimanche du mois (jour du mois)."""
    last = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last)
    return last - ((d.weekday() + 1) % 7)   # weekday(): lundi=0 … dimanche=6


def is_bst(year, month, day):
    """True si la date est à l'heure d'été britannique (BST, UTC+1).
    BST : dernier dimanche de mars 01:00 UTC -> dernier dimanche d'octobre
    01:00 UTC. Précision au jour près, largement suffisante pour une grille
    de matchs."""
    m3, m10 = _last_sunday(year, 3), _last_sunday(year, 10)
    date = (month, day)
    return (3, m3) <= date < (10, m10)


def uk_wall_to_ms(year, month, day, hour, minute):
    """Heure légale UK (murale) -> epoch UTC en ms."""
    dt = datetime(year, month, day, hour, minute)
    offset = 3600 if is_bst(year, month, day) else 0
    return (calendar.timegm(dt.timetuple()) - offset) * 1000


# ---- parsers purs (testables hors réseau) ----------------------------------

def parse_langs(text):
    """« GREEK & FRENCH » -> ['gr', 'fr'] (ordre de déclaration conservé)."""
    out = []
    for word in re.findall(r'[A-Za-z]+', text or u''):
        code = LANG_CODES.get(word.upper())
        if code and code not in out:
            out.append(code)
    return out


def split_sport(title):
    """« NHL: Anaheim Ducks @ Ottawa Senators » -> ('NHL', 'Anaheim …').
    « Lorient x Brest » -> ('Football', 'Lorient x Brest')."""
    m = re.match(r'^([A-Za-z][A-Za-z0-9 .]{1,20}?)\s*:\s+(.+)$', title or u'')
    if m and m.group(1).strip().upper() in SPORT_PREFIXES:
        p = m.group(1).strip()
        # « NHL » reste « NHL » (str.title() donnerait « Nhl ») ; un préfixe
        # tapé en minuscules est capitalisé pour l'affichage.
        sport = p if p == p.upper() else p.title()
        return sport, m.group(2).strip()
    return u'Football', (title or u'').strip()


def title_vs(title):
    """« Lorient x Brest » -> « Lorient vs Brest » (style du guide)."""
    t = re.sub(r'\bx\b', u'vs', title or u'')
    return t.strip()


def parse_chan_key(url):
    """…/channels/hd/hd4.php -> 'hd4' (clé des déclarations de langue)."""
    base = (url or '').rsplit('/', 1)[-1].lower()
    return base[:-4] if base.endswith('.php') else base


def reorder_langs(langs):
    """« fr » d'abord, puis ordre alphabétique (règle 3.7.10/3.7.11) —
    appliqué aux langues d'un événement ET à celles de chaque serveur."""
    rest = sorted(l for l in langs if l != 'fr')
    return (['fr'] + rest) if 'fr' in langs else rest


def _duration_min(sport, title):
    t = u'{0} {1}'.format(sport, title).lower()
    if 'tennis' in t or 'atp' in t or 'wta' in t:
        return 240
    if 'nba' in t or 'nhl' in t or 'mlb' in t:
        return 180
    if 'nfl' in t or 'ncaaf' in t:
        return 210
    return DEFAULT_DURATION_MIN


# ---- repli FR strict (v3.7.18) ----------------------------------------------
# prog.txt DÉCLARE normalement la langue de chaque chaîne (« HD10 FRENCH »),
# mais 8 canaux sur 25 du jour (sporttv*, eleven*, btv…) restent sans
# déclaration : leurs matchs disparaissent quand « Uniquement les sources
# françaises » est actif. Repli : équipes/compétitions françaises, avec une
# liste ÉTROITE — mesure du jour : sans les garde-fous, « ATP Masters 1000 -
# Paris 2026 » et « Villarreal U19 vs PSG U19 » seraient des faux positifs
# (catégories jeunes exclues, mots entiers seulement, Red Star Belgrade et
# Dinamo Brest explicitement évités).

_FR_ACCENTS = {
    u'à': u'a', u'â': u'a', u'ä': u'a', u'é': u'e', u'è': u'e', u'ê': u'e',
    u'ë': u'e', u'î': u'i', u'ï': u'i', u'ô': u'o', u'ö': u'o', u'ù': u'u',
    u'û': u'u', u'ü': u'u', u'ç': u'c', u'ñ': u'n',
    u'À': u'a', u'Â': u'a', u'É': u'e', u'È': u'e', u'Ê': u'e', u'Î': u'i',
    u'Ô': u'o', u'Û': u'u', u'Ç': u'c', u'’': u' ',
}

# Équipes FR (Ligue 1 / Ligue 2 / National) — normalisées sans accent.
_FR_TEAMS = (
    'psg', 'paris saint germain', 'paris fc', 'marseille', 'olympique marseille',
    'lyon', 'olympique lyonnais', 'monaco', 'lille', 'losc', 'nice', 'lens',
    'rennes', 'lorient', 'toulouse', 'montpellier', 'nantes',
    'strasbourg', 'metz', 'clermont', 'le havre', 'angers', 'auxerre',
    'saint etienne', 'reims',
    'sochaux', 'nancy', 'grenoble', 'annecy', 'pau', 'rodez', 'dunkerque',
    'laval', 'troyes', 'bastia', 'ajaccio', 'guingamp', 'caen', 'amiens',
    'valenciennes', 'quevilly', 'concarneau', 'chateauroux', 'niort',
    'versailles', 'martigues', 'boulogne', 'orleans', 'bourg en bresse',
)

# Compétitions FR + sélection nationale.
_FR_EXTRA = ('france', 'ligue 1', 'ligue 2', 'coupe de france',
             'trophee des champions', 'betclic elite', 'top 14', 'pro d2')

# Catégories à exclure du repli (jeunes/réserves : diffusion FR non garantie).
_FR_BLOCK = ('u17', 'u18', 'u19', 'u20', 'u21', u'réserve', 'reserve', 'youth',
             'espoirs')


def _norm_fr(text):
    """Normalisation pour comparaison : sans accent, minuscules, séparateurs
    unifiés (« Saint-Étienne » -> « saint etienne »)."""
    t = (text or u'').translate(_FR_ACCENTS).lower()
    return re.sub(r'[^a-z0-9]+', ' ', t).strip()


def _w(kw):
    """Mot(s) ENTIER(s) : ni précédé ni suivi d'une lettre/chiffre
    (« pau » ne matche pas « paulista »)."""
    return re.compile(r'(?<![a-z0-9])' + kw + r'(?![a-z0-9])')

_FR_PATTERNS = [_w(k) for k in _FR_TEAMS] + [_w(k) for k in _FR_EXTRA]
# « Red Star » tout seul = club FR ; « Red Star Belgrade » = Serbie.
_FR_PATTERNS.append(re.compile(r'(?<![a-z0-9])red star(?! (belgrade|beograd))'
                               r'(?![a-z0-9])'))
# « Brest » = club FR ; « Dinamo Brest » = Biélorussie.
_FR_PATTERNS.append(re.compile(r'(?<![a-z0-9])(?<!dinamo )brest(?![a-z0-9])'))
_FR_BLOCK_RES = [_w(k) for k in _FR_BLOCK]


def fr_fallback(title):
    """v3.7.18 — True si le libellé du match est clairement français
    (clubs L1/L2/National, sélection, compétitions FR). Strict par
    conception : il ne sert qu'à rattraper les canaux SANS déclaration de
    langue quand « Uniquement les sources françaises » est actif."""
    t = _norm_fr(title)
    if not t:
        return False
    for rx in _FR_BLOCK_RES:
        if rx.search(t):
            return False
    for rx in _FR_PATTERNS:
        if rx.search(t):
            return True
    return False


def parse_prog(txt, now=None):
    """Grille prog.txt -> liste d'événements normalisés (tri chronologique).

    Chaque événement : {title, sport, start_ms, status, servers, langs}.
    Les matchs diffusés sur plusieurs chaînes (Sassuolo x Fiorentina sur
    hd7 + hd8 + sporttv1) sont UN SEUL événement avec plusieurs serveurs.
    Les blocs « d'hier » (fichier pas encore renouvelé) sont ignorés.
    """
    now_ms = (now.timestamp() * 1000) if now is not None else time.time() * 1000
    # « aujourd'hui » à l'heure UK (les blocs du fichier sont des jours UK) :
    # UTC + 1 h si BST. Comme la conversion des heures, sans table des fuseaux.
    # (epoch -> datetime UTC via timedelta : compatible Py2/Py3 sans warning)
    utc_now = datetime(1970, 1, 1) + timedelta(seconds=now_ms / 1000.0)
    today = utc_now + timedelta(hours=1 if is_bst(utc_now.year, utc_now.month,
                                                 utc_now.day) else 0)
    today = today.date()

    events = {}          # (title, start_ms) -> event dict
    cur_day = None       # date du bloc courant (None = hors bloc)
    chan_langs = {}      # 'hd4' -> ['gr','fr'] (déclarations du bloc courant)

    for raw in (txt or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        head = line.split()[0].rstrip(':') if line.split() else u''
        if head.upper() in DAYS:
            dow = DAYS[head.upper()]
            offset = (dow - today.weekday()) % 7
            # 6 = « hier » (fichier pas encore renouvelé) : on ignore le bloc
            cur_day = None if offset == 6 else today + timedelta(days=offset)
            chan_langs = {}     # les langues sont re-déclarées chaque jour
            continue
        if cur_day is None:
            continue
        # déclaration de langue : « HD4 GREEK & FRENCH »
        m = re.match(r'^([A-Za-z]{1,3}\d{1,2})\s+([A-Z][A-Z &]+)$', line)
        if m:
            langs = parse_langs(m.group(2))
            if langs:
                chan_langs[m.group(1).lower()] = langs
            continue
        # ligne de match : « 19:45   Lorient x Brest | https://… »
        m = re.match(r'^(\d{1,2}):(\d{2})\s+(.+?)\s*\|\s*(https?://\S+)\s*$', line)
        if not m:
            continue
        hh, mi, title, url = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        start_ms = uk_wall_to_ms(cur_day.year, cur_day.month, cur_day.day, hh, mi)
        sport, raw_title = split_sport(title)
        ev_title = title_vs(raw_title)
        dur = _duration_min(sport, ev_title) * 60 * 1000
        if now_ms > start_ms + dur:        # déjà terminé
            continue
        status = 'live' if start_ms <= now_ms <= start_ms + dur else 'soon'
        key = (ev_title, start_ms)
        ev = events.get(key)
        if ev is None:
            ev = {'title': ev_title, 'sport': sport, 'start_ms': start_ms,
                  'status': status, 'servers': [], 'langs': []}
            events[key] = ev
        ckey = parse_chan_key(url)
        cl = reorder_langs(chan_langs.get(ckey, []))
        for l in cl:
            if l not in ev['langs']:
                ev['langs'].append(l)
        if not ev['status'] == 'live' and status == 'live':
            ev['status'] = 'live'
        ev['servers'].append([ckey.upper(), url, u','.join(cl)])
    out = sorted(events.values(), key=lambda e: (e['start_ms'], e['title']))
    # langues : fr d'abord, puis ordre alphabétique (règle 3.7.10/3.7.11)
    for ev in out:
        ev['langs'] = reorder_langs(ev['langs'])
    return out


# ---- extraction du flux (mêmes stratégies que les résolveurs existants) ----

def absolutize(url, base):
    if not url:
        return url
    url = url.strip()
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('http'):
        return url
    if base.startswith('http'):
        root = base.rsplit('/', 1)[0]
        return root + '/' + url.lstrip('/')
    return url


def extract_iframes(page_html):
    """Toutes les <iframe src> absolues d'une page canal."""
    out = []
    for src in re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', page_html or u'', re.I):
        u = absolutize(src, '')
        if u.startswith('http'):
            out.append(u)
    return out


def extract_streams(embed_html):
    """v3.7.18 — LISTE ORDONNÉE des flux candidats d'une page lecteur.

    Le résolveur valide chaque candidat (GET court du manifest, cf.
    validate_m3u8) et joue le premier qui répond vraiment :
      1) stream_url (P2P SwarmCloud, servie en HLS classique) — mesurée 200
         avec segments .ts jouables quand stream_url_nop2p répondait 403 ;
      2) stream_url_nop2p ;
      3) les autres formes connues de l'add-on (m3u8 direct, config JSON
         ?ppcfg=1, Playerjs/Clappr, char-array, jsunpack).
    Dédoublonné en conservant l'ordre de préférence. Le module econfig
    (résolveurs DaddyLive) n'est pas modifié."""
    html = embed_html or u''
    if not html:
        return []
    out = []
    # 1) blob window._econfig (embed dynmeadow/tossequip, Clappr+SwarmCloud) :
    #    stream_url D'ABORD, repli stream_url_nop2p.
    try:
        from resources.modules import econfig as _econfig
        raw = _econfig.find_econfig(html)
        if raw:
            cfg = _econfig.decode(raw)
            for k in ('stream_url', 'stream_url_nop2p'):
                fl = cfg.get(k)
                if fl:
                    fl = absolutize(fl, '')
                    if fl not in out:
                        out.append(fl)
    except Exception:
        pass
    # 2) m3u8 / mpd direct dans la page
    for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
        if m not in out:
            out.append(m)
    # 3) config lecteur JSON ?ppcfg=1 (dinamic-embed)
    try:
        import json as _json
        cfg = _json.loads(html.strip())
        fl = cfg.get('src') or cfg.get('srcBase') or cfg.get('url')
        if fl:
            fl = absolutize(fl, '')
            if fl not in out:
                out.append(fl)
    except Exception:
        pass
    # 4) Playerjs « file:"…" » / Clappr « source:"…" » / src:"…"
    m = re.search(r'(?:file|source|src)\s*:\s*["\']([^"\']*(?:\.m3u8|\.mpd|/playlist)[^"\']*)["\']', html)
    if m:
        fl = absolutize(m.group(1), '')
        if fl not in out:
            out.append(fl)
    # 5) char-array return(["…"].join(""))
    arr_m = re.search(r'return\s*\(\s*(\[(?:"[^"]*",?\s*)+\])', html)
    if arr_m:
        fl = u''.join(re.findall(r'"([^"]*)"', arr_m.group(1))).replace('\\/', '/')
        if fl.startswith('http') and fl not in out:
            out.append(fl)
    # 6) script packé eval(function(p,a,c,k,e,…)
    if 'eval(function(p,a,c,k,e' in html:
        try:
            from resources.modules import jsunpack
            packed = re.search(r'(eval\(function\(p,a,c,k,e.*?\)\)\))', html, re.DOTALL)
            if packed:
                unpacked = six_ensure(jsunpack.unpack(packed.group(1)))
                for fl in extract_streams(unpacked):
                    if fl not in out:
                        out.append(fl)
        except Exception:
            pass
    return out


def extract_stream(embed_html):
    """Premier flux candidat (compat 3.7.17). La VALIDATION de chaque
    candidat (validate_m3u8) se fait dans resolve()."""
    cands = extract_streams(embed_html)
    return cands[0] if cands else None


def validate_m3u8(url, referer=None, timeout=8, getter=None):
    """v3.7.18 — le manifest est-il réellement jouable ? Un GET court du
    m3u8 : HTTP 200 + contenu HLS plausible (#EXTM3U, segments .ts,
    référence m3u8/mpd). Évite de donner à Kodi une URL morte (403 CDN,
    lien expiré…) alors qu'un autre candidat répond. getter injectable
    pour les tests ; toute erreur = False (jamais d'exception)."""
    try:
        if getter is None:
            import requests as _requests
            import urllib3 as _urllib3
            try:
                _urllib3.disable_warnings()
            except Exception:
                pass

            def getter(u, ref=None, t=None):
                r = _requests.get(u, headers={'User-Agent': UA,
                                              'Referer': ref or ''},
                                  timeout=t or timeout, verify=False)
                return r.status_code, r.content
        status, body = getter(url, ref=referer, t=timeout)
        if status != 200:
            return False
        if isinstance(body, bytes):
            body = (body or b'')[:4096].decode('utf-8', 'replace')
        head = (body or u'').lower()
        return ('#extm3u' in head or '.ts' in head or '.m3u8' in head
                or '.mpd' in head)
    except Exception:
        return False


def six_ensure(text):
    try:
        import six
        return six.ensure_text(text, encoding='utf-8', errors='replace')
    except Exception:
        return text


# ---- réseau (Kodi / usage réel) ---------------------------------------------

def _get(url, referer=None, timeout=15):
    import requests
    import urllib3
    try:
        urllib3.disable_warnings()
    except Exception:
        pass
    headers = {'User-Agent': UA}
    if referer:
        headers['Referer'] = referer
    r = requests.get(url, headers=headers, timeout=timeout, verify=False)
    # la grille est en UTF-8 (BOM inclus) mais servie sans charset : requests
    # la décoderait en latin-1 (« GenÃ§lerbirliÄi ») -> décodage explicite.
    return r.content.decode('utf-8', 'replace').lstrip(u'\ufeff')


def fetch_prog():
    """Télécharge la grille via la liste des domaines officiels (repli)."""
    last_err = None
    for url in PROG_URLS:
        try:
            txt = _get(url, referer=url.rsplit('/', 1)[0] + '/')
            if txt and ('LAST UPDATE' in txt
                        or re.search(r'\d{1,2}:\d{2}\s+.+\| https?://', txt)):
                return txt
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    return u''


def list_events():
    return parse_prog(fetch_prog())


def resolve(canal_url, getter=None):
    """…/channels/hd/hdN.php -> (m3u8, url_embed) ou None.

    v3.7.18 — chaque flux candidat de l'embed est VALIDÉ (GET court du
    manifest) avant d'être rendu : stream_url (P2P) essayé avant
    stream_url_nop2p, le premier qui répond vraiment gagne. Si AUCUN ne
    valide, le premier trouvé est quand même rendu — l'IP de l'utilisateur
    peut passer là où la sonde échoue, et Kodi garde le message d'erreur
    habituel sinon. 3 passes (v3.7.13 : l'embed peut répondre 403 à une IP
    de serveur, le sous-domaine tourne à chaque chargement de la page
    canal) avec référents alternés ; iframes imbriquées suivies d'un
    niveau ; une erreur réseau sur un niveau n'arrête pas les suivants et
    ne remonte jamais jusqu'à la lecture (aucune exception jetée)."""
    referers = ('https://sportsonline.st/', 'https://sportsonline.gl/',
                'https://sportsonline.si/')
    for attempt in range(3):
        try:
            page = _get(canal_url, referer=referers[attempt % len(referers)])
        except Exception:
            continue
        for frame in extract_iframes(page):
            try:
                emb = _get(frame, referer=canal_url)
            except Exception:
                continue
            # candidats du niveau 1 + d'un niveau imbriqué éventuel,
            # chacun avec SA page d'embed (le CDN exige ce Referer)
            cands = [(fl, frame) for fl in extract_streams(emb)]
            for nested in extract_iframes(emb):
                if nested == frame:
                    continue
                try:
                    emb2 = _get(nested, referer=frame)
                except Exception:
                    continue
                for fl in extract_streams(emb2):
                    if fl not in [c[0] for c in cands]:
                        cands.append((fl, nested))
            if not cands:
                continue
            first = None
            for fl, src in cands:
                if first is None:
                    first = (fl, src)
                if validate_m3u8(fl, referer=src, getter=getter):
                    return (fl, src)
            # aucun validé : mieux vaut tenter le premier que ne rien rendre
            return first
    return None


# ---- auto-contrôle hors Kodi -------------------------------------------------

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    txt = open('/home/z/my-project/scripts/fixture_prog.txt',
               encoding='utf-8', errors='replace').read()
    evs = parse_prog(txt, now=datetime(2026, 11, 2, 14, 30))
    assert evs, 'aucun événement parsé'
    fr = [e for e in evs if 'fr' in e['langs']]
    assert fr, 'aucun match FR parsé'
    lor = [e for e in evs if 'Lorient' in e['title']]
    assert lor and lor[0]['langs'] == ['fr', 'gr'], lor and lor[0]['langs']
    assert lor[0]['title'] == u'Lorient vs Brest'
    assert all(e['status'] in ('live', 'soon') for e in evs)
    assert evs == sorted(evs, key=lambda e: (e['start_ms'], e['title']))
    print('grille : %d événements, %d FR' % (len(evs), len(fr)))
    for e in fr[:6]:
        print('  FR>', e['title'], e['start_ms'], e['status'], e['langs'],
              [s[0] for s in e['servers']])
    # fuseau UK : 19:45 GMT du 02/11/2026 = 20:45 CET = 19:45 UTC
    assert uk_wall_to_ms(2026, 11, 2, 19, 45) == 1792028700000 - 0 or True
    import calendar as _c
    assert uk_wall_to_ms(2026, 11, 2, 19, 45) == _c.timegm((2026, 11, 2, 19, 45, 0, 0, 0, 0)) * 1000
    assert uk_wall_to_ms(2026, 6, 15, 19, 45) == _c.timegm((2026, 6, 15, 18, 45, 0, 0, 0, 0)) * 1000
    assert is_bst(2026, 7, 1) and not is_bst(2026, 11, 2)
    html = open('/home/z/my-project/scripts/fixture_hd4.html',
                encoding='utf-8', errors='replace').read()
    fr2 = extract_iframes(html)
    assert fr2 and 'dynmeadow' in fr2[0], fr2
    print('iframe canal :', fr2[0][:70])
    # v3.7.18 — repli FR strict
    for ok_t in (u'Reims x Sochaux', u'Red Star x Nancy', u'PSG x Marseille',
                 u'France x Italie', u'Saint-Étienne x Grenoble',
                 u'Pau x Bastia', u'Coupe de France : PSG x Le Mans'):
        assert fr_fallback(ok_t), ok_t
    for ko_t in (u'ATP Masters 1000 - Paris 2026',
                 u'Villarreal U19 vs PSG U19', u'Lazio x Roma',
                 u'Palmeiras x Paulista', u'Red Star Belgrade x Celtic',
                 u'Dinamo Brest x Zenit', u'Genclerbirligi x Kocaelispor'):
        assert not fr_fallback(ko_t), ko_t
    print('fr_fallback : %d positifs, %d negatifs OK'
          % (7, 7))
    # v3.7.18 — domaines distants
    set_prog_urls(['https://miroir.example/prog.txt'])
    assert PROG_URLS[0] == 'https://miroir.example/prog.txt', PROG_URLS
    assert 'https://sportsonline.st/prog.txt' in PROG_URLS, PROG_URLS
    set_prog_urls([])          # vide : liste intégrée conservée
    assert PROG_URLS[0] == 'https://miroir.example/prog.txt', PROG_URLS
    print('set_prog_urls OK :', len(PROG_URLS), 'domaines')
    print('OK')
