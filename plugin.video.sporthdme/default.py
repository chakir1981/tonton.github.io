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
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import requests
from resources.modules import control, client
try:
    from resources.modules import dlch as dlch_mod
except ImportError:
    dlch_mod = None
import time
from dateutil.parser import parse
from dateutil.tz import gettz
from dateutil import parser, tz

# tvsport.guide is no longer a menu entry: it is the fallback schedule for
# ÉVÉNEMENTS EN DIRECT (see Live_fallback_url).

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
    filtered_out = 0
    for match in matches:
        channels = match.get("channels", [])
        sport = match.get('sport', '') or ''
        lname = match.get('league', None)
        lname = six.ensure_text(lname, encoding='utf-8', errors='replace') if lname else sport
        team1 = match.get('team1', None)
        team1 = six.ensure_text(team1, encoding='utf-8', errors='replace') if team1 else None
        team2 = match.get('team2', None)
        team2 = six.ensure_text(team2, encoding='utf-8', errors='replace') if team2 else None
        icon = match.get('team1Img', ICON)
        if fr_only and not _channels_have_french(channels):
            # Pas de chaîne FR sur le site : le match reste visible si les
            # chaînes FR 24/7 (beIN, RMC, Canal+…) peuvent le couvrir.
            _hint = u' '.join(x for x in (sport, lname, team1, team2) if x)
            if dlch_mod is None or not dlch_mod.has_suggestion(_hint):
                filtered_out += 1
                continue
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
        _sugg = dlch_mod.suggest(name) if dlch_mod is not None else []
        if _sugg:
            # Matchs sans source FR sur le site : liens de secours = chaînes
            # FR 24/7 (beIN, RMC, Canal+…), même style que les liens français.
            for _ch in _sugg:
                _pid, _logo = dlch_mod.CHANNELS[_ch]
                _title = u'[COLOR gold]{0}[/COLOR] - FR'.format(_ch)
                info = {'title': _title, 'sorttitle': '', 'plot': name}
                addDir(_title, 'dlch://' + _pid, 'play_stream', _logo or ICON, name,
                       isFolder=False, infoLabels=info)
            xbmcplugin.setContent(_handle, 'videos')
            xbmcplugin.endOfDirectory(_handle)
            return
        if fr_only:
            control.infoDialog(u"[COLOR gold]Aucune source française pour ce match.[/COLOR]\n"
                               u"[COLOR lime]Réglage : « Uniquement les sources françaises » dans les paramètres.[/COLOR]",
                               NAME, ICON, 7000)
        else:
            control.infoDialog(u"[COLOR gold]Aucun lien disponible pour le moment.\n "
                               u"[COLOR lime]Réessayez plus tard ![/COLOR]", NAME, ICON, 5000)
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

    # NOUVELLE BRANCHE : chaînes FR 24/7 (dlch://premium{N}, grille Daddylive)
    if url.startswith('dlch://'):
        Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
        if dlch_mod is None:
            control.infoDialog(u'[COLOR red]Module chaînes FR indisponible.[/COLOR]', NAME, ICON, 5000)
            return
        try:
            _dl_domains = ADDON.getSetting('dl_domains') or dlch_mod.DEFAULT_DOMAINS
        except Exception:
            _dl_domains = dlch_mod.DEFAULT_DOMAINS
        _dl_domains = [d for d in _dl_domains.split(',') if d.strip()]
        _res = dlch_mod.resolve(url[7:], _dl_domains, client)
        if not _res:
            control.infoDialog(u'[COLOR red]Chaîne momentanément indisponible.[/COLOR]\n'
                               u'[COLOR gold]Mets à jour les domaines Daddylive dans les paramètres.[/COLOR]',
                               NAME, ICON, 7000)
            return
        _flink, _dom = _res
        stream_headers = {'Referer': 'https://{0}/'.format(_dom), 'Origin': 'https://{0}'.format(_dom),
                          'User-Agent': dlch_mod.DL_UA, 'Connection': 'keep-alive'}
        stream_url = xbmc_curl_encode(_flink, stream_headers)
    # NEW GLISCO/SANSAT BRANCH
    elif '//glisco' in url or '//sansat' in url or 'vertex' in url or 'nexa' in url or 'kora.st' in url:
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
        if mode == 'settings' or mode == 'version' or mode == 'clear' or mode == 'play_stream':
            isFolder = False
        else:
            liz.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(_handle, url=u, listitem=liz, isFolder=isFolder)


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