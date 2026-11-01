# -*- coding: utf-8 -*-
from __future__ import print_function
import xbmcvfs
import base64
import re
import sys
import six
from six.moves.urllib.parse import urljoin, quote_plus, quote, unquote, parse_qsl, urlencode, urlparse
from datetime import datetime, timedelta
import json
import threading
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import requests
from resources.modules import control, client
from resources.modules import site_embedlivesports
from resources.modules import site_futbollibre
from resources.modules import site_futbolmov
from resources.modules import site_vavoo
from resources.modules import site_livewatch
import time
from dateutil.parser import parse
from dateutil.tz import gettz
from dateutil import parser, tz

# Extra sports sites: one self-contained module each. To add a site, drop a
# module exposing NAME/KEY/list_events()/resolve() and append it here.
# tvsport.guide is no longer a menu entry: it is the fallback schedule for
# ÉVÉNEMENTS EN DIRECT and Événements en direct (see Live_fallback_url).
EXTRA_SITES = [site_embedlivesports, site_futbollibre]
SITES = {s.KEY: s for s in EXTRA_SITES}
# LiveWatch n'est pas une rubrique du menu : c'est la porte de secours
# pour les matchs SANS AUCUNE source française (3 liens « Lien 1/2/3 »
# à résolutions différentes quand le site l'annonce, pré-résolus en
# arrière-plan pour s'ouvrir vite). Les chaînes françaises utilisées
# sont les mêmes que celles du moteur Vavoo. Registré ici uniquement
# pour que site_play puisse appeler son resolve().
SITES[site_livewatch.KEY] = site_livewatch
# futbol-libre.mov: one main-menu entry -> its three lists (fmov_menu)
SITES.update((s.KEY, s) for s in site_futbolmov.SUBSITES)
# Vavoo French channels: one main-menu entry -> its two lists (vavoo_menu)
SITES.update((s.KEY, s) for s in site_vavoo.SUBSITES)

_url = sys.argv[0]
_handle = int(sys.argv[1])

ADDON = xbmcaddon.Addon()
ADDON_DATA = ADDON.getAddonInfo('profile')
ADDON_PATH = ADDON.getAddonInfo('path')
DESCRIPTION = ADDON.getAddonInfo('description')
FANART = ADDON.getAddonInfo('fanart')
ICON = ADDON.getAddonInfo('icon')
ID = ADDON.getAddonInfo('id')
NAME = ADDON.getAddonInfo('name')
VERSION = ADDON.getAddonInfo('version')
Lang = ADDON.getLocalizedString
Dialog = xbmcgui.Dialog()
vers = VERSION
ART = ADDON_PATH + "/resources/icons/"

BASEURL = 'https://one.sporthd.me/'  # 'https://sporthd.live/'  #'https://sportl.ivesoccer.sx/'
Live_url = 'https://league.st'  # super.league.st died Sep 2026; bare league.st is back

# Agendas « rapides » (Futbol Libre + Futbol Libre Mov) : les mêmes liens
# que leurs menus, préchargés en arrière-plan et croisés avec les matchs
# sans source française — proposés AVANT les chaînes Vavoo (plus rapides).
AGENDA_TTL = 10 * 60.0
_agenda_cache = {'ts': 0.0, 'events': [], 'thread': None}
Live_fallback_url = 'https://tvsport.guide/proxy.php?source=index'  # same matches schema, JSON
Alt_url = 'https://liveon.sx/program'  # 'https://1.livesoccer.sx/program'
headers = {'User-Agent': client.agent(),
           'Referer': BASEURL}

# Kodi 19+ prefers LOGINFO; Kodi 18 still has LOGNOTICE — same idea: visible in kodi.log
# without turning on full "debug logging".
_LOG_INFO = getattr(xbmc, 'LOGINFO', None)
if _LOG_INFO is None:
    _LOG_INFO = getattr(xbmc, 'LOGNOTICE', xbmc.LOGDEBUG)


def _log_line(text):
    """Prefix addon name; safe str for xbmc.log on Py2/Py3."""
    try:
        line = u'[{0}] {1}'.format(NAME, text)
        return six.ensure_str(line, encoding='utf-8', errors='replace')
    except Exception:
        return six.ensure_str(text, encoding='utf-8', errors='replace')


def log_debug(msg):
    xbmc.log(_log_line(msg), xbmc.LOGDEBUG)


def log_info(msg):
    """Like old ``xbmc.log(msg)`` one-arg — shows in normal kodi.log."""
    xbmc.log(_log_line(msg), _LOG_INFO)


def log_warning(msg):
    xbmc.log(_log_line(msg), xbmc.LOGWARNING)


def log_error(msg):
    xbmc.log(_log_line(msg), xbmc.LOGERROR)


def Main_menu():
    # addDir('[B][COLOR gold]Channels 24/7[/COLOR][/B]', 'https://1.livesoccer.sx/program.php', 14, ICON, FANART, '')
    addDir(u'[B][COLOR white]ÉVÉNEMENTS EN DIRECT[/COLOR][/B]', Live_url, 'events', ICON, FANART, True)
    for _s in EXTRA_SITES:
        addDir('[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(_s.NAME),
               _s.KEY, 'site_events', ICON, FANART, True,
               infoLabels={'title': _s.NAME, 'plot': getattr(_s, 'DESC', '')})
    addDir('[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(site_futbolmov.NAME),
           'fmov', 'fmov_menu', ICON, FANART, True,
           infoLabels={'title': site_futbolmov.NAME, 'plot': site_futbolmov.DESC})
    addDir('[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(site_vavoo.NAME),
           'vavoo', 'vavoo_menu', ICON, FANART, True,
           infoLabels={'title': site_vavoo.NAME, 'plot': site_vavoo.DESC})
    # addDir('[B][COLOR gold]Alternative VIEW [/COLOR][/B]', '', '', ICON, FANART, '')
    # addDir('[B][COLOR gold]Alternative LIVE EVENTS[/COLOR][/B]', Alt_url, 15, ICON, FANART, '')
    # addDir('[B][COLOR white]SPORTS[/COLOR][/B]', '', 3, ICON, FANART, '')
    # addDir('[B][COLOR white]BEST LEAGUES[/COLOR][/B]', '', 2, ICON, FANART, '')
    addDir(u'[B][COLOR gold]Paramètres[/COLOR][/B]', 'set', 'settings', ICON, FANART, False)
    addDir(u"[B][COLOR gold]Vider les données de l'add-on[/COLOR][/B]", 'clear', 'clear', ICON, FANART, False)
    addDir(u'[B][COLOR gold]Version : [COLOR lime]{}[/COLOR][/B]'.format(vers), '', 'version', ICON, FANART, False)
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _matches_from_page(url):
    data = requests.get(url, timeout=20).text
    data = six.ensure_text(data, encoding='utf-8', errors='ignore')
    data = re.sub('\t', '', data).replace('&nbsp', '')

    events = client.parseDOM(data, 'script')
    events = [i for i in events if '''matchDate''' in i][0]

    new_matches = re.findall(r'window\.matches\s*=\s*JSON\.parse\(`(\[.+?\])`\)', events, re.DOTALL)
    if new_matches:
        return json.loads(new_matches[0])
    events = events[:-1].replace('self.__next_f.push(', '').replace('\\', '')
    old_pattern = r'"matches"\s*\:\s*(\[.+?])}]]}]n'
    return json.loads(re.findall(old_pattern, events.replace(',false', ''), re.DOTALL)[0])


def get_events(url):  # 5
    # préchauffage Vavoo (catalogue + grille TV en arrière-plan) et des
    # agendas rapides : pendant que l'utilisateur parcourt les matchs, les
    # caches se remplissent et les sources de dépannage d'un match sans
    # source française s'afficheront sans attente au clic.
    try:
        site_vavoo.warmup()
        site_livewatch.warmup()
        _agenda_warm()
    except Exception:
        pass
    matches = None
    if url != Live_fallback_url:
        try:
            matches = _matches_from_page(url)
        except Exception as e:
            log_info('events page {0} failed: {1!r}'.format(url, e))
    if not matches:
        # fallback: same schema served as plain JSON by tvsport.guide
        try:
            matches = requests.get(Live_fallback_url, headers={'User-Agent': client.agent()},
                                   timeout=20).json().get('matches')
        except Exception as e:
            log_info('events fallback {0} failed: {1!r}'.format(Live_fallback_url, e))
    if not matches:
        control.infoDialog(u"[COLOR red]Aucun match programmé.[/COLOR]", NAME, ICON, 5000)
        return

    # matches = re.findall('''null\,(\{"(?:matches|customNotFoundMessage).+?)\]\}\]n''', events, re.DOTALL)[0]
    # pattern = r'("matches"\s*\:\s*\[.+?])}]}]n"'
    # pattern = r'"matches"\s*\:\s*(\[.+?])}]]}]n'
    # matches = re.findall(pattern, events.replace(',false', ''), re.DOTALL)[0]
    # xbmc.log('EVENTSSS: {}'.format(matches))
    # matches = json.loads(matches)

    event_list = []

    if six.PY2:
        now = datetime.now()
        now_time_in_ms = (time.mktime(now.timetuple()) + now.microsecond / 1e6) * 1000
    else:
        now_time_in_ms = datetime.now().timestamp() * 1000
    fr_only = _french_only()
    vavoo_fb = _vavoo_fallback()
    filtered_out = 0
    for match in matches:
        channels = match.get("channels", [])
        # match sans chaîne française : masqué, sauf si le rattachement
        # Vavoo est actif (il recevra des chaînes Vavoo de dépannage)
        no_fr = fr_only and not _channels_have_french(channels)
        if no_fr and not vavoo_fb:
            filtered_out += 1
            continue
        # links = match['additionalLinks']
        # links.extend(match['channels'])
        icon = match.get('team1Img', ICON)
        sport = match.get('sport', '')
        lname = match.get('league', None)
        lname = six.ensure_text(lname, encoding='utf-8', errors='replace') if lname else sport
        team1 = match.get('team1', None)
        team1 = six.ensure_text(team1, encoding='utf-8', errors='replace') if team1 else None
        team2 = match.get('team2', None)
        team2 = six.ensure_text(team2, encoding='utf-8', errors='replace') if team2 else None
        event = u"{} vs {}".format(team1, team2) if team1 and team2 else team1

        try:
            compare = match['startTimestamp']
            ftime = time_convert(compare)
        except Exception:
            try:
                matchdt = match['matchDate']
                tvtime = match['livetvtimestr']
                compare = adjust_date_and_convert_to_timestamp_ms(matchdt, tvtime)
                ftime = time_convert(compare)
                # xbmc.log('SHOW FTIME: {}'.format(ftime))
            except Exception:
                compare = int('999999999999')
                ftime = '-'

        duration_in_ms = match['duration'] * 60 * 1000

        is_live = False
        if compare <= now_time_in_ms <= compare + duration_in_ms:
            is_live = True

        m_color = "lime" if is_live else "gold"
        ftime = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        name = u'{0} [COLOR {1}]{2}[/COLOR] - [I]{3}[/I]'.format(ftime, m_color, event, lname)
        if no_fr:  # conservé grâce au rattachement Vavoo -> balise visible
            name += u' [COLOR gold][Vavoo][/COLOR]'
        event_list.append([name, compare, channels, icon])

        # streams = str(quote(base64.b64encode(six.ensure_binary(str(streams)))))
    if not event_list:
        if filtered_out:
            control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs du moment.[/COLOR]", NAME, ICON, 6000)
        return
    events = sorted(event_list, key=lambda x: x[1])
    for event in deduplicate_events(events):
        streams = str(quote(base64.b64encode(six.ensure_binary(str(event[2])))))
        name = event[0]
        icon = event[3]
        addDir(name, streams, 'get_streams', icon, FANART, isFolder=True)

    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def deduplicate_events(events):
    unique_events = []
    for event in events:
        unique_key = (event[0], event[1], tuple(event[2]), event[3])
        if unique_key not in unique_events:
            unique_events.append(unique_key)
    return unique_events


# ---------------------------------------------------------------------------
# Filtrage « sources françaises » (réglage french_only, activé par défaut).
# Le site marque chaque chaîne d'un match avec un code pays (language='FR',
# 'GB', 'ES'...) : on garde uniquement les chaînes françaises ; les matchs
# sans aucune chaîne française disparaissent de la liste.
# ---------------------------------------------------------------------------
FRENCH_LANGS = ('fr', 'french', u'français', 'francais')
FRENCH_NAME_RE = re.compile(
    r'\bfr\b|\[fr\]|\(fr\)|french|fran[cç]ais|\bfrance\b|canal\+\s*foot|rmc\s*sport|'
    r'quipe|tf1\b|\bm6\b|\bw9\b|\btmc\b|ligue1')


def _french_only():
    """Réglage « Uniquement les sources françaises » (activé par défaut)."""
    try:
        return (ADDON.getSetting('french_only') or 'true').lower() == 'true'
    except Exception:
        return True


def _is_french_source(name, lang):
    """True si la chaîne/le serveur est en français (code pays, langue ou nom)."""
    try:
        lang_s = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip().lower()
        name_s = six.ensure_text(name or u'', encoding='utf-8', errors='replace').lower()
    except Exception:
        return False
    if lang_s in FRENCH_LANGS:
        return True
    return bool(name_s and FRENCH_NAME_RE.search(name_s))


def _db_channel(channel_name):
    """Cherche une chaîne par nom dans la base locale (channels.json)."""
    try:
        wanted = channel_name.replace(' ', '')
        for channel in load_data_from_json():
            if channel.get('channelName', '').replace(' ', '') == wanted:
                return channel
    except Exception:
        return None
    return None


def _channels_have_french(channels):
    """True si le match a au moins une chaîne française. Les chaînes
    indéterminées (base absente, nom inconnu) ne masquent pas le match :
    le filtrage fin se fait à l'ouverture du match."""
    identified = False
    for ch in channels:
        if isinstance(ch, dict):
            identified = True
            if _is_french_source(ch.get('name'), ch.get('language')):
                return True
        else:
            row = _db_channel(six.ensure_text(ch, encoding='utf-8', errors='replace'))
            if row is not None:
                identified = True
                if _is_french_source(row.get('channelName'), row.get('language')):
                    return True
    return not identified


def _servers_have_french(servers):
    """True si au moins un serveur (sites annexes) est en français."""
    for srv in servers or []:
        if isinstance(srv, dict):
            if _is_french_source(srv.get('name'), srv.get('language')):
                return True
        elif isinstance(srv, (list, tuple)) and srv:
            if _is_french_source(srv[0], ''):
                return True
        elif isinstance(srv, six.string_types):
            if _is_french_source(srv, ''):
                return True
    return False


def _vavoo_fallback():
    """Réglage « Rattacher les chaînes Vavoo aux matchs sans source
    française » (activé par défaut) : ces matchs restent affichés, avec
    une balise [Vavoo], et leur menu de sources propose des chaînes
    Vavoo françaises de dépannage."""
    try:
        return (ADDON.getSetting('vavoo_fallback') or 'true').lower() == 'true'
    except Exception:
        return True


def _clean_label(label):
    """Retire les codes couleur/balises Kodi d'un libellé de match."""
    return re.sub(r'\[/?(?:COLOR[^\]]*|B|I)\]', u'', six.ensure_text(
        label or u'', encoding='utf-8', errors='replace')).strip()


def _fast_agendas():
    """Agenda consolidé fllibre + fmov (cache mémoire AGENDA_TTL)."""
    now = time.time()
    if _agenda_cache['events'] and now - _agenda_cache['ts'] < AGENDA_TTL:
        return _agenda_cache['events']
    events = []
    for site in [site_futbollibre] + list(site_futbolmov.SUBSITES):
        try:
            for e in site.list_events():
                e['_k'] = site.KEY
                events.append(e)
        except Exception as exc:
            log_info('agenda {0} failed: {1!r}'.format(site.KEY, exc))
    if events:
        _agenda_cache['ts'] = now
        _agenda_cache['events'] = events
    return _agenda_cache['events']


def _agenda_warm():
    """Précharge les agendas rapides en arrière-plan (une fois à la fois)."""
    try:
        t = _agenda_cache['thread']
        if t is not None and t.is_alive():
            return
    except Exception:
        return

    def _run():
        try:
            _fast_agendas()
        except Exception:
            pass

    try:
        th = threading.Thread(target=_run)
        th.daemon = True
        _agenda_cache['thread'] = th
        th.start()
    except Exception:
        pass


def _agenda_ready():
    """True si les agendas rapides sont déjà en cache mémoire."""
    try:
        return bool(_agenda_cache['events']) \
            and time.time() - _agenda_cache['ts'] < AGENDA_TTL
    except Exception:
        return False


def _team_tokens(text):
    """Mots significatifs d'un titre de match, pour le croisement.

    Les accents sont repliés (ALAVÉS -> ALAVES, FRANÇAIS -> FRANCAIS)
    pour que les titres espagnols/latins des agendas rapides rejoignent
    les libellés anglais des événements.
    """
    try:
        import unicodedata
        txt = six.ensure_text(text or u'', encoding='utf-8',
                              errors='replace').upper()
        txt = unicodedata.normalize('NFKD', txt)
        txt = u''.join(c for c in txt if not unicodedata.combining(c))
    except Exception:
        txt = six.ensure_text(text or u'', encoding='utf-8',
                              errors='replace').upper()
    txt = re.sub(r'[^A-Z0-9 ]', ' ', txt)
    return set(w for w in txt.split()
               if len(w) > 3 and w not in (u'VS', u'THE', u'LIVE',
                                           u'STREAM', u'DIRECT', u'EN'))


def _match_fast_events(title, limit=4):
    """Événements des agendas rapides (fllibre/fmov) portant ce match.

    Croisement par mots d'équipes : au moins 2 mots communs (ou l'unique
    mot significatif). Le même match vu sur plusieurs sites ne garde que
    la liste avec le plus de serveurs.
    """
    need = _team_tokens(title)
    if not need:
        return []
    best = {}
    for e in _fast_agendas():
        shared = need & _team_tokens(e['title'])
        if len(shared) < min(2, len(need)):
            continue
        key = e['title'].strip().lower()   # dédoublonnage inter-sites
        cur = best.get(key)
        if cur is None or len(e.get('servers') or []) > len(cur.get('servers') or []):
            best[key] = e
    out = sorted(best.values(),
                 key=lambda e: -len(e.get('servers') or []))
    return out[:limit]


def _add_vavoo_fallback(match_label, poster=ICON):
    """Propose les sources de dépannage d'un match sans source française :
    d'abord les liens rapides (agendas Futbol Libre / Futbol Libre Mov
    qui portent le même match), puis les chaînes Vavoo françaises.
    True si au moins une source a été ajoutée à la liste."""
    if not _vavoo_fallback():
        return False
    title = _clean_label(match_label)
    try:
        warm = _agenda_ready() and site_vavoo.is_warm()
    except Exception:
        warm = False
    if not warm:
        # agendas ou caches Vavoo pas encore prêts : dire à l'utilisateur
        # pourquoi la liste tarde un instant (normalement < quelques s)
        Dialog.notification(NAME, u'[COLOR gold]Recherche de sources de dépannage…[/COLOR]',
                            ICON, 4000, False)
    added = 0
    # 1) sources rapides : le même match dans les agendas fllibre/fmov
    try:
        fast = _match_fast_events(title)
    except Exception as exc:
        log_info('fast agenda match failed: {0!r}'.format(exc))
        fast = []
    for e in fast:
        try:
            etitle = six.ensure_text(e['title'], encoding='utf-8', errors='replace')
            servers = e.get('servers') or []
            if not servers:
                continue
            payload = json.dumps({'k': e['_k'], 's': servers,
                                  't': etitle, 'p': e.get('poster') or '',
                                  'nf': 1})
            label_ = u'[B][COLOR lime]{0}[/COLOR][/B] [COLOR gray]· rapide[/COLOR]'.format(etitle)
            info = {'title': u'{0} | {1}'.format(title, etitle),
                    'plot': u'{0} — source rapide « {1} » : {2} serveur(s) à '
                            u'choisir. Mêmes liens que les menus Futbol '
                            u'Libre / Futbol Libre Mov, proposés en premier '
                            u'car plus rapides que Vavoo.'.format(
                                title, etitle, len(servers))}
            addDir(label_, payload, 'site_streams', e.get('poster') or poster,
                   title, isFolder=True, infoLabels=info)
            added += 1
        except Exception as exc:
            log_info('fast agenda item failed: {0!r}'.format(exc))
    # 2) chaînes Vavoo françaises (moteur grille TV + heuristiques)
    try:
        chans = site_vavoo.fallback_channels(title)
    except Exception as e:
        log_info('vavoo fallback failed: {0!r}'.format(e))
        chans = []
    for ch in chans:
        surl = (ch.get('urls') or [''])[0]
        if not surl:
            continue
        payload = json.dumps({'k': site_vavoo.SUBSITES[0].KEY, 'u': surl,
                              'alt': ch['urls'][1:],
                              't': title, 'p': ch.get('logo') or ''})
        # horaire du programme trouvé dans la grille TV, s'il y en a un
        st, sp = ch.get('epg_start') or 0, ch.get('epg_stop') or 0
        epg_tag = u''
        if st and sp:
            now_s = time.time()
            if st <= now_s <= sp:
                epg_tag = u'en direct'
            else:
                epg_tag = u'\u00e0 {0}'.format(six.ensure_text(
                    datetime.fromtimestamp(st).strftime('%H:%M'),
                    encoding='utf-8', errors='replace'))
        epg_title = six.ensure_text(ch.get('epg_title') or u'',
                                    encoding='utf-8', errors='replace')
        if epg_title:
            info = {'title': u'{0} | {1} (Vavoo)'.format(title, ch['title']),
                    'plot': u'{0} — {1} via Vavoo. Grille TV {2} : \u00ab {3} \u00bb '
                            u"(l'horaire peut changer, le match n'est pas "
                            u"garanti sur cette cha\u00eene).".format(
                                title, ch['title'], epg_tag or u'(programme)',
                                epg_title)}
        else:
            info = {'title': u'{0} | {1} (Vavoo)'.format(title, ch['title']),
                    'plot': u"{0} — {1} via Vavoo : cha\u00eene fran\u00e7aise propos\u00e9e "
                            u"en d\u00e9pannage (le match n'est pas garanti sur "
                            u"cette cha\u00eene).".format(title, ch['title'])}
        label_ = u'[B][COLOR gold]{0}[/COLOR][/B]'.format(ch['title'])
        if epg_tag:
            label_ += u' [COLOR cyan]\u00b7 {0}[/COLOR]'.format(epg_tag)
        label_ += u' [COLOR gray]\u00b7 Vavoo[/COLOR]'
        addDir(label_, payload, 'site_play',
               ch.get('logo') or poster, title, isFolder=False,
               infoLabels=info)
    return bool(added or chans)


# Les trois miroirs du service principal sont affichés discrètement
# « un », « deux », « trois » au lieu des domaines réels.
_MIRROR_LABELS = ((r'vertex\.st', u'un'), (r'nexa\.st', u'deux'), (r'kora\.st', u'trois'))


def _link_label(link):
    """Libellé affiché après le nom de la chaîne : « un / deux / trois »
    pour les miroirs vertex.st / nexa.st / kora.st (sous-domaines inclus,
    ex. s1.nexa.st), sinon le domaine réel."""
    host = (urlparse(link).netloc or '').lower()
    for rx, lbl in _MIRROR_LABELS:
        if re.search(r'(?:^|\.)' + rx + '$', host):
            return lbl
    return host


def get_stream(name, url):  # 4
    data = six.ensure_text(base64.b64decode(unquote(url))).strip('\n')

    import ast
    fr_only = _french_only()
    sstreams = []
    for event in ast.literal_eval(data):
        if not isinstance(event, dict):  # TNT Sports 1
            datos = get_links_for_channel(event) or []
            # xbmc.log('SHOW DATOS: {}'.format(datos))
            for chan, link, lang in datos:
                chan = six.ensure_text(chan, encoding='utf-8', errors='replace')
                lang = six.ensure_text(lang, encoding='utf-8', errors='replace')
                if fr_only and not _is_french_source(chan, lang):
                    continue
                chan = u'[COLOR gold]{}[/COLOR] - {}'.format(chan, lang)
                sstreams.append((link, chan))

        else:
            if fr_only and not _is_french_source(event.get('name'), event.get('language')):
                continue
            link = event.get('links', [])
            lang = six.ensure_text(event.get('language', ''), encoding='utf-8', errors='replace')
            chan = six.ensure_text(event.get('name', ''), encoding='utf-8', errors='replace')
            chan = u'[COLOR gold]{}[/COLOR] - {}'.format(chan, lang)
            for url_ in link:
                sstreams.append((url_, chan))

    if len(sstreams) < 1:
        # rattachement Vavoo : chaînes françaises de dépannage proposées
        # pour ce match quand il n'a aucune source française
        fb = _add_vavoo_fallback(name) if fr_only else False
        if fb:
            control.infoDialog(u'[COLOR gold]Aucune source française native — '
                               u'sources de dépannage proposées '
                               u'(rapides d\'abord, puis Vavoo).[/COLOR]',
                               NAME, ICON, 6000)
        elif fr_only:
            control.infoDialog(u"[COLOR gold]Aucune source française pour ce match.[/COLOR]\n"
                               u"[COLOR lime]Réglage : « Uniquement les sources françaises » dans les paramètres.[/COLOR]",
                               NAME, ICON, 7000)
        else:
            control.infoDialog(u"[COLOR gold]Aucun lien disponible pour le moment.\n "
                               u"[COLOR lime]Réessayez plus tard ![/COLOR]", NAME, ICON, 5000)
        xbmcplugin.setContent(_handle, 'videos')
        xbmcplugin.endOfDirectory(_handle)
        return
    else:
        if len(sstreams) > 1:
            for i in sstreams:
                title, link = i[1], i[0]
                if not 'vecdn' in link:
                    # if not 'https://bedsport' in link and not 'vecdn' in link:
                    if not str(link) == str(title):
                        title += ' | {}'.format(_link_label(link))
                info = {'title': title, 'sorttitle': '', 'plot': name}
                addDir(title, link, 'play_stream', ICON, name, isFolder=False, infoLabels=info)
            xbmcplugin.setContent(_handle, 'videos')
            xbmcplugin.endOfDirectory(_handle)

        else:
            link = sstreams[0][0]
            resolve2(name, link)


def xbmc_curl_encode(url, headers):
    return "{}|{}".format(url, urlencode(headers))


def resolve2(name, url):
    stream_url = ''
    ua_win = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.120 Safari/537.36'
    ua = 'Mozilla/5.0 (iPad; CPU OS 15_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.1 Mobile/15E148 Safari/604.1'

    resolved = ['//dabac', '//sansat', '//istorm', '//zvision', '//glisco', '//bedsport', '//coolrea', '//evfancy',
                '//s2watch', '//vuen', '//gopst']
    # new_streams = ['//dabac']
    log_info('RESOLVE-URL: {0}'.format(url))

    # NEW GLISCO/SANSAT BRANCH
    if '//glisco' in url or '//sansat' in url or 'vertex' in url or 'nexa' in url or 'kora.st' in url:
        Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        chan_id = url.split('id=')[-1]

        def _extract_stream(embed_html):
            # A channel's api/player.php can hand back several player types; try
            # each extraction in turn. Returns (method, url); method is set even
            # on failure so the log can say *why* a mirror produced no stream
            # (e.g. econfig-blank = the host blanked the URL for Kodi's TLS).
            # 1) ?ppcfg=1 JSON  (dinamic-embed.site, tursatrueper.xyz/pink.php)
            try:
                cfg = json.loads(embed_html)
                fl = cfg.get('src') or cfg.get('srcBase')
                if fl:
                    return ('ppcfg', fl)
            except Exception:
                pass
            # 2) character-array return([...].join(""))  (forgemindly.com)
            arr_m = re.search(r'return\s*\(\s*(\[(?:"[^"]*",?\s*)+\])', embed_html)
            if arr_m:
                fl = ''.join(re.findall(r'"([^"]*)"', arr_m.group(1))).replace('\\/', '/')
                if fl:
                    return ('char-array', fl)
            # 3) window._econfig blob  (talentedlost.click / fisherman / wilderness)
            try:
                from resources.modules import econfig as _econfig
                if _econfig.find_econfig(embed_html):
                    fl, _ = _econfig.extract_stream_from_html(embed_html)
                    # fl == '' means the blob decoded but the host blanked the
                    # stream_url (TLS-fingerprint gating) -> report it distinctly.
                    return ('econfig' if fl else 'econfig-blank', fl or None)
            except Exception:
                pass
            # 4) window.PP_CLAPPR_CONFIG in the plain page  (play.matchli.st
            #    clappr-classic.php answers 403 to ?ppcfg=1)
            pp = re.search(r'window\.PP_CLAPPR_CONFIG\s*=\s*(\{.*?\});\s*</script>', embed_html, re.DOTALL)
            if pp:
                try:
                    cfg = json.loads(pp.group(1))
                    fl = cfg.get('src') or cfg.get('srcBase')
                    if fl:
                        return ('pp-clappr-config', fl)
                except Exception:
                    pass
            return (None, None)

        # The same channel is mirrored across sub-domains (nexa.st, s1/s2/s3.
        # nexa.st, vertex.st ...) and each mirror routes to a *different* embed
        # host. Some hosts blank the stream for Kodi's old TLS fingerprint, so
        # when the caller's host yields nothing we retry the other mirrors.
        import time as _time
        uri0 = urlparse(url)
        hosts = [uri0.netloc]
        m = re.match(r'^(?:s\d+\.)?(nexa\.st|vertex\.st|kora\.st)$', uri0.netloc)
        if m:
            base = m.group(1)
            for h in (base, 's2.' + base, 's1.' + base, 's3.' + base):
                if h not in hosts:
                    hosts.append(h)

        flink = None
        frame = None
        for host in hosts:
            try:
                api_url = '{0}://{1}/api/player.php?id={2}'.format(uri0.scheme, host, chan_id)
                api_ref = '{0}://{1}/'.format(uri0.scheme, host)
                api_resp = six.ensure_text(client.request(api_url, referer=api_ref))
                cand_frame = json.loads(api_resp)['url']
                cfg_url = cand_frame + ('&' if '?' in cand_frame else '?') + 'ppcfg=1&_=' + str(int(_time.time() * 1000))
                try:
                    embed_html = six.ensure_str(
                        client.request(cfg_url, referer=api_ref, headers={'User-Agent': ua_win}) or '')
                except Exception:
                    embed_html = ''  # e.g. 403 on ?ppcfg=1 -> plain page below
                method, cand = _extract_stream(embed_html)
                if not cand:  # ?ppcfg=1 refused -> plain frame page
                    try:
                        method, cand = _extract_stream(six.ensure_str(
                            client.request(cand_frame, referer=api_ref, headers={'User-Agent': ua_win})))
                    except Exception:
                        pass
                frame_host = urlparse(cand_frame).netloc
                if cand:
                    flink = cand
                    frame = cand_frame
                    log_info('mirror {0} -> {1}: OK via {2}'.format(host, frame_host, method))
                    break
                log_info('mirror {0} -> {1}: no stream ({2})'.format(
                    host, frame_host, method or 'unknown player'))
            except Exception as _me:
                log_info('mirror {0}: error {1!r}'.format(host, _me))
                continue
        if not flink:
            raise Exception('could not extract stream src for ' + url)

        cookie_str = ''
        # Origin / Referer must match the winning iframe host.
        frame_origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(frame))
        stream_headers = {
            'Referer': frame_origin + '/',
            'Origin': frame_origin,
            'User-Agent': ua_win,
            'verifypeer': 'false',
        }
        if cookie_str:
            stream_headers['Cookie'] = cookie_str

        stream_url = xbmc_curl_encode(flink, stream_headers)

    elif any(i in url for i in resolved):
        Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        referer = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(url))
        r = six.ensure_str(client.request(url))
        if 'get_content.php?channel=' in r or 'api/player.php?id=' in r:
            id_ = re.findall(r'(\d+)$', url)[0]
            if 'get_content.php?channel=' in r:
                frame = referer + "get_content.php"
                hdr = {
                    'referer': url,
                    # 'sec-fetch-mode': 'cors',
                    'user-agent': ua_win,
                }
                params = {'channel': id_, }
            elif 'api/player.php?id=' in r:
                frame = referer + "api/player.php"
                hdr = {
                    'referer': url,
                    # 'sec-fetch-mode': 'cors',
                    'user-agent': ua_win,
                }
                params = {'id': id_, }
            r = six.ensure_str(requests.get(frame, params=params, headers=hdr).content)
        if 'fid=' in r:
            regex = '''<script>fid=['"](.+?)['"].+?text/javascript.*?src=['"](.+?)['"]></script>'''
            vid, getembed = re.findall(regex, r, re.DOTALL)[0]
            getembed = 'https:' + getembed if getembed.startswith('//') else getembed
            embed = six.ensure_str(client.request(getembed))
            embed = re.findall(r'''document.write.+?src=['"](.+?player)=''', embed, re.DOTALL)[0]
            host = '{}=desktop&live={}'.format(embed, str(vid))
            data = six.ensure_str(client.request(host, referer=referer))
            try:
                link = re.findall(r'''return\((\[.+?\])\.join''', data, re.DOTALL)[0]
            except IndexError:
                link = re.findall(r'''file:.*['"](http.+?)['"]\,''', data, re.DOTALL)[0]

            flink = link.replace('[', '').replace(']', '').replace('"', '').replace(',', '').replace('\/', '/')
            stream_headers = {'Referer': host.split('embed')[0], 'User-Agent': ua_win}
            stream_url = xbmc_curl_encode(flink, stream_headers)
        else:
            try:
                frame = json.loads(r).get('url')
            except Exception:
                frame = client.parseDOM(r, 'iframe', ret='src')[-1]
            # xbmc.log('FRAME: {}'.format(frame))
            referer = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(frame))
            data = six.ensure_str(client.request(frame, referer=url, output=url))
            try:
                data = re.findall(r'''script>(eval.+?\{\}\))\)''', data, re.DOTALL)[-1]
                from resources.modules import jsunpack
                data = six.ensure_text(jsunpack.unpack(str(data) + ')'), encoding='utf-8')
            except Exception:
                pass
            # xbmc.log('DATAAAA: {}'.format(data))

            if '"h","t","t","p"' in data:
                link = re.findall(r'''return\((\[.+?\])\.join''', data, re.DOTALL)[0]
                link = json.loads(link)
                link = "".join(link)
                flink = link.replace('////', '//')

            elif 'player.src({src:' in data:
                flink = re.findall(r'''player.src\(\{src:\s*["'](.+?)['"]\,''', data, re.DOTALL)[0]
            elif 'hlsjsConfig' in data and not 'new Clappr' in data:
                try:
                    if 'data-page=' in data:
                        data_page = client.parseDOM(data, 'div', ret='data-page', attrs={'id': 'app'})[0]
                        data_page = client.replaceHTMLCodes(data_page)
                        try:
                            data_page = data_page.replace('\/', '/')
                            flink = re.findall(r'''(https?:\/\/[^\s]+\.m3u8)''', data_page, re.DOTALL)[0]
                        except Exception:
                            data_page = json.loads(data_page)
                            flink = data_page['props']['streamData']['streamurl']
                    else:
                        xbmcgui.Dialog().textviewer('data', str(data))
                        hlsurl, pk, ea = \
                            re.findall('.*hlsUrl\s*=\s*"(.*?&\w+=)".*?var\s+\w+\s*=\s*"([^"]+).*?>\s*ea\s*=\s*"([^"]+)',
                                       data,
                                       re.DOTALL)[0]
                        pk = pk[:53] + pk[53 + 1:]
                        link = hlsurl.replace('" + ea + "', ea) + pk
                        link_data = six.ensure_str(client.request(link))
                        flink = re.findall('.*(http.+?$)', link_data)[0]
                except Exception as e:
                    xbmcgui.Dialog().textviewer('e', str(e))
                    flink = re.findall(r'''src=\s*["'](.+?)['"]''', data, re.DOTALL)[0]
            elif 'new Clappr' in data:
                flink = re.findall(r'''source\s*:\s*["']?(.+?)['"]?\,''', str(data), re.DOTALL)[0]
                # xbmc.log('FLINKKK: {}'.format(flink))
                if flink == "m3u8Url" or flink == "m3u8":
                    html = six.ensure_str(data)
                    m = re.search(r'const\s+CHANNEL_KEY\s*=\s*["\']([\w-]+)["\']', html)
                    if not m:
                        raise Exception('CHANNEL_KEY not found')
                    channel_key = m.group(1)

                    m = re.search(r'const\s+BUNDLE\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', html)
                    if not m:
                        raise Exception('BUNDLE not found')
                    bundle_b64 = m.group(1)

                    parts_raw = base64.b64decode(bundle_b64)
                    parts = json.loads(six.ensure_str(parts_raw))
                    for k in list(parts.keys()):
                        try:
                            parts[k] = six.ensure_str(base64.b64decode(parts[k]))
                        except Exception:
                            pass

                    auth_url = (
                            parts.get('b_host', '') +
                            parts.get('b_script', '') +
                            '?channel_id=' + quote_plus(channel_key) +
                            '&ts=' + quote_plus(parts.get('b_ts', '')) +
                            '&rnd=' + quote_plus(parts.get('b_rnd', '')) +
                            '&sig=' + quote_plus(parts.get('b_sig', ''))
                    )

                    pu = urlparse(referer)
                    origin = '{}://{}'.format(pu.scheme or 'https', pu.netloc)
                    ref_for_headers = frame or referer
                    try:
                        client.request(auth_url, referer=ref_for_headers)
                    except Exception:
                        pass

                    # server_lookup για server_key
                    lookup_url = urljoin(origin, '/server_lookup.php?channel_id=' + quote_plus(channel_key))
                    body = six.ensure_str(client.request(lookup_url, referer=ref_for_headers))

                    server_key = ''
                    try:
                        resp = json.loads(body)
                        server_key = six.ensure_str(resp.get('server_key', '')).strip()
                    except Exception:
                        m = re.search(r'"server_key"\s*:\s*"([^"]+)"', body)
                        if m:
                            server_key = six.ensure_str(m.group(1)).strip()

                    if not server_key:
                        raise Exception('server_key not found')

                    sk_slug = server_key.strip().strip('/')  # π.χ. 'x4-cdn' ή 'x4-cdn/cdn'
                    if sk_slug == 'top1/cdn':
                        candidates = [
                            'https://top1.newkso.ru/top1/cdn/{}/mono.m3u8'.format(channel_key),
                            'http://top1.newkso.ru/top1/cdn/{}/mono.m3u8'.format(channel_key),
                        ]
                    else:
                        host_prefix = sk_slug.replace('/', '.')  # 'x4-cdn.cdn' αν έχει subpath
                        candidates = [
                            'https://{}.new.newkso.ru/{}/{}/mono.m3u8'.format(host_prefix, sk_slug, channel_key),
                            'https://new.newkso.ru/{}/{}/mono.m3u8'.format(sk_slug, channel_key),
                            'http://{}.new.newkso.ru/{}/{}/mono.m3u8'.format(host_prefix, sk_slug, channel_key),
                            'http://new.newkso.ru/{}/{}/mono.m3u8'.format(sk_slug, channel_key),
                        ]

                    candidates = [re.sub(r'(?<!:)/{2,}', '/', u) for u in candidates]
                    working = None
                    for u in candidates:
                        try:
                            chunk = client.request(u, referer=ref_for_headers)
                            s = six.ensure_str(chunk) if chunk is not None else ''
                            if s.startswith('#EXTM3U') or len(s) > 0:
                                working = u
                                break
                        except Exception as e:
                            log_debug('probe fail: {0} -> {1}'.format(u, e))
                            continue

                    if not working:
                        working = candidates[0]
                    # xbmc.log('FLINK (new Clappr): {}'.format(working))
                    flink = working


                elif flink == "src":
                    flink = re.findall(r'''src=\s*["'](.+?)['"]''', data, re.DOTALL)[0]
            elif 'player.setSrc' in data:
                flink = re.findall(r'''player.setSrc\(["'](.+?)['"]\)''', data, re.DOTALL)[0]
            elif 'new Player(' in data:
                player = re.findall(r'''new Player(\(.+?\))''', data,
                                    re.DOTALL)[0]
                import ast, random
                player = ast.literal_eval(player)
                p1 = player[3]
                p2 = random.choice(list(player[4].keys()))
                flink = 'https://{}/hls/{}/live.m3u8'.format(p2, p1)
            else:
                try:
                    data = data.replace('\/', '/')
                    flink = re.findall(r'(https?:\/\/[^\s]+\.m3u8)', data, re.DOTALL)[0]
                except Exception:
                    try:
                        flink = re.findall(r'''source:\s*["'](.+?)['"]''', data, re.DOTALL)[0]
                    except IndexError:
                        ea = re.findall(r'''ajax\(\{url:\s*['"](.+?)['"],''', data, re.DOTALL)[0]
                        ea = six.ensure_text(client.request(ea)).split('=')[1]
                        flink = re.findall('''videoplayer.src = "(.+?)";''', ea, re.DOTALL)[0]
                        flink = flink.replace('" + ea + "', ea)
            stream_headers = {'Referer': referer + '/', 'Origin': referer, 'User-Agent': ua_win,
                              'Connection': 'keep-alive'}
            stream_url = xbmc_curl_encode(flink, stream_headers)
    elif '//dabac' in url:
        Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        referer = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(url))
        frame = referer + "api/player.php?id={}"
        id = url.split("id=")[-1]
        nurl = frame.format(id)
        data = six.ensure_text(client.request(nurl))
        # xbmc.log("DATAAAAAA: {}".format(data))
        url = json.loads(data)["url"]
        data = requests.get(url, headers=headers, timeout=10).text
        iframe = re.findall(r'<iframe[^>]+src="([^"]+?)\+encodeURIComponent\(document\.referrer\)', data, re.DOTALL)[-1]
        ref = '{uri.scheme}://{uri.netloc}/'.format(uri=urlparse(iframe))
        iframe += quote_plus(referer)
        hdr = {
            'User-Agent': ua_win,  # ή ua_mob
            'Referer': url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
        }
        data = six.ensure_text(requests.get(iframe, headers=hdr, timeout=10).text)
        # xbmc.log("DATAAAAAA2: {}".format(data))
        m = re.search(r'id="crf__"\s+value=[\'"]([^\'"]+)', data)
        if m:
            b64url = m.group(1)
            flink = base64.b64decode(b64url).decode("utf-8")
            stream_headers = {'Referer': ref + '/', 'Origin': ref, 'User-Agent': 'iPad'}
            stream_url = xbmc_curl_encode(flink, stream_headers)

    else:
        stream_url = url

    # xbmc.log('STREAM: {}'.format(stream_url))
    liz = xbmcgui.ListItem(six.ensure_str(name, encoding='utf-8', errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setProperty("IsPlayable", "true")
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


################################################################################
#########################CHANNELS HELPERS#######################################
################################################################################

xbmcvfs.mkdir(control.translatePath(ADDON_DATA))
JSON_FILE_PATH = control.translatePath(ADDON_DATA + 'channels.json')
LAST_UPDATE_FILE = control.translatePath(ADDON_DATA + 'last_update.txt')


def is_time_to_update(hours=6):
    if not xbmcvfs.exists(LAST_UPDATE_FILE):
        f = control.openFile(LAST_UPDATE_FILE, 'w')
        f.write(str(time.time()))
        f.close()
    f = control.openFile(LAST_UPDATE_FILE, 'r')
    content = f.read()
    last_update_timestamp = float(content.strip())
    f.close()

    hours_in_seconds = hours * 60 * 60
    current_time = time.time()

    return (current_time - last_update_timestamp) >= hours_in_seconds


def fetch_and_store_channel_data():
    hdrs = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36'}
    try:
        url = BASEURL + '''api/trpc/mutual.getTopTeams,saves.getAllUserSaves,mutual.getFooterData,mutual.getAllChannels,mutual.getWebsiteConfig?batch=1&input={"0":{"json":null,"meta":{"values":["undefined"]}},"1":{"json":null,"meta":{"values":["undefined"]}},"2":{"json":null,"meta":{"values":["undefined"]}},"3":{"json":null,"meta":{"values":["undefined"]}},"4":{"json":null,"meta":{"values":["undefined"]}}}'''
        response = six.ensure_text(client.request(url, headers=hdrs), encoding='utf-8', errors='ignore')
        new_data = json.loads(response)

        new_channels_data = None
        for result in new_data:
            if ("result" in result and "data" in result["result"] and
                    "json" in result["result"]["data"] and "allChannels" in result["result"]["data"]["json"]):
                new_channels_data = result["result"]["data"]["json"]["allChannels"]
                break

        if not new_channels_data:
            return

        if not xbmcvfs.exists(JSON_FILE_PATH):
            existing_data = []
        else:
            file = xbmcvfs.File(JSON_FILE_PATH)
            existing_data = json.load(file)
            file.close()

        existing_data_map = {channel['_id']: channel for channel in existing_data}
        changes_made = False

        for new_channel in new_channels_data:
            channel_id = new_channel['_id']
            if channel_id not in existing_data_map or new_channel['links'] != existing_data_map[channel_id]['links']:
                existing_data_map[channel_id] = new_channel
                changes_made = True

        if changes_made:
            file = xbmcvfs.File(JSON_FILE_PATH, 'w')
            file.write(json.dumps(list(existing_data_map.values())))
            file.close()
            print("Channel links updated")

    except Exception as e:
        print("Error fetching or updating channel data: {}".format(e))


def load_data_from_json():
    if not xbmcvfs.exists(JSON_FILE_PATH):
        return []
    f = control.openFile(JSON_FILE_PATH, 'r')
    contents = f.read()
    f.close()
    return json.loads(contents)


def get_links_for_channel(channel_name):
    channels_data = load_data_from_json()
    for channel in channels_data:
        datos = []
        if channel['channelName'] == channel_name:
            for link in channel['links']:
                chan = channel['channelName']
                lang = channel['language']
                datos.append((chan, link, lang))
            return datos
        else:
            if channel['channelName'].replace(' ', '') == channel_name.replace(' ', ''):
                for link in channel['links']:
                    chan = channel['channelName']
                    lang = channel['language']
                    datos.append((chan, link, lang))
                return datos
    return None


###################################################
###################################################
###################################################
########### Time and Date Helpers #################
###################################################
def fetch_user_timezone():
    try:
        locale_timezone = json.loads(xbmc.executeJSONRPC(
            '{"jsonrpc": "2.0", "method": "Settings.GetSettingValue", "params": {"setting": "locale.timezone"}, "id": 1}'))

        if locale_timezone['result']['value']:
            local_tzinfo = gettz(locale_timezone['result']['value'])
            if local_tzinfo:
                log_debug('SHOW FINAL ΤΙΜΕΖΟΝΕ: {0}'.format(local_tzinfo))
                return local_tzinfo
            else:
                log_warning("Failed to get tzinfo for timezone: {0}".format(locale_timezone['result']['value']))
        else:
            log_debug("No timezone value found in Kodi settings")
    except Exception as e:
        log_warning("Error fetching timezone from Kodi settings: {0}".format(e))

    return gettz()


def convDateUtil(timestring, newfrmt='default', in_zone='UTC'):
    if newfrmt == 'default':
        newfrmt = xbmc.getRegion('time').replace(':%S', '')
    try:
        local_tzinfo = fetch_user_timezone()
        in_time = parse(timestring)
        in_time_with_timezone = in_time.replace(tzinfo=gettz(in_zone))
        local_time = in_time_with_timezone.astimezone(local_tzinfo)
        return local_time.strftime(newfrmt)
    except Exception:
        return timestring


def time_convert(timestamp):
    timestamp = int(str(timestamp)[:10])
    dt_object = datetime.fromtimestamp(timestamp)
    time_ = dt_object.strftime("%d-%b, %H:%M")
    # strftime's %b is the locale month abbrev -> a byte str with non-ascii bytes
    # on non-english locales; return unicode so it can't poison label .format().
    return six.ensure_text(time_, encoding='utf-8', errors='replace')


def time_to_update(hours=6):
    try:
        f = control.openFile(LAST_UPDATE_FILE, 'r')
        content = f.read()
        last_update_timestamp = float(content.strip())
        f.close()
    except Exception:
        return True

    hours_in_seconds = int(hours) * 60 * 60
    current_time = time.time()

    return (current_time - last_update_timestamp) >= hours_in_seconds


def update_last_update_time():
    try:
        f = control.openFile(LAST_UPDATE_FILE, 'w')
        f.write(str(time.time()))
        f.close()
    except Exception:
        print("Error updating last update time")


def adjust_date_and_convert_to_timestamp_ms(matchDate, livetvtimestr):
    date_obj = parser.parse(matchDate[2:])
    hours, minutes = map(int, livetvtimestr.split(":"))
    date_obj = date_obj.replace(hour=hours, minute=minutes)

    date_obj += timedelta(days=1)
    user_timezone = fetch_user_timezone()
    date_obj = date_obj.astimezone(user_timezone)

    if six.PY2:
        timestamp_ms = int((time.mktime(date_obj.timetuple()) + date_obj.microsecond / 1e6) * 1000)
    else:
        timestamp_ms = int(date_obj.timestamp() * 1000)
    return timestamp_ms


##########################################################################################
##########################################################################################


def Open_settings():
    control.openSettings()


def addDir(name, url, mode, iconimage, description, isFolder=True, infoLabels=None):
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
        mode) + "&name=" + name_encoded + "&iconimage=" + iconimage_encoded + "&description=" + description_encoded

    liz = xbmcgui.ListItem(six.ensure_str(name, encoding='utf-8', errors='replace'))
    liz.setArt({'icon': iconimage, 'thumb': iconimage, 'poster': iconimage, 'fanart': FANART})
    if infoLabels:
        liz.setInfo(type="Video", infoLabels=infoLabels)
    if not isFolder:
        if mode == 'settings' or mode == 'version' or mode == 'clear' or mode == 'play_stream' or mode == 'site_play' or mode == 'vavoo_diag':
            isFolder = False
        else:
            liz.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(_handle, url=u, listitem=liz, isFolder=isFolder)


def site_events_menu(key):
    # préchauffage Vavoo (catalogue + grille TV), LiveWatch et agendas
    # rapides en arrière-plan pour des sources de dépannage instantanées
    # au clic
    try:
        site_vavoo.warmup()
        site_livewatch.warmup()
        _agenda_warm()
    except Exception:
        pass
    site = SITES[key]
    try:
        events = site.list_events()
    except Exception as e:
        log_info('site {0} failed: {1!r}'.format(key, e))
        events = []
    if not events and key == site_embedlivesports.KEY:
        # streamx-hd down / empty agenda: futbollibrefullhd.org agenda instead,
        # resolved by the Futbol Libre module (same embed family)
        try:
            events = site_futbollibre.list_diaries()
        except Exception as e:
            log_info('emls fallback failed: {0!r}'.format(e))
        key = site_futbollibre.KEY
    # Vavoo subsites set NO_FR_FILTER: every channel is French by
    # definition and their server names are generic "Serveur N", so the
    # "Uniquement les sources françaises" setting must not hide them
    # (it wiped the whole Vavoo lists with the misleading message
    # "Aucune source française parmi les matchs de ce site").
    fr_only = _french_only() and not getattr(site, 'NO_FR_FILTER', False)
    vavoo_fb = _vavoo_fallback()
    shown = 0
    for e in events:
        # événement sans serveur français : masqué, sauf si le rattachement
        # Vavoo est actif (ses sources Vavoo de dépannage seront proposées
        # dans le menu des serveurs)
        no_fr = fr_only and e.get('servers') and \
            not _servers_have_french(e.get('servers'))
        if no_fr and not vavoo_fb:
            continue
        _add_site_event(key, site, e, vavoo=no_fr)
        shown += 1
    if events and not shown:
        control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs de ce site.[/COLOR]", NAME, ICON, 6000)
    if not events and key.startswith('vavoo'):
        control.infoDialog(
            u'[COLOR red]Vavoo est momentanément inaccessible '
            u'(connexion ou serveur). Réessayez plus tard.[/COLOR]',
            NAME, ICON, 6000)
        addDir(u'[B][COLOR gold]Diagnostic Vavoo — voir pourquoi la liste '
               u'est vide[/COLOR][/B]', 'diag', 'vavoo_diag', ICON, FANART,
               False, infoLabels={'title': u'Diagnostic Vavoo'})
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _add_site_event(key, site, e, vavoo=False):
    # title colour signals state: cyan = live, gold = upcoming, grey = finished.
    colors = {'live': 'cyan', 'soon': 'gold', 'done': 'grey'}
    t = time_convert(e['start_ms']) if e['start_ms'] else u'-'
    # event titles carry accents/ñ (España, Brasil...) -> keep every piece
    # unicode so the byte-str .format() can't fall back to the ascii codec.
    tag = six.ensure_text(e.get('code') or e.get('league') or '',
                          encoding='utf-8', errors='replace')  # country code, else league
    title = six.ensure_text(e['title'], encoding='utf-8', errors='replace')
    cc = u'[COLOR orange][{0}][/COLOR] '.format(tag) if tag else u''
    if getattr(site, 'TAG_LAST', False):  # time + title first, league/sport tag at the end
        label = u'[COLOR cyan]{0}[/COLOR] [COLOR {1}][B]{2}[/B][/COLOR]{3}'.format(
            t, colors.get(e['status'], 'gold'), title, u' ' + cc.rstrip() if cc else u'')
    else:
        label = u'{0}[COLOR cyan]{1}[/COLOR] [COLOR {2}][B]{3}[/B][/COLOR]'.format(
            cc, t, colors.get(e['status'], 'gold'), title)
    if vavoo:  # match conservé grâce au rattachement Vavoo -> balise
        label += u' [COLOR gold][Vavoo][/COLOR]'
    # carry the event's title + poster down the chain so the servers menu
    # and the player show the match, not the generic SportHD art.
    payload = json.dumps({'k': key, 's': e['servers'],
                          't': e['title'], 'p': e['poster']})
    addDir(label, payload, 'site_streams', e['poster'] or ICON, e['title'], True)


def fmov_menu():
    for s in site_futbolmov.SUBSITES:
        addDir(u'[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(s.NAME), s.KEY,
               'site_events', ICON, FANART, True,
               infoLabels={'title': s.NAME, 'plot': s.DESC})
    addDir(u'[B][COLOR gold]Rechercher[/COLOR][/B]', 'fmov', 'fmov_search', ICON, FANART, True,
           infoLabels={'title': u'Rechercher', 'plot': u'Rechercher dans les trois listes (équipe, championnat…).'})
    xbmcplugin.endOfDirectory(_handle)


def vavoo_menu():
    for s in site_vavoo.SUBSITES:
        addDir(u'[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(s.NAME), s.KEY,
               'site_events', ICON, FANART, True,
               infoLabels={'title': s.NAME, 'plot': s.DESC})
    addDir(u'[B][COLOR gold]Diagnostic Vavoo (dépannage)[/COLOR][/B]', 'diag',
           'vavoo_diag', ICON, FANART, False,
           infoLabels={'title': u'Diagnostic Vavoo',
                       'plot': u'Teste la connexion à Vavoo (catalogue, '
                               u'signature, cache, lecture) et affiche la '
                               u'cause exacte des listes vides.'})
    xbmcplugin.endOfDirectory(_handle)


def vavoo_diag():
    rows = []
    try:
        rows = site_vavoo.diagnostic()
    except Exception as e:
        log_info('vavoo diagnostic failed: {0!r}'.format(e))
    lines = [u'[B]Diagnostic Vavoo — SportHD {0}[/B]'.format(VERSION), u'']
    for label, ok, detail in rows:
        state = (u'[COLOR lime]OK    [/COLOR]' if ok
                 else u'[COLOR red]ECHEC[/COLOR]')
        lines.append(u'{0}  {1} : {2}'.format(state, label, detail))
    if not rows:
        lines.append(u'[COLOR red]ECHEC[/COLOR]  diagnostic impossible')
    lines.append(u'')
    if site_vavoo.LAST_ERROR:
        lines.append(u'Dernière erreur enregistrée :')
        lines.append(u'[COLOR gold]{0}[/COLOR]'.format(site_vavoo.LAST_ERROR))
        lines.append(u'')
    if rows and not rows[0][1]:
        lines.append(u'Le catalogue Vavoo est inaccessible depuis votre '
                     u'connexion (blocage du fournisseur d\'accès, DNS ou '
                     u'pare-feu). Essayez un autre réseau (partage de '
                     u'connexion mobile) pour comparer.')
    try:
        xbmcgui.Dialog().textviewer(u'Diagnostic Vavoo', u'\n'.join(lines))
    except Exception:
        control.okDialog(u'Diagnostic Vavoo', u'\n'.join(lines))
    try:
        if xbmcgui.Dialog().select(
                u'Diagnostic Vavoo — actions',
                [u'Vider le cache Vavoo et réessayer', u'Fermer']) == 0:
            site_vavoo.clear_cache()
            control.infoDialog(
                u'[COLOR gold]Cache Vavoo vidé — rouvrez les listes '
                u'Vavoo.[/COLOR]', NAME, ICON, 5000)
    except Exception:
        pass


def fmov_search():
    kb = xbmc.Keyboard('', u'Rechercher')
    kb.doModal()
    query = six.ensure_text(kb.getText(), encoding='utf-8', errors='replace').strip().lower() \
        if kb.isConfirmed() else u''
    if not query:
        xbmcplugin.endOfDirectory(_handle, succeeded=False)
        return
    found = 0
    for site in site_futbolmov.SUBSITES:
        try:
            events = site.list_events()
        except Exception as e:
            log_info('fmov search {0} failed: {1!r}'.format(site.KEY, e))
            continue
        for e in events:
            hay = six.ensure_text(u'{0} {1}'.format(e['title'], e.get('league') or ''),
                                  encoding='utf-8', errors='replace').lower()
            if query in hay:
                _add_site_event(site.KEY, site, e)
                found += 1
    if not found:
        control.infoDialog(u'[COLOR red]Aucun résultat[/COLOR]', NAME, ICON, 4000)
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


_QUAL_RES = {'4K': u'2160p', 'FHD': u'1080p', 'HD': u'720p', 'SD': u'576p'}


def _add_livewatch_links(title, poster=ICON):
    """Ajoute 3 liens « Lien 1 / 2 / 3 » via LiveWatch pour un match
    sans source française.

    Ce sont les chaînes françaises correspondant au match, trouvées par
    DOUBLE CHASSE : le moteur Vavoo (grille TV + heuristique, pool
    élargi à 8) PUIS la recherche directe dans le groupe France de
    LiveWatch (~930 chaînes, plus large) qui attrape des chaînes que le
    moteur ne voit pas. Chaque lien porte la résolution annoncée par le
    site (4K/FHD/HD) quand elle existe et les URLs Vavoo de la même
    chaîne pour la DOUBLE PORTE au clic (résolution parallèle Vavoo +
    LiveWatch, la première qui répond gagne — ouverture aussi rapide
    qu'une chaîne française native). Les liens sont pré-résolus en
    arrière-plan dès l'affichage (un seul ticket pour les trois).
    Rien n'est ajouté si le catalogue LiveWatch n'est pas encore prêt
    (jamais d'attente à l'affichage ; il le sera au prochain menu).
    """
    try:
        if not site_livewatch.is_warm():
            site_livewatch.warmup()
            return 0
        chans = site_vavoo.fallback_channels(title, limit=8)
        try:
            lw_only = site_livewatch.search_french(title, limit=8)
        except Exception as exc:
            log_info('livewatch search_french failed: {0!r}'.format(exc))
            lw_only = []
        if lw_only:
            have = set(site_vavoo._norm(c['title']) for c in chans)
            chans = list(chans) + [c for c in lw_only
                                   if site_vavoo._norm(c['title'])
                                   not in have]
    except Exception as exc:
        log_info('livewatch backup failed: {0!r}'.format(exc))
        return 0
    if not chans:
        return 0
    links = site_livewatch.backup_links(chans, limit=3)
    if not links:
        return 0
    # ouverture rapide : les 3 liens partent en pré-résolution tout de suite
    site_livewatch.prewarm(links)
    added = 0
    for ch in links:
        try:
            payload = json.dumps({'k': site_livewatch.KEY,
                                  'u': u'lw://{0}'.format(ch['id']),
                                  'alt': [u'lw://{0}'.format(i)
                                          for i in ch['ids']
                                          if i != ch['id']],
                                  'vav': ch.get('vav') or [],
                                  't': title, 'p': poster})
            qual = ch.get('quality') or ''
            res = _QUAL_RES.get(qual)
            if qual and res:
                qtxt = u'Qualité annoncée : {0} (~{1}).'.format(qual, res)
            elif qual:
                qtxt = u'Qualité annoncée : {0}.'.format(qual)
            else:
                qtxt = u'Qualité non annoncée par le site.'
            info = {'title': u'{0} | Lien {1} — {2} (LiveWatch)'.format(
                        title, added + 1, ch['title']),
                    'plot': u'{0} — {1} via la double porte Vavoo + '
                            u'LiveWatch : les deux résolutions partent en '
                            u'parallèle au clic, la première qui répond '
                            u'gagne — ouverture aussi rapide qu\'une '
                            u'chaîne française native. {2}'.format(
                                title, ch['title'], qtxt)}
            label = u'[B]Lien {0}[/B]'.format(added + 1)
            if ch['title']:
                label += u' · {0}'.format(ch['title'])
            if qual:
                label += u' ({0})'.format(qual)
            addDir(label, payload, 'site_play', poster, title,
                   isFolder=False, infoLabels=info)
            added += 1
        except Exception as exc:
            log_info('livewatch item failed: {0!r}'.format(exc))
    return added


def site_streams_menu(payload):
    d = json.loads(payload)
    poster = d.get('p') or ICON
    title = d.get('t', '')
    # same NO_FR_FILTER exception as site_events_menu (Vavoo servers are
    # generic "Serveur N" and French by definition); the 'nf' flag does
    # the same for the fast fallback sources (fllibre/fmov) whose server
    # names are generic too ("Server1 (sportiskas)", "Disney+ (tvf90)")
    fr_only = _french_only() and not d.get('nf') and not getattr(
        SITES.get(d['k']), 'NO_FR_FILTER', False)
    shown = 0
    fr_shown = 0
    for sname, surl in d['s']:
        # server names carry accents too (e.g. "Señal 1") -- byte-str .format()
        # on a unicode arg raises UnicodeEncodeError in py2, so keep it unicode.
        sname = six.ensure_text(sname, encoding='utf-8', errors='replace')
        is_fr = _is_french_source(sname, '')
        if is_fr:
            fr_shown += 1
        if fr_only and not is_fr:
            continue
        info = {'title': u'{0} | {1}'.format(title, sname), 'plot': title}
        addDir(u'[B]{0}[/B]'.format(sname),
               json.dumps({'k': d['k'], 'u': surl, 't': title, 'p': d.get('p', '')}),
               'site_play', poster, title, isFolder=False, infoLabels=info)
        shown += 1
    # LiveWatch : réservé aux matchs SANS AUCUNE source française —
    # « ce qui est déjà en français ne bouge pas ». 3 liens « Lien 1/2/3 »
    # correspondant au match (mêmes chaînes que le moteur Vavoo, autres
    # résolutions quand le site les annonce), pré-résolus en arrière-plan
    # dès l'affichage pour s'ouvrir vite. Jamais pour les rubriques Vavoo
    # (déjà des chaînes françaises) ni si le réglage de dépannage est coupé.
    if not fr_shown and not d['k'].startswith('vavoo') and _vavoo_fallback():
        lw_ok = False
        try:
            lw_ok = _add_livewatch_links(title, poster)
        except Exception as exc:
            log_info('livewatch backup menu failed: {0!r}'.format(exc))
        if not shown:
            if lw_ok:
                control.infoDialog(
                    u'[COLOR gold]Aucune source française native — '
                    u'3 liens de secours LiveWatch proposés (ils se '
                    u'préparent en arrière-plan).[/COLOR]', NAME, ICON, 6000)
            elif _add_vavoo_fallback(title, poster):
                # filet de sécurité si LiveWatch est indisponible
                control.infoDialog(
                    u'[COLOR gold]Aucune source française — chaînes Vavoo '
                    u'de dépannage proposées.[/COLOR]', NAME, ICON, 6000)
            else:
                control.infoDialog(
                    u"[COLOR gold]Aucune source française pour ce match.[/COLOR]",
                    NAME, ICON, 6000)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def _resolve_dual_door(d):
    """DOUBLE PORTE pour un lien LiveWatch : la MÊME chaîne est résolue
    en parallèle par la porte Vavoo (rapide : ~0,4 s) et par la porte
    LiveWatch (pré-résolue au menu -> ~0,1 s). La première qui répond
    gagne ; l'autre n'est jamais attendue inutilement.

    Retour (resultat, site, referer_url_ou_None) :
      - porte LiveWatch : resultat = (m3u8, page) — site_play en tire
        l'origine livewatch.icu (en-têtes côté CDN) ;
      - porte Vavoo : resultat = m3u8, referer_url = l'URL Vavoo (pour
        l'origine + le User-Agent MediaHubMX/2, comme un flux natif).
      - (None, None, None) si les deux portes échouent.
    """
    res = {}

    def _lw(u):
        try:
            res['lw'] = site_livewatch.resolve(u)
        except Exception as exc:
            log_info('double porte lw {0}: {1!r}'.format(u, exc))

    def _vav(u):
        try:
            m = site_vavoo.resolve_channel(u)
            if m:
                res['vav'] = m
                res['vav_url'] = u
        except Exception as exc:
            log_info('double porte vav {0}: {1!r}'.format(u, exc))

    ths = [threading.Thread(target=_lw, args=(d.get('u') or '',))]
    for u in (d.get('vav') or [])[:2]:
        ths.append(threading.Thread(target=_vav, args=(u,)))
    for t in ths:
        t.daemon = True
        t.start()
    # premier succès gagne : LiveWatch pré-résolu -> instantané, sinon
    # la porte Vavoo répond en général en ~0,4 s
    deadline = time.time() + 16.0
    while time.time() < deadline:
        if 'lw' in res or 'vav' in res:
            break
        if not any(t.is_alive() for t in ths):
            break
        time.sleep(0.1)
    if 'lw' in res:
        return res['lw'], site_livewatch, None
    if 'vav' in res:
        return res['vav'], site_vavoo, res.get('vav_url')
    for t in ths:                # un dernier thread encore vivant : finir
        t.join(timeout=8.0)
    if 'lw' in res:
        return res['lw'], site_livewatch, None
    if 'vav' in res:
        return res['vav'], site_vavoo, res.get('vav_url')
    return None, None, None


def site_play(payload):
    d = json.loads(payload)
    site = SITES[d['k']]
    title = d.get('t') or NAME
    poster = d.get('p') or ICON
    Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
    # 'alt' (rattachement Vavoo) : variantes de la même chaîne, essayées
    # dans l'ordre si la première ne résout pas.
    links = [d.get('u') or ''] + [u for u in (d.get('alt') or []) if u]
    result = None
    play_site = site
    vav_ref = None
    if d['k'] == site_livewatch.KEY and (d.get('vav') or d.get('alt')):
        # DOUBLE PORTE (v2.1.5) : LiveWatch (pré-résolu) + Vavoo partent
        # en parallèle, la première qui répond gagne.
        result, play_site, vav_ref = _resolve_dual_door(d)
        if not result:
            # portes principales KO : variantes LiveWatch restantes,
            # puis URLs Vavoo restantes, comme avant
            tried = set([d.get('u') or ''] + list(d.get('vav') or []))
            for u in [x for x in (d.get('alt') or [])
                      if x and x not in tried]:
                try:
                    result = site_livewatch.resolve(u)
                    play_site = site_livewatch
                except Exception as exc:
                    log_info('site resolve {0} failed: {1!r}'.format(u, exc))
                    result = None
                if result:
                    break
            if not result:
                for u in [x for x in (d.get('vav') or [])
                          if x not in tried]:
                    try:
                        m = site_vavoo.resolve_channel(u)
                    except Exception as exc:
                        log_info('site resolve {0} failed: {1!r}'.format(u, exc))
                        m = None
                    if m:
                        result, play_site, vav_ref = m, site_vavoo, u
                        break
    else:
        for u in links:
            try:
                result = site.resolve(u)
            except Exception as exc:
                log_info('site resolve {0} failed: {1!r}'.format(u, exc))
                result = None
            if result:
                break
    if not result:
        raise Exception('could not resolve stream for ' + d['u'])
    # resolve() normally returns just the m3u8 string; sites that chain
    # through intermediate hosts (e.g. futbollibre's tarjetarojita.xyz
    # wrapper) return (m3u8, final_page_url) since the CDN token's own
    # host, not the originally-clicked link, is what the player needs
    # for Referer/Origin.
    flink, referer_url = result if isinstance(result, tuple) else \
        (result, vav_ref or d['u'])
    origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(referer_url))
    stream_headers = {'Referer': origin + '/', 'Origin': origin,
                      'User-Agent': play_site.UA}
    # installed inputstream.ffmpegdirect (21.3.8) does not whitelist a
    # stream_headers listitem property -- its addon.xml listitemprops=
    # "...default_url|is_realtime_stream|..." has no headers entry, so
    # Kodi silently drops that property before the addon ever sees it
    # and FFmpeg opens the URL with no User-Agent, which this CDN 403s.
    # Embed the headers in the URL itself via Kodi's "url|key=val&..."
    # pipe convention instead -- recognized by Kodi's ffmpeg build
    # regardless of which inputstream addon is driving it.
    stream_url = xbmc_curl_encode(flink, stream_headers)
    liz = xbmcgui.ListItem(six.ensure_str(title, encoding='utf-8', errors='replace'))
    liz.setArt({'icon': poster, 'thumb': poster, 'poster': poster, 'fanart': FANART})
    liz.setInfo('video', {'title': title, 'plot': title})
    liz.setProperty("IsPlayable", "true")
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
    # live m3u8 via inputstream.ffmpegdirect: the plain FFmpeg HLS demuxer
    # leaves a read-ahead thread that isn't cancelled on CloseFile() for
    # live (non-EOF) streams, so Kodi blocks ~28s per pending segment
    # request before it can close/switch. ffmpegdirect tears the stream
    # down immediately instead. It doesn't exist for Kodi 18/Leia at all
    # (dropped from addon.xml <requires> for that reason -- a hard
    # dependency there would make the *whole* addon uninstallable on
    # Leia), so check for it at runtime and fall back to the plain
    # player -- with the old close-hang -- when it's missing.
    if xbmc.getCondVisibility('System.HasAddon(inputstream.ffmpegdirect)'):
        liz.setProperty('inputstream', 'inputstream.ffmpegdirect')
        liz.setProperty('inputstream.ffmpegdirect.is_realtime_stream', 'true')
        liz.setProperty('inputstream.ffmpegdirect.stream_mode', 'live')
        liz.setProperty('inputstream.ffmpegdirect.manifest_type', 'hls')
        # open_mode=curl routes I/O through Kodi's own CCurlFile instead
        # of raw FFmpeg AVFormat. Some CDNs (seen on a futbollibre
        # alt-server chained through tarjetarojita.xyz) silently
        # blackhole FFmpeg's bundled TLS client for ~30s while an
        # ordinary curl/requests call against the exact same URL and
        # headers succeeds in well under a second -- a TLS/client-
        # fingerprint block aimed at ffmpeg, not the headers.
        # CCurlFile's TLS stack isn't flagged the same way.
        # ...but curl mode only sends our headers with the playlist request:
        # FFmpeg's HLS demuxer then fetches the segments itself without
        # Referer/Origin, so CDNs that check them on every segment (the
        # quotarevival.net player behind tarjetarojita: 403) never start.
        # A resolver that returns its player page means "this CDN wants
        # these headers" -> let FFmpeg open it, it passes them to segments.
        liz.setProperty('inputstream.ffmpegdirect.open_mode',
                        'ffmpeg' if isinstance(result, tuple) else 'curl')
        liz.setProperty('inputstream.ffmpegdirect.default_url', stream_url)
    liz.setPath(stream_url)
    xbmc.Player().play(stream_url, liz, False)


def router(paramstring):
    params = dict(parse_qsl(paramstring))
    if params:
        if params['mode'] == 'events':
            # if time_to_update():
            #     fetch_and_store_channel_data()
            #     update_last_update_time()
            # else:
            #     print("Not yet time to check for updates.")
            get_events(params['url'])
        elif params['mode'] == 'get_streams':
            get_stream(params['name'], params['url'])
        elif params['mode'] == 'play_stream':
            resolve2(params['description'], params['url'])
        elif params['mode'] == 'settings':
            Open_settings()
        elif params['mode'] == 'clear':
            control.deleteFile(LAST_UPDATE_FILE)
            control.infoDialog(u"[COLOR gold]Fichiers effacés[/COLOR]", NAME,
                               ICON, 5000)
        elif params['mode'] == 'version':
            xbmc.executebuiltin('UpdateAddonRepos')
        elif params['mode'] == 'fmov_menu':
            fmov_menu()
        elif params['mode'] == 'vavoo_menu':
            vavoo_menu()
        elif params['mode'] == 'vavoo_diag':
            vavoo_diag()
        elif params['mode'] == 'fmov_search':
            fmov_search()
        elif params['mode'] == 'site_events':
            site_events_menu(params['url'])
        elif params['mode'] == 'site_streams':
            site_streams_menu(params['url'])
        elif params['mode'] == 'site_play':
            site_play(params['url'])
    else:
        Main_menu()


if __name__ == '__main__':
    try:
        router(sys.argv[2][1:])
    except Exception as e:
        log_error('router: {0}'.format(e))
        try:
            snippet = six.ensure_text(str(e), encoding='utf-8', errors='replace')
            if len(snippet) > 180:
                snippet = snippet[:180] + u'...'
            control.infoDialog(
                u'[COLOR red]{0}[/COLOR]'.format(snippet),
                NAME, ICON, 5000)
        except Exception:
            pass