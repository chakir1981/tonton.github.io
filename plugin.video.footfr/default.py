# -*- coding: utf-8 -*-
"""
Foot FR (plugin.video.footfr) v1.0.0 — add-on KODI SÉPARÉ de SportHD.

« Matchs de football en français » : un seul sport à la fois clair et une
seule langue. Coexiste avec plugin.video.sporthdme (identifiant, menu et
cache entièrement distincts — les deux add-ons peuvent être installés
ensemble, ils ne partagent RIEN : ni fichier, ni réglage, ni code).

Menu :
  1. MATCHS FR DU JOUR        -> grille league.st/tvsport.guide, matchs avec
                                 chaîne française uniquement (football, ou
                                 tous les sports selon réglage).
  2. PAR CHAÎNE FR (24h/24)   -> chaînes françaises du guide, flux continus
                                 jouables à tout moment (Canal+ Foot…).
  3. PLUS DE MATCHS (SportOnline) -> source d'appoint FR : Ligue 2 et matchs
                                 diffusés en français absents du guide.
  4. CHAÎNES EN CLAIR (IPTV-org)  -> playlist publique GitHub (chaînes
                                 françaises en clair, flux directs m3u8).
  5. PLUS DE MATCHS (LiveSoccer)  -> grille française géo-IP (livesoccertv),
                                 associée automatiquement aux flux du guide.

Résolveurs (réutilisés et éprouvés dans SportHD) :
  - miroirs vertex.st / nexa.st / kora.st (api/player.php + lecteurs
    ?ppcfg=1, char-array, window._econfig, PP_CLAPPR_CONFIG) avec repli
    automatique de miroirs et cache 30 min ;
  - SportOnline (pages channels/hd/hdN.php, embeds dynmeadow).
"""
from __future__ import print_function
import base64
import re
import sys
import six
from six.moves.urllib.parse import quote_plus, quote, unquote, unquote_plus, urlencode, urlparse
from datetime import datetime
import json
import threading
import time
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import requests
from resources.modules import client
from resources.modules import site_sportonline

try:
    from resources.modules import fastcache as fastcache
except Exception:
    fastcache = None

_url = sys.argv[0]
_handle = int(sys.argv[1])

ADDON = xbmcaddon.Addon()
ADDON_DATA = ADDON.getAddonInfo('profile')
ADDON_PATH = ADDON.getAddonInfo('path')
FANART = ADDON.getAddonInfo('fanart')
ICON = ADDON.getAddonInfo('icon')
ID = ADDON.getAddonInfo('id')
NAME = ADDON.getAddonInfo('name')
VERSION = ADDON.getAddonInfo('version')
vers = VERSION

# Le guide JSON de tvsport.guide sert exactement le même schéma de données
# que la page league.st ({matches: [...]}) : c'est la source principale.
GUIDE_URL = 'https://tvsport.guide/proxy.php?source=index'
IPTV_URL = 'https://iptv-org.github.io/iptv/countries/fr.m3u'
LSOC_URL = 'https://www.livesoccertv.com/fr/'

_UA_CHROME = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

_LOG_INFO = getattr(xbmc, 'LOGINFO', None)
if _LOG_INFO is None:
    _LOG_INFO = getattr(xbmc, 'LOGNOTICE', xbmc.LOGDEBUG)


def _log_line(text):
    try:
        line = u'[{0}] {1}'.format(NAME, text)
        return six.ensure_str(line, encoding='utf-8', errors='replace')
    except Exception:
        return six.ensure_str(text, encoding='utf-8', errors='replace')


def log_debug(msg):
    xbmc.log(_log_line(msg), xbmc.LOGDEBUG)


def log_info(msg):
    xbmc.log(_log_line(msg), _LOG_INFO)


def log_warning(msg):
    xbmc.log(_log_line(msg), xbmc.LOGWARNING)


def log_error(msg):
    xbmc.log(_log_line(msg), xbmc.LOGERROR)


def _plain(text):
    """Retire les balises Kodi ([B] [I] [CR] [COLOR x]…) pour les champs
    infoLabels (le panneau d'information du skin les affiche en clair)."""
    try:
        return re.sub(r'\[/?(?:B|I|CR|UPPERCASE|LOWERCASE|COLOR[^\]]*)\]',
                      u'', text or u'', flags=re.IGNORECASE).strip()
    except Exception:
        return text or u''


def _setting_bool(key, default='true'):
    try:
        return (ADDON.getSetting(key) or default).strip().lower() == 'true'
    except Exception:
        return default == 'true'


# ---------------------------------------------------------------------------
# Filtrage français (hérité et éprouvé de SportHD : un code langue explicite
# non français rejette la chaîne avant tout test de nom — fin des
# Canal+ Sport 3/5 polonaises, TSN3 canadiennes, NFL Sunday Ticket US…).
# ---------------------------------------------------------------------------
FRENCH_LANGS = ('fr', 'french', u'français', 'francais')
NON_FR_LANGS = ('pl', 'us', 'gb', 'uk', 'de', 'es', 'it', 'pt', 'nl', 'dk', 'no',
                'se', 'fi', 'is', 'ie', 'gr', 'tr', 'ru', 'ua', 'cz', 'sk', 'ro',
                'rs', 'hr', 'bg', 'hu', 'si', 'ee', 'lv', 'lt', 'sa', 'ae', 'qa',
                'eg', 'ma', 'dz', 'tn', 'jp', 'cn', 'kr', 'in', 'au', 'nz', 'ca',
                'mx', 'br', 'ar', 'cl', 'co', 'pe', 'en', 'eng')
FRENCH_NAME_RE = re.compile(
    r'\bfr\b|\[fr\]|\(fr\)|french|fran[cç]ais|\bfrance\b|canal\+\s*foot|rmc\s*sport|'
    r'quipe|tf1\b|\bm6\b|\bw9\b|\btmc\b|ligue1'
    r'|bein sports (max )?\d+ france|eurosport \d+ france'
    r'|canal\+(?! ?sport ?[1-9]\b)(?! ?[1-9]\b)'
    r'|tennis\+|golf\+|\bfoot\+|automoto|motogp|infosport'
    r'|6ter|\barte\b|\bc8\b|\blci\b|\bbfm\b')
NOT_FRENCH_NAME_RE = re.compile(
    r'\b(sk|cz|pl|de|es|pt|it|nl|ro|rs|hr|bg|gr|tr|ua|ru)\b'
    r'|poland|spain|deutschland|germany|portugal|italy|belgium|netherlands'
    r'|nederland|romania|serbia|croatia|bulgaria|greece|hungary|slovakia'
    r'|slovenia|ukraine|russia|arabia|arabic|mena|t[uü]rkiye|turkey|malaysia'
    r'|australia|qatar|israel|kazakhstan|english|espa[nñ]ol|italiano'
    r'|portugu[eè]s|deutsch|nederlands|scandinavia|sweden|norway|denmark'
    r'|finland|usa',
    re.IGNORECASE)
_LANG_ALIASES = {'french': u'fr', u'français': u'fr', 'francais': u'fr',
                 'english': u'en', 'eng': u'en'}


def _is_french_source(name, lang):
    """True si la chaîne est en français (code pays, langue ou nom)."""
    try:
        lang_s = six.ensure_text(lang or u'', encoding='utf-8',
                                 errors='replace').strip().lower()
        name_s = six.ensure_text(name or u'', encoding='utf-8',
                                 errors='replace').lower()
    except Exception:
        return False
    if lang_s in FRENCH_LANGS:
        return True
    if lang_s and lang_s in NON_FR_LANGS:
        return False
    if name_s and NOT_FRENCH_NAME_RE.search(name_s):
        return False
    return bool(name_s and FRENCH_NAME_RE.search(name_s))


def _channel_lang(name, lang):
    """Code langue affichable (« fr », « de »…) : le code explicite fait foi ;
    sans code, une chaîne reconnue française par son nom vaut « fr »."""
    lang_s = six.ensure_text(lang or u'', encoding='utf-8',
                             errors='replace').strip().lower()
    if lang_s:
        if lang_s in FRENCH_LANGS:
            return u'fr'
        if lang_s in _LANG_ALIASES:
            return _LANG_ALIASES[lang_s]
        if re.match(r'^[a-z]{2,3}$', lang_s):
            return lang_s
        return u''
    if name and _is_french_source(name, u''):
        return u'fr'
    return u''


def _match_langs(channels):
    """Langues distinctes d'un match : « fr » d'abord puis alphabétique."""
    found = []
    try:
        for ch in channels or []:
            if isinstance(ch, dict):
                tag = _channel_lang(ch.get('name'), ch.get('language'))
                if tag and tag not in found:
                    found.append(tag)
    except Exception:
        pass
    rest = sorted(l for l in found if l != u'fr')
    return ([u'fr'] + rest) if u'fr' in found else rest


def _lang_suffix(langs):
    """Codes langue en MAJUSCULES en magenta ; « FR » premier et en gras.
    Foot FR n'affiche que FR quand « sources françaises » est actif
    (comportement par défaut, cohérent avec SportHD 3.7.16)."""
    if _setting_bool('french_only', 'true'):
        langs = [u'fr'] if u'fr' in langs else []
    parts = []
    for l in langs:
        if l == u'fr':
            parts.append(u'[COLOR magenta][B]FR[/B][/COLOR]')
        else:
            parts.append(u'[COLOR magenta]{0}[/COLOR]'.format(l.upper()))
    return (u' ' + u' '.join(parts)) if parts else u''


def _channels_have_french(channels):
    """True si le match a au moins une chaîne française (dicts du guide).
    Un match SANS aucune chaîne n'appartient pas à Foot FR (False)."""
    if not channels:
        return False
    identified = False
    for ch in channels:
        if isinstance(ch, dict):
            identified = True
            if _is_french_source(ch.get('name'), ch.get('language')):
                return True
    return not identified


def _chan_label(chan, lang, fr):
    """Libellé d'un lien : chaîne en or, langue en vert (FR) ou gris."""
    lang = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip()
    tag = u''
    if fr:
        tag = u'[COLOR lime]{0}[/COLOR]'.format(lang.upper() or u'FR')
    elif lang:
        tag = u'[COLOR dimgray]{0}[/COLOR]'.format(lang)
    return u'[COLOR gold]{0}[/COLOR]{1}'.format(chan, u' ' + tag if tag else u'')


_MIRROR_LABELS = ((r'vertex\.st', u'un'), (r'nexa\.st', u'deux'),
                  (r'kora\.st', u'trois'))


def _link_label(link):
    """« un / deux / trois » pour les miroirs vertex/nexa/kora, sinon domaine."""
    host = (urlparse(link).netloc or '').lower()
    for rx, lbl in _MIRROR_LABELS:
        if re.search(r'(?:^|\.)' + rx + '$', host):
            return lbl
    return host


def time_convert(timestamp):
    """Heure locale de l'appareil : « 03-Nov, 20:45 »."""
    try:
        timestamp = int(str(timestamp)[:10])
    except Exception:
        return u'-'
    dt_object = datetime.fromtimestamp(timestamp)
    time_ = dt_object.strftime("%d-%b, %H:%M")
    return six.ensure_text(time_, encoding='utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Grille principale (guide JSON tvsport.guide) — cache disque 2 min + grace
# 3 h + téléchargement en arrière-plan dès l'accueil (éprouvés en 3.7.7).
# ---------------------------------------------------------------------------

def _fetch_guide():
    matches = None
    try:
        matches = requests.get(GUIDE_URL, headers={'User-Agent': _UA_CHROME},
                               timeout=12).json().get('matches')
    except Exception as e:
        log_info('guide {0} failed: {1!r}'.format(GUIDE_URL, e))
    if matches and fastcache is not None:
        fastcache.fset('ev:guide', matches)
        fastcache.flush()
    return matches


def _guide_matches():
    matches = fastcache.fget('ev:guide', 120) if fastcache is not None else None
    if not matches and fastcache is not None:
        matches = fastcache.fstale('ev:guide', 3 * 3600)
        if matches:
            _refresh_matches_bg()
    if not matches:
        matches = _fetch_guide()
    return matches or []


_WARM_INFLIGHT = set()
_WARM_LOCK = threading.Lock()


def _refresh_matches_bg():
    """(Re)télécharge la grille en arrière-plan (fil daemon), un seul à la fois."""
    def _run():
        with _WARM_LOCK:
            if 'guide' in _WARM_INFLIGHT:
                return
            _WARM_INFLIGHT.add('guide')
        try:
            _fetch_guide()
        except Exception:
            pass
        finally:
            with _WARM_LOCK:
                _WARM_INFLIGHT.discard('guide')
    try:
        t = threading.Thread(target=_run)
        t.daemon = True
        t.start()
    except Exception:
        pass


def refresh_events():
    """Menu contextuel « Actualiser la grille » : re-télécharge puis redessine."""
    try:
        control_dialog_info(u"[COLOR skyblue]Actualisation de la grille…[/COLOR]", 3000)
        m = _fetch_guide()
        if not m:
            control_dialog_info(u"[COLOR gold]Actualisation impossible pour le moment (réseau ?).[/COLOR]", 5000)
            return
        xbmc.executebuiltin('Container.Refresh')
    except Exception:
        pass


def control_dialog_info(msg, ms=5000):
    try:
        xbmcgui.Dialog().notification(NAME, _plain(msg), ICON, ms, False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# MENU 1 — MATCHS FR DU JOUR
# ---------------------------------------------------------------------------

def matches_menu():
    matches = _guide_matches()
    if not matches:
        control_dialog_info(u"[COLOR red]Aucun match programmé.[/COLOR]", 5000)
        return
    now_ms = datetime.now().timestamp() * 1000
    all_sports = _setting_bool('all_sports', 'false')
    event_list = []
    filtered_out = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        channels = match.get('channels') or []
        if not _channels_have_french(channels):
            filtered_out += 1
            continue
        sport = six.ensure_text(match.get('sport') or u'',
                                encoding='utf-8', errors='replace')
        if not all_sports and sport.lower() != 'football':
            continue
        lname = match.get('league')
        lname = six.ensure_text(lname, encoding='utf-8',
                                errors='replace') if lname else sport
        team1 = match.get('team1')
        team2 = match.get('team2')
        team1 = six.ensure_text(team1, encoding='utf-8',
                                errors='replace') if team1 else None
        team2 = six.ensure_text(team2, encoding='utf-8',
                                errors='replace') if team2 else None
        event = u"{} vs {}".format(team1, team2) if team1 and team2 else team1
        try:
            compare = int(match['startTimestamp'])
            ftime = time_convert(compare)
        except Exception:
            compare = int('999999999999')
            ftime = u'-'
        duration_in_ms = (match.get('duration') or 120) * 60 * 1000
        is_live = bool(compare <= now_ms <= compare + duration_in_ms)
        finished = (not is_live) and now_ms > compare + duration_in_ms
        if is_live:
            m_color, state = 'lime', u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]'
        elif finished:
            m_color, state = 'grey', u' [COLOR grey]fini[/COLOR]'
        else:
            m_color, state = 'gold', u''
        cnt = _lang_suffix(_match_langs(channels))
        ftime_s = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        label = u'{0} [COLOR {1}][B]{2}[/B][/COLOR]{3}{4}'.format(
            ftime_s, m_color, event, state, cnt)
        plot = []
        if lname and lname != sport:
            plot.append(u'{0} — {1}'.format(sport or u'Sport', lname))
        elif sport:
            plot.append(sport)
        plot.append(u'Début : {0}{1}'.format(_plain(ftime),
                                             u' — en direct' if is_live else u''))
        chan_names = [six.ensure_text(ch.get('name') or u'',
                                      encoding='utf-8', errors='replace')
                      for ch in (channels or [])[:6] if isinstance(ch, dict)]
        chan_names = [c for c in chan_names if c]
        if chan_names:
            plot.append(u'Chaînes : ' + u', '.join(chan_names))
        info = {'title': _plain(event), 'plot': u'\n'.join(plot),
                'genre': sport or u'Football'}
        event_list.append([label, compare, channels, info])
    event_list.sort(key=lambda x: x[1])
    seen = set()
    ctx_refresh = [(u'Actualiser la grille',
                    'RunPlugin({0}?mode=refresh_events)'.format(_url))]
    shown = 0
    for label, _ts, channels, info in event_list:
        key = (label, repr(channels))
        if key in seen:
            continue
        seen.add(key)
        streams = base64.b64encode(six.ensure_binary(str(channels))).decode('ascii')
        addDir(label, streams, 'get_streams', ICON, info.get('title', u''),
               isFolder=True, infoLabels=info, context=ctx_refresh)
        shown += 1
    if fastcache is not None:
        fastcache.flush()
    if not shown:
        control_dialog_info(
            u"[COLOR gold]Aucun match français au catalogue pour l'instant.[/COLOR]",
            6000)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


# ---------------------------------------------------------------------------
# MENU 2 — PAR CHAÎNE FR (24h/24) : copie éprouvée de SportHD 3.7.20.
# ---------------------------------------------------------------------------

_FRCH_TTL = 1800          # cache 30 min


def _fr_channels_from_guide():
    matches = None
    try:
        if fastcache is not None:
            matches = fastcache.fget('ev:guide', 120)
    except Exception:
        matches = None
    if not matches:
        matches = _fetch_guide()
    if not matches:
        return []
    try:
        now_ms = datetime.now().timestamp() * 1000
    except Exception:
        now_ms = 0
    chans = {}
    for m in matches:
        if not isinstance(m, dict):
            continue
        st = m.get('startTimestamp') or 0
        dur = (m.get('duration') or 120) * 60 * 1000
        live = bool(st and now_ms and st <= now_ms < st + dur)
        t1 = m.get('team1')
        t2 = m.get('team2')
        titre = u'{0} vs {1}'.format(t1, t2) if t1 and t2 else (t1 or u'?')
        try:
            ftime = time_convert(st)
        except Exception:
            ftime = u''
        for ch in (m.get('channels') or []):
            if not isinstance(ch, dict):
                continue
            nm = ch.get('name') or u''
            nm = six.ensure_text(nm, encoding='utf-8', errors='replace').strip()
            if not nm or not _is_french_source(nm, ch.get('language')):
                continue
            links = []
            for l in (ch.get('links') or []):
                try:
                    l = six.ensure_text(l, encoding='utf-8', errors='replace')
                except Exception:
                    continue
                if 'id=' in l and l not in links:
                    links.append(l)
            if not links:
                continue
            cid = links[0].split('id=')[-1]
            e = chans.setdefault(cid, {'names': [], 'links': [],
                                       'live': False, 'matchs': []})
            for l in links:
                if l not in e['links']:
                    e['links'].append(l)
            if nm not in e['names']:
                e['names'].append(nm)
            e['live'] = e['live'] or live
            if (titre, ftime, live) not in e['matchs']:
                e['matchs'].append((titre, ftime, live))
    out = []
    for cid, e in chans.items():
        out.append({'id': cid,
                    'name': e['names'][0],
                    'aliases': e['names'][1:],
                    'links': e['links'],
                    'live': e['live'],
                    'matchs': e['matchs'][:5]})
    out.sort(key=lambda x: x['name'].lower())
    return out


def fr_channels_menu():
    chans = None
    try:
        if fastcache is not None:
            chans = fastcache.fget('frchan:list', _FRCH_TTL)
    except Exception:
        chans = None
    if not chans:
        try:
            chans = _fr_channels_from_guide()
        except Exception as e:
            log_info('fr channels failed: {0!r}'.format(e))
            chans = []
        try:
            if fastcache is not None and chans:
                fastcache.fset('frchan:list', chans)
                fastcache.flush()
        except Exception:
            pass
    if not chans:
        control_dialog_info(
            u"[COLOR gold]Aucune chaîne française dans la grille du moment "
            u"(réseau ?).[/COLOR]", 6000)
        xbmcplugin.endOfDirectory(_handle)
        return
    for c in chans:
        label = u'[B]{0}[/B]'.format(c['name'])
        if c.get('live'):
            label += u' [COLOR lime][B]● match en cours[/B][/COLOR]'
        label += u' [COLOR dimgray]· 24h/24[/COLOR]'
        plot = [u"Chaîne française diffusée en continu — regardable à tout "
                u"moment, même sans match."]
        if c.get('aliases'):
            plot.append(u'Aussi déclarée sous le nom : {0}.'
                        .format(u', '.join(c['aliases'])))
        if c.get('matchs'):
            ms = u', '.join(u'{0} ({1})'.format(t, f or u'?')
                            for (t, f, _lv) in c['matchs'])
            plot.append(u'Matchs du jour sur cette chaîne : {0}.'.format(ms))
        info = {'title': _plain(c['name']), 'plot': u'\n'.join(plot),
                'genre': u'Chaîne française'}
        addDir(label, c['links'][0], 'play_stream', ICON, c['name'],
               isFolder=False, infoLabels=info)
    log_info('chaînes FR : {0}'.format(len(chans)))
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


# ---------------------------------------------------------------------------
# MENU 3 — SOURCE D'APPOINT SportOnline (Ligue 2, matchs diffusés en FR).
# ---------------------------------------------------------------------------

def spo_events_menu():
    try:
        evs = site_sportonline.list_events()
    except Exception as e:
        log_info('sportonline failed: {0!r}'.format(e))
        evs = []
    fr = []
    for ev in evs or []:
        if not isinstance(ev, dict):
            continue
        if 'fr' in (ev.get('langs') or []):
            fr.append(ev)
    if not fr:
        control_dialog_info(
            u"[COLOR gold]Aucun match français chez la source d'appoint "
            u"pour l'instant (réseau ou grille vide).[/COLOR]", 6000)
        xbmcplugin.endOfDirectory(_handle)
        return
    now_ms = datetime.now().timestamp() * 1000
    for ev in fr:
        try:
            ftime = time_convert(int(ev.get('start_ms') or 0) // 1000)
        except Exception:
            ftime = u'-'
        st = ev.get('start_ms') or 0
        dur = (ev.get('duration_min') or 120) * 60 * 1000
        is_live = bool(st and st <= now_ms < st + dur)
        state = (u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]' if is_live else u'')
        chans = [s[0] for s in (ev.get('servers') or [])]
        label = u'[COLOR cyan]{0}[/COLOR] [COLOR gold][B]{1}[/B][/COLOR]{2}'.format(
            ftime, ev.get('title') or u'?', state)
        plot = [u"Source d'appoint — grille déclarée par la source "
                u"(langues fiables).",
                u'Chaînes : ' + u', '.join(chans) if chans else u'']
        payload = base64.b64encode(
            six.ensure_binary(str(ev.get('servers') or []))).decode('ascii')
        info = {'title': _plain(ev.get('title') or u''),
                'plot': u'\n'.join(p for p in plot if p),
                'genre': u"Source d'appoint"}
        addDir(label, payload, 'spo_streams', ICON, u'',
               isFolder=True, infoLabels=info)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def spo_streams_menu(payload):
    """Liste des canaux d'un match d'appoint : chaîne en or + langue.
    Payload b64 brut (le routeur a désencodé)."""
    try:
        data = six.ensure_text(base64.b64decode(payload)).strip('\n')
        import ast
        servers = ast.literal_eval(data)
    except Exception:
        servers = []
    sstreams = []
    for srv in servers or []:
        if not (isinstance(srv, (list, tuple)) and len(srv) >= 2):
            continue
        cname, curl = srv[0], srv[1]
        cname = six.ensure_text(cname, encoding='utf-8', errors='replace')
        curl = six.ensure_text(curl, encoding='utf-8', errors='replace')
        lang = u'fr' if 'fr' in _lsoc_srv_lang(cname) else u''
        sstreams.append((curl, _chan_label(cname, lang, bool(lang))))
    if not sstreams:
        control_dialog_info(u"[COLOR gold]Aucun canal disponible pour ce match.[/COLOR]",
                            5000)
        return
    for idx, (link, title) in enumerate(sstreams, 1):
        num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
        info = {'title': _plain(title), 'plot': _plain(u'Source d\'appoint')}
        addDir(num + title, link, 'spo_play', ICON, u'',
               isFolder=False, infoLabels=info)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def _lsoc_srv_lang(cname):
    """Langue déclarée par le nom de canal (« HD4 GREEK & FRENCH »)."""
    try:
        up = six.ensure_text(cname or u'', encoding='utf-8',
                             errors='replace').upper()
        return ['FRENCH' if 'FRENCH' in up else '']
    except Exception:
        return [u'']


def spo_play(name, canal_url):
    """Résolveur SportOnline : page canal -> iframe -> m3u8, puis lecture
    avec le Referer de l'iframe (éprouvé en 3.7.17/3.7.18)."""
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        res = site_sportonline.resolve(canal_url)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not res:
        raise Exception(u"Flux introuvable (source d'appoint) — réessayez.")
    fl, frame = res
    stream_headers = {'User-Agent': site_sportonline.UA,
                      'Referer': frame,
                      'verifypeer': 'false'}
    stream_url = xbmc_curl_encode(fl, stream_headers)
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name) or u'Match FR',
                                          encoding='utf-8', errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name) or u'Match FR'})
    liz.setProperty('IsPlayable', 'true')
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


# ---------------------------------------------------------------------------
# MENU 4 — CHAÎNES EN CLAIR (IPTV-org, playlist publique GitHub).
# ---------------------------------------------------------------------------

IPTV_SPORT_RE = re.compile(
    r'(?i)sport|foot|bein|canal\+|dazn|ligue|rmc\s|equipe|équipe')


def _iptv_entries():
    """(nom, url) des chaînes françaises sportives de la playlist publique.
    Parsing m3u simple ; dé-duplication par nom ; [] si réseau down."""
    ents = []
    try:
        raw = requests.get(IPTV_URL, headers={'User-Agent': _UA_CHROME},
                           timeout=12).text
    except Exception as e:
        log_info('iptv-org failed: {0!r}'.format(e))
        return []
    lines = raw.splitlines()
    seen = set()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith('#EXTINF') and ',' in ln:
            name = ln.split(',', 1)[1].strip()
            url = ''
            if i + 1 < len(lines) and not lines[i + 1].startswith('#'):
                url = lines[i + 1].strip()
                i += 2
            else:
                i += 1
            if url.startswith('http') and name and IPTV_SPORT_RE.search(name):
                if name not in seen:
                    seen.add(name)
                    ents.append((name, url))
        else:
            i += 1
    ents.sort(key=lambda x: x[0].lower())
    return ents


def _iptv_alive(url):
    """GET court : True si le manifest HLS répond avec de l'EXTM3U."""
    try:
        r = requests.get(url, headers={'User-Agent': _UA_CHROME}, timeout=6)
        return r.status_code == 200 and b'#EXT' in r.content[:400]
    except Exception:
        return False


def iptv_menu():
    ents = _iptv_entries()
    if not ents:
        control_dialog_info(
            u"[COLOR gold]Playlist IPTV-org indisponible (réseau ?).[/COLOR]",
            6000)
        xbmcplugin.endOfDirectory(_handle)
        return
    # test de vivacité en parallèle (cache 30 min) : pastille honnête par chaîne
    statuses = {}
    if fastcache is not None:
        statuses = fastcache.fget('iptv:alive', 1800) or {}
    todo = [u for _n, u in ents if u not in statuses]
    if todo:
        def _check(u):
            statuses[u] = _iptv_alive(u)
        try:
            if fastcache is not None:
                fastcache.parallel_map(_check, todo, workers=4)
                fastcache.fset('iptv:alive', statuses)
                fastcache.flush()
            else:
                for u in todo:
                    _check(u)
        except Exception:
            pass
    for name, url in ents:
        alive = statuses.get(url)
        dot = (u' [COLOR lime]● disponible[/COLOR]' if alive
               else u' [COLOR grey]· indisponible[/COLOR]' if alive is False
               else u'')
        label = u'[B]{0}[/B]{1} [COLOR dimgray]· en clair[/COLOR]'.format(name, dot)
        info = {'title': _plain(name),
                'plot': u"Chaîne française en clair (playlist publique "
                        u"IPTV-org) — flux direct, sans résolution.",
                'genre': u'Chaîne en clair'}
        addDir(label, url, 'iptv_play', ICON, name, isFolder=False,
               infoLabels=info)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def play_direct(name, url):
    """Lecture d'un m3u8 direct (chaînes en clair) avec un User-Agent propre."""
    stream_url = xbmc_curl_encode(url, {'User-Agent': _UA_CHROME})
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name), encoding='utf-8',
                                          errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name)})
    liz.setProperty('IsPlayable', 'true')
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


# ---------------------------------------------------------------------------
# MENU 5 — PLUS DE MATCHS (LiveSoccer, grille géo-IP française) : les matchs
# reconnus DANS la grille du guide sont jouables (association par noms
# d'équipes, les DEUX équipes doivent correspondre) ; les autres sont
# affichés en gris, sans lien inventé.
# ---------------------------------------------------------------------------

def _norm_tokens(s):
    """Tokens normalisés (minuscules, sans accents, >= 3 lettres)."""
    import unicodedata
    try:
        s = six.ensure_text(s or u'', encoding='utf-8', errors='replace')
    except Exception:
        return set()
    s = unicodedata.normalize('NFKD', s)
    s = u''.join(c for c in s if not unicodedata.combining(c)).lower()
    toks = re.findall(r'[a-z0-9]{3,}', s)
    stop = {'vs', 'fc', 'cf', 'ac', 'as', 'sc', 'the', 'and', 'club',
            'player', 'now', 'live', 'direct', 'stream', 'hd'}
    return set(t for t in toks if t not in stop)


def _lsoc_rows():
    """Lignes de matchs de la page LiveSoccer française (texte nettoyé)."""
    html = client.request(LSOC_URL, headers={'User-Agent': _UA_CHROME},
                          timeout='12')
    if not html:
        return []
    html = six.ensure_text(html, encoding='utf-8', errors='replace')
    rows = re.findall(r'<tr[^>]*class="[^"]*row[^"]*"[^>]*>(.*?)</tr>',
                      html, re.S)
    out = []
    for r in rows:
        txt = re.sub(r'<[^>]+>', ' ', r)
        txt = re.sub(r'\s+', ' ', txt).strip()
        if len(txt) >= 10:
            out.append(txt[:160])
    return out


def _lsoc_menu():
    rows = _lsoc_rows()
    if not rows:
        control_dialog_info(
            u"[COLOR gold]LiveSoccer indisponible pour le moment "
            u"(réseau ?).[/COLOR]", 6000)
        xbmcplugin.endOfDirectory(_handle)
        return
    matches = _guide_matches()
    guide = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        t1, t2 = m.get('team1'), m.get('team2')
        if t1 and t2:
            guide.append((m, _norm_tokens(t1), _norm_tokens(t2)))
    matched = 0
    for txt in rows:
        toks = _norm_tokens(txt)
        hit = None
        for (m, tk1, tk2) in guide:
            if tk1 and tk2 and tk1 <= toks and tk2 <= toks:
                hit = m
                break
        if hit:
            matched += 1
            channels = hit.get('channels') or []
            t1 = six.ensure_text(hit.get('team1'), encoding='utf-8',
                                 errors='replace')
            t2 = six.ensure_text(hit.get('team2'), encoding='utf-8',
                                 errors='replace')
            try:
                compare = int(hit['startTimestamp'])
                ftime = time_convert(compare)
            except Exception:
                compare = int('999999999999')
                ftime = u'-'
            now_ms = datetime.now().timestamp() * 1000
            is_live = bool(compare <= now_ms <= compare + (hit.get('duration') or 120) * 60000)
            state = (u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]' if is_live else u'')
            label = u'[COLOR cyan]{0}[/COLOR] [COLOR gold][B]{1} vs {2}[/B][/COLOR]{3}{4}'.format(
                ftime, t1, t2, state, _lang_suffix(_match_langs(channels)))
            streams = base64.b64encode(six.ensure_binary(str(channels))).decode('ascii')
            info = {'title': u'{0} vs {1}'.format(t1, t2),
                    'plot': u'Match identifié dans la grille du guide via '
                            u'LiveSoccer — chaînes françaises disponibles.',
                    'genre': u'Football'}
            addDir(label, streams, 'get_streams', ICON, u'', isFolder=True,
                   infoLabels=info)
        else:
            label = u'[COLOR dimgray]{0}[/COLOR]'.format(txt[:90])
            info = {'title': _plain(txt[:60]),
                    'plot': u'Grille LiveSoccer — aucun flux identifié dans '
                            u'le guide pour ce match (pas de lien inventé).',
                    'genre': u'Football'}
            addDir(label, '', 'no_link', ICON, u'', isFolder=False,
                   infoLabels=info)
    log_info('livesoccer : {0} lignes, {1} associees'.format(len(rows), matched))
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


# ---------------------------------------------------------------------------
# Liste des liens d'un match du guide + résolveur miroirs (éprouvés).
# ---------------------------------------------------------------------------

def get_stream(name, url):
    """Liste des liens (chaînes) d'un match, numérotés, miroir en gris.
    L'URL reçue du routeur est déjà désencodée : payload b64 brut."""
    try:
        data = six.ensure_text(base64.b64decode(url)).strip('\n')
        import ast
        events = ast.literal_eval(data)
    except Exception:
        events = []
    all_channels = _setting_bool('all_channels', 'false')
    sstreams = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        fr = _is_french_source(event.get('name'), event.get('language'))
        if not all_channels and not fr:
            continue
        chan = six.ensure_text(event.get('name', ''), encoding='utf-8',
                               errors='replace')
        lang = six.ensure_text(event.get('language', ''), encoding='utf-8',
                               errors='replace')
        for url_ in event.get('links', []):
            sstreams.append((url_, _chan_label(chan, lang, fr)))
    if len(sstreams) < 1:
        control_dialog_info(
            u"[COLOR gold]Aucune chaîne française pour ce match.[/COLOR]\n"
            u"[COLOR lime]Réglage : « Toutes les chaînes » dans les paramètres.[/COLOR]",
            7000)
        return
    if len(sstreams) > 1:
        for idx, i in enumerate(sstreams, 1):
            title, link = i[1], i[0]
            lbl = _link_label(link)
            if lbl and str(link) != str(title):
                title = u'{0} [COLOR dimgray]· {1}[/COLOR]'.format(title, lbl)
            num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
            info = {'title': _plain(title), 'plot': _plain(name)}
            addDir(num + title, link, 'play_stream', ICON, name,
                   isFolder=False, infoLabels=info)
        xbmcplugin.setContent(_handle, 'videos')
        xbmcplugin.endOfDirectory(_handle)
    else:
        resolve2(name, sstreams[0][0])


def xbmc_curl_encode(url, headers):
    return "{}|{}".format(url, urlencode(headers))


def _resolve2_impl(name, url):
    """Extrait l'URL finale du flux (miroirs vertex/nexa/kora avec repli
    automatique, lecteurs ?ppcfg=1 / char-array / window._econfig /
    PP_CLAPPR_CONFIG, cache api/player 30 min, salve parallèle)."""
    ua_win = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.120 Safari/537.36'
    log_info('RESOLVE-URL: {0}'.format(url))

    if not ('vertex' in url or 'nexa' in url or 'kora.st' in url
            or '//glisco' in url or '//sansat' in url):
        raise Exception(u'Lien hors résolveur : {0}'.format(url))

    control_dialog_info(u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", 2000)
    chan_id = url.split('id=')[-1]

    def _extract_stream(embed_html):
        # Plusieurs types de lecteurs ; retourne (méthode, url) — la méthode
        # reste renseignée même en échec pour diagnostiquer au log.
        # 1) ?ppcfg=1 JSON (dinamic-embed.site, tursatrueper.xyz/pink.php)
        try:
            cfg = json.loads(embed_html)
            fl = cfg.get('src') or cfg.get('srcBase')
            if fl:
                return ('ppcfg', fl)
        except Exception:
            pass
        # 2) character-array return([...].join("")) (forgemindly.com)
        arr_m = re.search(r'return\s*\(\s*(\[(?:"[^"]*",?\s*)+\])', embed_html)
        if arr_m:
            fl = ''.join(re.findall(r'"([^"]*)"', arr_m.group(1))).replace('\\/', '/')
            if fl:
                return ('char-array', fl)
        # 3) window._econfig blob (talentedlost.click / fisherman / wilderness)
        try:
            from resources.modules import econfig as _econfig
            if _econfig.find_econfig(embed_html):
                fl, _ = _econfig.extract_stream_from_html(embed_html)
                return ('econfig' if fl else 'econfig-blank', fl or None)
        except Exception:
            pass
        # 4) window.PP_CLAPPR_CONFIG (play.matchli.st clappr-classic.php)
        pp = re.search(r'window\.PP_CLAPPR_CONFIG\s*=\s*(\{.*?\});\s*</script>',
                       embed_html, re.DOTALL)
        if pp:
            try:
                cfg = json.loads(pp.group(1))
                fl = cfg.get('src') or cfg.get('srcBase')
                if fl:
                    return ('pp-clappr-config', fl)
            except Exception:
                pass
        return (None, None)

    # Le même canal est miroité (nexa.st, s1/s2/s3.nexa.st, vertex.st…)
    # et chaque miroir route vers un embed différent ; si l'un ne livre
    # rien, on essaie les autres.
    uri0 = urlparse(url)
    hosts = [uri0.netloc]
    m = re.match(r'^(?:s\d+\.)?(nexa\.st|vertex\.st|kora\.st)$', uri0.netloc)
    if m:
        base = m.group(1)
        for h in (base, 's2.' + base, 's1.' + base, 's3.' + base):
            if h not in hosts:
                hosts.append(h)

    # Cache 30 min des réponses api/player.php + salve parallèle (3.7.7).
    def _api_key(h):
        return 'gap:{0}:{1}'.format(h, chan_id)

    api_results = {}
    to_fetch = []
    for h in hosts:
        v = fastcache.fget(_api_key(h), 1800) if fastcache is not None else None
        if v:
            api_results[h] = v
        else:
            to_fetch.append(h)

    if to_fetch:
        def _grab(h):
            try:
                r = client.request(
                    '{0}://{1}/api/player.php?id={2}'.format(
                        uri0.scheme, h, chan_id),
                    referer='{0}://{1}/'.format(uri0.scheme, h), timeout='12')
                if r:
                    api_results[h] = six.ensure_text(
                        r, encoding='utf-8', errors='replace')
                    if fastcache is not None:
                        fastcache.fset(_api_key(h), api_results[h])
            except Exception:
                pass
        if fastcache is not None:
            fastcache.parallel_map(_grab, to_fetch, workers=4)
        else:
            for _h in to_fetch:
                _grab(_h)

    flink = None
    frame = None
    for host in hosts:
        try:
            api_ref = '{0}://{1}/'.format(uri0.scheme, host)
            api_resp = api_results.get(host)
            if not api_resp:
                api_resp = six.ensure_text(client.request(
                    '{0}://{1}/api/player.php?id={2}'.format(
                        uri0.scheme, host, chan_id),
                    referer=api_ref, timeout='12') or '')
            cand_frame = json.loads(api_resp)['url']
            cfg_url = cand_frame + ('&' if '?' in cand_frame else '?') \
                + 'ppcfg=1&_=' + str(int(time.time() * 1000))
            try:
                embed_html = six.ensure_str(
                    client.request(cfg_url, referer=api_ref,
                                   headers={'User-Agent': ua_win},
                                   timeout='12') or '')
            except Exception:
                embed_html = ''   # 403 sur ?ppcfg=1 -> page plain ci-dessous
            method, cand = _extract_stream(embed_html)
            if not cand:
                try:
                    method, cand = _extract_stream(six.ensure_str(
                        client.request(cand_frame, referer=api_ref,
                                       headers={'User-Agent': ua_win},
                                       timeout='12')))
                except Exception:
                    pass
            frame_host = urlparse(cand_frame).netloc
            if cand:
                flink = cand
                frame = cand_frame
                log_info('mirror {0} -> {1}: OK via {2}'.format(
                    host, frame_host, method))
                break
            log_info('mirror {0} -> {1}: no stream ({2})'.format(
                host, frame_host, method or 'unknown player'))
        except Exception as _me:
            log_info('mirror {0}: error {1!r}'.format(host, _me))
            continue
    if not flink:
        raise Exception('could not extract stream src for ' + url)

    frame_origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(frame))
    stream_headers = {
        'Referer': frame_origin + '/',
        'Origin': frame_origin,
        'User-Agent': ua_win,
        'verifypeer': 'false',
    }
    return xbmc_curl_encode(flink, stream_headers)


def resolve2(name, url):
    """Roue d'attente pendant la résolution, lecture avec titre propre et
    bon type MIME HLS (démarrage plus fiable)."""
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        stream_url = _resolve2_impl(name, url)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not stream_url:
        raise Exception(u'Flux introuvable pour ce lien — réessayez ou '
                        u'choisissez un autre serveur.')
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name), encoding='utf-8',
                                          errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name)})
    liz.setProperty('IsPlayable', 'true')
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


# ---------------------------------------------------------------------------
# Vue / navigation
# ---------------------------------------------------------------------------

_NOPLAY_MODES = ('settings', 'version', 'clear', 'play_stream', 'spo_play',
                 'iptv_play', 'no_link')


def addDir(name, url, mode, iconimage, description, isFolder=True,
           infoLabels=None, context=None):
    if isinstance(name, str):
        name = six.ensure_text(name)
    if isinstance(url, str):
        url = six.ensure_text(url)
    if isinstance(description, str):
        description = six.ensure_text(description)
    if isinstance(iconimage, str):
        iconimage = six.ensure_text(iconimage)

    url_encoded = quote_plus(url.encode('utf-8') if isinstance(url, six.text_type) else url)
    name_encoded = quote_plus(name.encode('utf-8') if isinstance(name, six.text_type) else name)
    iconimage_encoded = quote_plus(iconimage.encode('utf-8') if isinstance(iconimage, six.text_type) else iconimage)
    description_encoded = quote_plus(
        description.encode('utf-8') if isinstance(description, six.text_type) else description)

    u = sys.argv[0] + "?url=" + url_encoded + "&mode=" + str(
        mode) + "&name=" + name_encoded + "&iconimage=" + iconimage_encoded \
        + "&description=" + description_encoded

    liz = xbmcgui.ListItem(six.ensure_str(name, encoding='utf-8',
                                          errors='replace'))
    liz.setArt({'icon': iconimage, 'thumb': iconimage, 'poster': iconimage,
                'fanart': FANART})
    if infoLabels:
        liz.setInfo(type="Video", infoLabels=infoLabels)
    if context:
        try:
            liz.addContextMenuItems(context)
        except Exception:
            pass
    if not isFolder:
        if mode in _NOPLAY_MODES:
            isFolder = False
        else:
            liz.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(_handle, url=u, listitem=liz,
                                isFolder=isFolder)


def Main_menu():
    """Accueil : la grille se préchauffe en arrière-plan (premier clic
    instantané), puis les 5 rubriques + services."""
    _refresh_matches_bg()
    addDir(u'[B][COLOR white]MATCHS FR DU JOUR[/COLOR][/B]', 'guide', 'events',
           ICON, FANART, True,
           infoLabels={'title': u'Matchs français du jour',
                       'plot': u'Football (et autres sports selon réglage) '
                               u'avec chaîne française — matchs en cours et '
                               u'à venir.'})
    addDir(u'[B][COLOR white]PAR CHAÎNE FR[/COLOR][/B] [COLOR dimgray](24h/24)[/COLOR]',
           'frch', 'fr_channels', ICON, FANART, True,
           infoLabels={'title': u'Chaînes françaises',
                       'plot': u'Chaînes françaises du guide diffusées en '
                               u'continu — regardables à tout moment, même '
                               u'sans match (Canal+ Foot…).'})
    if _setting_bool('spo_enabled', 'true'):
        addDir(u'[B][COLOR gold]PLUS DE MATCHS[/COLOR][/B] [COLOR dimgray](SportOnline — Ligue 2…)[/COLOR]',
               'spo', 'spo_events', ICON, FANART, True,
               infoLabels={'title': u"Source d'appoint",
                           'plot': u'Grille d\'appoint avec langues déclarées : '
                                   u'Ligue 2 et matchs diffusés en français '
                                   u'absents du guide principal.'})
    if _setting_bool('iptv_enabled', 'true'):
        addDir(u'[B][COLOR deepskyblue]CHAÎNES EN CLAIR[/COLOR][/B] [COLOR dimgray](IPTV-org)[/COLOR]',
               'iptv', 'iptv', ICON, FANART, True,
               infoLabels={'title': u'Chaînes en clair',
                           'plot': u'Playlist publique IPTV-org : chaînes '
                                   u'françaises en clair, flux directs m3u8.'})
    if _setting_bool('lsoc_enabled', 'true'):
        addDir(u'[B][COLOR deepskyblue]PLUS DE MATCHS[/COLOR][/B] [COLOR dimgray](LiveSoccer)[/COLOR]',
               'lsoc', 'lsoc', ICON, FANART, True,
               infoLabels={'title': u'Grille LiveSoccer',
                           'plot': u'Grille française du jour (géo-IP) — les '
                                   u'matchs reconnus dans le guide sont jouables.'})
    addDir(u'[B][COLOR gold]Paramètres[/COLOR][/B]', 'set', 'settings', ICON,
           FANART, False,
           infoLabels={'title': u'Paramètres',
                       'plot': u'Sources, football seulement, toutes les '
                               u'chaînes.'})
    addDir(u"[B][COLOR gold]Vider les données de l'add-on[/COLOR][/B]",
           'clear', 'clear', ICON, FANART, False,
           infoLabels={'title': u"Vider les données de l'add-on",
                       'plot': u'Supprime le cache (grille, lecteurs, test '
                               u'des chaînes en clair).'})
    addDir(u'[B][COLOR gold]Version : [COLOR lime]{}[/COLOR][/B]'.format(vers),
           '', 'version', ICON, FANART, False,
           infoLabels={'title': u'Version ' + six.ensure_text(
               vers, encoding='utf-8', errors='replace'),
               'plot': u'Foot FR — add-on séparé de SportHD ; vérifiez les '
                       u'mises à jour du dépôt.'})
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def Open_settings():
    ADDON.openSettings()


def clear_data():
    if fastcache is not None:
        fastcache.clear()
    control_dialog_info(u"[COLOR lime]Données vidées.[/COLOR]", 4000)


def router(paramstring):
    # Kodi passe la query TELLE QUELLE (encodée) : addDir a encodé chaque
    # champ avec quote_plus -> on désencode ICI, une seule fois, et toutes
    # les fonctions reçoivent les valeurs brutes (payload b64, URL de flux,
    # nom de match).
    params = {}
    for pair in (paramstring.split('&') if paramstring else []):
        if '=' not in pair:
            continue
        k, v = pair.split('=', 1)
        try:
            params[k] = unquote_plus(v)
        except Exception:
            params[k] = v
    mode = params.get('mode', None)
    log_info('mode: {0}, url: {1}'.format(mode, params.get('url', '')))
    if mode is None:
        Main_menu()
    elif mode == 'events':
        matches_menu()
    elif mode == 'refresh_events':
        refresh_events()
    elif mode == 'fr_channels':
        fr_channels_menu()
    elif mode == 'spo_events':
        spo_events_menu()
    elif mode == 'spo_streams':
        spo_streams_menu(params.get('url', ''))
    elif mode == 'spo_play':
        spo_play(params.get('description') or u'Match FR',
                 params.get('url', ''))
    elif mode == 'iptv':
        iptv_menu()
    elif mode == 'iptv_play':
        play_direct(params.get('description') or u'Chaîne',
                    params.get('url', ''))
    elif mode == 'lsoc':
        _lsoc_menu()
    elif mode == 'get_streams':
        get_stream(params.get('name', u''), params.get('url', ''))
    elif mode == 'play_stream':
        resolve2(params.get('description', u''), params.get('url', ''))
    elif mode == 'no_link':
        control_dialog_info(
            u"[COLOR gold]Aucun flux identifié pour ce match dans le guide.[/COLOR]",
            5000)
    elif mode == 'settings':
        Open_settings()
    elif mode == 'clear':
        clear_data()
    elif mode == 'version':
        control_dialog_info(
            u'[COLOR lime]Foot FR v{0}[/COLOR] — vérifiez les mises à jour '
            u'du dépôt.'.format(vers), 5000)
    else:
        Main_menu()


if __name__ == '__main__':
    try:
        router(sys.argv[2][1:])
    except Exception as e:
        log_error('router: {0}'.format(e))
        try:
            snippet = six.ensure_text(str(e), encoding='utf-8',
                                      errors='replace')
            if len(snippet) > 180:
                snippet = snippet[:180] + u'...'
            control_dialog_info(
                u'[COLOR red]Impossible de lire ce flux.[/COLOR] '
                u'[COLOR dimgray]Réessayez ou choisissez un autre lien '
                u'({0})[/COLOR]'.format(snippet), 6000)
        except Exception:
            pass
