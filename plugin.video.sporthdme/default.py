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
from resources.modules import site_embedlivesports
from resources.modules import site_futbollibre
from resources.modules import site_futbolmov
from resources.modules import site_sportonline

# v3.7.6 — cache disque persistant (portage de la 3.7.4) : les caches en
# mémoire meurent à chaque navigation Kodi (nouveau processus) et chaque
# visite du menu re-téléchargeait la grille des matchs. fastcache garde la
# liste sur le disque du profil ; toute panne est silencieuse : l'add-on
# fonctionne alors exactement comme la 2.0.5, juste sans cache.
try:
    from resources.modules import fastcache as fastcache
except Exception:
    fastcache = None
import threading
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
# futbol-libre.mov: one main-menu entry -> its three lists (fmov_menu)
SITES.update((s.KEY, s) for s in site_futbolmov.SUBSITES)
# v3.7.12 — BÉTA SportOnline (réglage beta_sportonline) : enregistré dans
# SITES pour réutiliser site_play (résolution + lecture) tel quel.
SITES[site_sportonline.KEY] = site_sportonline

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
    # v2.0.5 — add-on recentré : seule la rubrique « ÉVÉNEMENTS EN DIRECT »
    # (affichée en blanc) est conservée au menu. Les rubriques bleues
    # « Événements en direct » (doublon embedlivesports), « Football Libre »
    # et « Football Libre MOV » ne sont plus proposées (les modules et les
    # modes du routeur restent en place, simplement sans entrée de menu).
    # v3.7.7 — préchauffage : la grille des matchs se télécharge en
    # arrière-plan dès l'écran d'accueil, pendant que l'utilisateur lit le
    # menu -> le premier clic sur « ÉVÉNEMENTS EN DIRECT » est instantané.
    _refresh_matches_bg(Live_url)
    # v3.7.8 — descriptions des entrées du menu (panneau d'information du skin)
    addDir(u'[B][COLOR white]ÉVÉNEMENTS EN DIRECT[/COLOR][/B]', Live_url, 'events', ICON, FANART, True,
           infoLabels={'title': u'Événements en direct',
                       'plot': u'Matchs en cours et à venir — grille du jour, sources françaises en priorité.'})
    # v3.7.12 — option BÉTA (réglage « BÉTA — Source SportOnline », désactivée
    # par défaut) : grille indépendante de DaddyLive, langue de chaque chaîne
    # déclarée par la source. L'entrée du menu n'apparaît que si le réglage
    # est activé : rien ne change pour l'utilisateur tant qu'il ne l'active pas.
    if _setting_bool('beta_sportonline', 'false'):
        addDir(u'[B][COLOR magenta]BÉTA — Matchs SportOnline[/COLOR][/B]', 'spo', 'spo_events', ICON, FANART, True,
               infoLabels={'title': u'BÉTA — Matchs SportOnline',
                           'plot': u"Source de test indépendante de DaddyLive : grille du jour (aujourd'hui + demain, heure UK convertie en heure locale), langue de chaque chaîne déclarée par la source (FR fiable). Désactivable dans Paramètres > « BÉTA — Source SportOnline »."})
    # addDir('[B][COLOR gold]Channels 24/7[/COLOR][/B]', 'https://1.livesoccer.sx/program.php', 14, ICON, FANART, '')
    # addDir('[B][COLOR gold]Alternative VIEW [/COLOR][/B]', '', '', ICON, FANART, '')
    # addDir('[B][COLOR gold]Alternative LIVE EVENTS[/COLOR][/B]', Alt_url, 15, ICON, FANART, '')
    # addDir('[B][COLOR white]SPORTS[/COLOR][/B]', '', 3, ICON, FANART, '')
    # addDir('[B][COLOR white]BEST LEAGUES[/COLOR][/B]', '', 2, ICON, FANART, '')
    addDir(u'[B][COLOR gold]Paramètres[/COLOR][/B]', 'set', 'settings', ICON, FANART, False,
           infoLabels={'title': u'Paramètres',
                       'plot': u'Uniquement les sources françaises, fuseau horaire, affichage (emoji, compteurs, groupement).'})
    addDir(u"[B][COLOR gold]Vider les données de l'add-on[/COLOR][/B]", 'clear', 'clear', ICON, FANART, False,
           infoLabels={'title': u"Vider les données de l'add-on",
                       'plot': u'Supprime le cache (grille, lecteurs) et la base de chaînes locales.'})
    addDir(u'[B][COLOR gold]Version : [COLOR lime]{}[/COLOR][/B]'.format(vers), '', 'version', ICON, FANART, False,
           infoLabels={'title': u'Version ' + six.ensure_text(vers, encoding='utf-8', errors='replace'),
                       'plot': u'Vérifier les mises à jour des dépôts.'})
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _matches_from_page(url):
    data = requests.get(url, timeout=12).text
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


def _fetch_matches(url):
    """v3.7.7 — télécharge la liste des matchs (page du guide, puis repli
    JSON tvsport.guide) et l'écrit dans le cache disque (2 min). Retourne la
    liste ou None ; toute panne réseau est silencieuse."""
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
                                   timeout=12).json().get('matches')
        except Exception as e:
            log_info('events fallback {0} failed: {1!r}'.format(Live_fallback_url, e))
    if matches and fastcache is not None:
        fastcache.fset('ev:' + url, matches)
        fastcache.flush()
    return matches


_WARM_INFLIGHT = set()
_WARM_LOCK = threading.Lock()


def _refresh_matches_bg(url):
    """v3.7.7 — lance le (re)téléchargement de la grille en ARRIÈRE-PLAN
    (fil daemon) : l'affichage, lui, part aussitôt du cache — la liste à
    jour sera là à la navigation suivante. Un seul téléchargement à la fois
    par URL (anti-doublon accueil + menu)."""
    def _run():
        with _WARM_LOCK:
            if url in _WARM_INFLIGHT:
                return
            _WARM_INFLIGHT.add(url)
        try:
            _fetch_matches(url)
        except Exception:
            pass
        finally:
            with _WARM_LOCK:
                _WARM_INFLIGHT.discard(url)
    try:
        t = threading.Thread(target=_run)
        t.daemon = True
        t.start()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# v3.7.14 — BÉTA SportOnline INTÉGRÉE à la grille « ÉVÉNEMENTS EN DIRECT »
# (réglage « BÉTA — Source SportOnline » activé) : les matchs de la source
# BÉTA absents du guide (ex. Ligue 2 : Reims vs Sochaux, Red Star vs Nancy)
# apparaissent dans la MÊME liste, au MÊME format, dé-dupliqués contre le
# guide (mêmes équipes à ±20 min). Un problème réseau de la BÉTA n'affecte
# jamais la grille habituelle.
# ---------------------------------------------------------------------------

def _spo_norm_title(t):
    """Titre normalisé pour la dé-duplication (minuscules, alphanumériques)."""
    t = _plain(t or u'').lower()
    return re.sub(r'[^a-z0-9]+', u'', t)


def _spo_fetch_events():
    """Grille BÉTA avec cache disque 10 min (prog.txt change rarement en
    journée). Retourne [] si indisponible — jamais d'exception vers la grille."""
    events = None
    try:
        if fastcache is not None:
            events = fastcache.fget('spo:prog', 600)
    except Exception:
        events = None
    if events is None:
        try:
            events = site_sportonline.list_events()
        except Exception as e:
            log_info('spo merge fetch failed: {0!r}'.format(e))
            events = []
        try:
            if fastcache is not None and events:
                fastcache.fset('spo:prog', events)
        except Exception:
            pass
    return events or []


def _spo_channels_of(e):
    """Événement BÉTA -> liste de canaux compatibles get_stream() : dicts
    avec spo_canal (page canal à résoudre), name (chaîne), language (codes
    CSV déclarés par la source) et spo_title (titre brut pour la lecture)."""
    out = []
    title = six.ensure_text(e.get('title') or u'Match', encoding='utf-8', errors='replace')
    for srv in (e.get('servers') or []):
        try:
            lbl = six.ensure_text(srv[0], encoding='utf-8', errors='replace')
            url = six.ensure_text(srv[1], encoding='utf-8', errors='replace')
            langs = six.ensure_text(srv[2] if len(srv) > 2 else u'',
                                    encoding='utf-8', errors='replace')
        except Exception:
            continue
        out.append({'spo_canal': url, 'name': lbl, 'language': langs,
                    'spo_title': title})
    return out


def _spo_merge_into_event_list(event_list, fr_only):
    """Ajoute à la liste les matchs BÉTA absents du guide, dé-dupliqués
    (mêmes équipes à ±20 min), habillage EXACTEMENT identique à la boucle
    de get_events : heure cyan, équipes or, badge vert ● EN DIRECT, langues
    magenta (fr premier en gras) — plus un badge discret [BÉTA] en bout de
    ligne pour distinguer la source de test. Le réglage « Uniquement les
    sources françaises » s'applique aussi ici."""
    if not _setting_bool('beta_sportonline', 'false'):
        return 0
    events = _spo_fetch_events()
    if not events:
        return 0
    window = 20 * 60 * 1000
    seen = []
    for it in event_list:
        try:
            seen.append((_spo_norm_title(it[5].get('title')), int(it[1])))
        except Exception:
            continue
    added = 0
    for e in events:
        langs = [six.ensure_text(l, encoding='utf-8', errors='replace')
                 for l in (e.get('langs') or [])]
        if fr_only and u'fr' not in langs:
            continue
        ev_title = six.ensure_text(e['title'], encoding='utf-8', errors='replace')
        try:
            compare = int(e['start_ms'])
        except Exception:
            continue
        nkey = _spo_norm_title(ev_title)
        if any(nk == nkey and abs(c - compare) <= window for nk, c in seen):
            continue          # déjà dans le guide (même équipes, même créneau)
        seen.append((nkey, compare))
        is_live = e.get('status') == 'live'
        emo = _sport_emoji(e.get('sport'), ev_title)
        if is_live:
            m_color = 'lime'
            state = u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]'
        else:
            m_color = 'gold'
            state = u''
        cnt = u''
        if _setting_bool('show_langs'):
            cnt = _lang_suffix(langs)
        ftime = time_convert(compare / 1000)
        ftime_s = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        name = u'{0}{1} [COLOR {2}][B]{3}[/B][/COLOR]{4}{5} [COLOR magenta][BÉTA][/COLOR]'.format(
            (emo + u' ') if emo else u'', ftime_s, m_color, ev_title, state, cnt)
        # fiche du match (infoLabels) — mêmes lignes que la grille + source
        spo_sport = six.ensure_text(e.get('sport') or u'Sport',
                                    encoding='utf-8', errors='replace') or u'Sport'
        plot = [u'{0} — BÉTA SportOnline'.format(spo_sport)]
        plot.append(u'Début : {0}{1}'.format(_plain(ftime),
                                             u' — en direct' if is_live else u''))
        spo_channels = _spo_channels_of(e)
        chan_names = []
        for ch in spo_channels:
            lg = ch['language'].replace(',', ' ')
            chan_names.append(u'{0} ({1})'.format(ch['name'], lg) if lg else ch['name'])
        if chan_names:
            plot.append(u'Chaînes : ' + u', '.join(chan_names))
        plot.append(u'Source de test indépendante de DaddyLive (BÉTA).')
        info = {'title': _plain(ev_title), 'plot': u'\n'.join(plot),
                'genre': spo_sport}
        event_list.append([name, compare, spo_channels, ICON, spo_sport, info])
        added += 1
    if added:
        log_info('spo merge: {0} match(s) BETA ajoutes a la grille'.format(added))
    return added


def get_events(url):  # 5
    # v3.7.6/3.7.7 — menu toujours instantané : la liste des matchs vit sur
    # le disque du profil (fastcache, porté de la 3.7.4).
    #   1) fraîche (< 2 min)           -> affichée telle quelle ;
    #   2) plus ancienne (jusqu'à 3 h) -> affichée AUSSI, et re-téléchargée
    #      en arrière-plan pour la navigation suivante ;
    #   3) rien du tout                -> téléchargement direct (première
    #      visite ; l'accueil préchauffe déjà ce téléchargement).
    ck_ev = 'ev:' + url
    matches = fastcache.fget(ck_ev, 120) if fastcache is not None else None
    if not matches and fastcache is not None:
        matches = fastcache.fstale(ck_ev, 3 * 3600)
        if matches:
            _refresh_matches_bg(url)
    if not matches:
        matches = _fetch_matches(url)
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
        if fr_only and not _channels_have_french(channels):
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
        finished = (not is_live) and now_time_in_ms > compare + duration_in_ms

        # v3.7.8 — habillage : emoji du sport, badge d'état, compteur de
        # liens, et une fiche lisible (plot) pour le panneau d'information.
        emo = _sport_emoji(sport, lname)
        if is_live:
            m_color = 'lime'
            state = u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]'
        elif finished:
            m_color = 'grey'
            state = u' [COLOR grey]fini[/COLOR]'
        else:
            m_color = 'gold'
            state = u''
        # v3.7.11 — titre épuré : heure, équipes, langues. La compétition
        # (« France. Ligue 1 »…) SORT de la ligne du match (elle reste dans
        # la fiche ci-dessous, panneau d'informations du skin).
        # v3.7.10 — les langues du match s'affichent en MAJUSCULES dans une
        # couleur inédite (magenta) : « Lorient vs Brest FR », « Sassuolo vs
        # Fiorentina DE IT ». Remplace le compteur 3.7.8 qui comptait des
        # chaînes et non des liens (les miroirs un/deux/trois triplent
        # l'affichage -> chiffre trompeur).
        cnt = u''
        if _setting_bool('show_langs'):
            cnt = _lang_suffix(_match_langs(channels))
        ftime_s = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        name = u'{0}{1} [COLOR {2}][B]{3}[/B][/COLOR]{4}{5}'.format(
            (emo + u' ') if emo else u'', ftime_s, m_color, event, state, cnt)

        # fiche du match (infoLabels) — texte brut, sans balises Kodi
        plot = []
        if lname and lname != sport:
            plot.append(u'{0} — {1}'.format(sport or u'Sport', lname))
        elif sport:
            plot.append(sport)
        plot.append(u'Début : {0}{1}'.format(_plain(ftime),
                                             u' — en direct' if is_live else u''))
        chan_names = []
        for ch in (channels or [])[:6]:
            try:
                if isinstance(ch, dict):
                    nm = ch.get('name')
                else:
                    row = _db_channel(six.ensure_text(ch, encoding='utf-8', errors='replace'))
                    nm = row.get('channelName') if row else None
                if nm:
                    chan_names.append(six.ensure_text(nm, encoding='utf-8', errors='replace'))
            except Exception:
                continue
        if chan_names:
            plot.append(u'Chaînes : ' + u', '.join(chan_names))
        info = {'title': _plain(event), 'plot': u'\n'.join(plot),
                'genre': six.ensure_text(sport, encoding='utf-8', errors='replace') or u'Sport'}
        event_list.append([name, compare, channels, icon, sport, info])

        # streams = str(quote(base64.b64encode(six.ensure_binary(str(streams)))))
    # v3.7.14 — BÉTA SportOnline : les matchs de la source BÉTA absents du
    # guide (ex. Ligue 2 : Reims vs Sochaux) sont intégrés À CETTE MÊME
    # LISTE (réglage « BÉTA — Source SportOnline »), dé-dupliqués contre le
    # guide, même habillage ; un problème réseau de la BÉTA n'affecte pas
    # la grille habituelle.
    _spo_merge_into_event_list(event_list, fr_only)
    if not event_list:
        if fastcache is not None:
            fastcache.flush()          # persister la liste mise en cache
        if filtered_out:
            control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs du moment.[/COLOR]", NAME, ICON, 6000)
        return
    # v3.7.8 — tri par heure (comme avant) ou groupement par sport (réglage)
    if _setting_bool('group_by_sport', 'false'):
        events = sorted(event_list, key=lambda x: (six.ensure_text(x[4], encoding='utf-8', errors='replace').lower(), x[1]))
    else:
        events = sorted(event_list, key=lambda x: x[1])
    ctx_refresh = [(u'Actualiser la grille',
                    'RunPlugin({0}?mode=refresh_events)'.format(_url))]
    for event in deduplicate_events(events):
        streams = str(quote(base64.b64encode(six.ensure_binary(str(event[2])))))
        name = event[0]
        icon = event[3]
        addDir(name, streams, 'get_streams', icon, FANART, isFolder=True,
               infoLabels=event[5], context=ctx_refresh)

    if fastcache is not None:
        fastcache.flush()              # persister la liste mise en cache
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def deduplicate_events(events):
    unique_events = []
    seen = set()
    for event in events:
        # repr(channels) : hachable que les canaux soient des chaînes OU des
        # dict (tuple() d'un dict lèverait TypeError).
        unique_key = (event[0], event[1], repr(event[2]), event[3], event[4])
        if unique_key not in seen:
            seen.add(unique_key)
            unique_events.append(event)
    return unique_events


def refresh_events(url):
    """v3.7.8 — menu contextuel « Actualiser la grille » : re-télécharge la
    liste des matchs immédiatement (avant : il fallait attendre l'expiration
    du cache ou vider les données), puis redessine la liste affichée."""
    control.infoDialog(u"[COLOR skyblue]Actualisation de la grille…[/COLOR]", NAME, ICON, 3000)
    matches = _fetch_matches(url)
    if fastcache is not None:
        fastcache.flush()
    if not matches:
        control.infoDialog(u"[COLOR gold]Actualisation impossible pour le moment (réseau ?).[/COLOR]", NAME, ICON, 5000)
        return
    xbmc.executebuiltin('Container.Refresh')


# ---------------------------------------------------------------------------
# Filtrage « sources françaises » (réglage french_only, activé par défaut).
# Le site marque chaque chaîne d'un match avec un code pays (language='FR',
# 'GB', 'ES'...) : on garde uniquement les chaînes françaises ; les matchs
# sans aucune chaîne française disparaissent de la liste.
# ---------------------------------------------------------------------------
FRENCH_LANGS = ('fr', 'french', u'français', 'francais')
# v3.7.1 — codes pays/langue EXPLICITEMENT non français : si le guide marque
# la chaîne avec l'un de ces codes, elle n'est pas française même si son nom
# contient une marque FR (Canal+ Sport 3/5 polonaises lang='PL', TSN3 'CA',
# NFL Sunday Ticket 'US'…). Les codes inconnus restent jugés sur le nom.
NON_FR_LANGS = ('pl', 'us', 'gb', 'uk', 'de', 'es', 'it', 'pt', 'nl', 'dk', 'no',
                'se', 'fi', 'is', 'ie', 'gr', 'tr', 'ru', 'ua', 'cz', 'sk', 'ro',
                'rs', 'hr', 'bg', 'hu', 'si', 'ee', 'lv', 'lt', 'sa', 'ae', 'qa',
                'eg', 'ma', 'dz', 'tn', 'jp', 'cn', 'kr', 'in', 'au', 'nz', 'ca',
                'mx', 'br', 'ar', 'cl', 'co', 'pe', 'en', 'eng')
FRENCH_NAME_RE = re.compile(
    r'\bfr\b|\[fr\]|\(fr\)|french|fran[cç]ais|\bfrance\b|canal\+\s*foot|rmc\s*sport|'
    r'quipe|tf1\b|\bm6\b|\bw9\b|\btmc\b|ligue1'
    # v3.6.1 — chaînes FR sans suffixe « France » ni champ langue :
    r'|bein sports (max )?\d+ france|eurosport \d+ france'
    # v3.7.1 — « canal+ » seul ne suffit plus : les Canal+ Sport 1-5 et
    # Canal+ 1 numérotés sont des chaînes ÉTRANGÈRES (Pologne…) ; la France
    # n'a que Canal+ Sport (sans numéro) et Canal+ Sport 360. On exclut donc
    # tout « canal+ » suivi d'un numéro nu ou de « sport 1-5 » (360 reste FR).
    r'|canal\+(?! ?sport ?[1-9]\b)(?! ?[1-9]\b)'
    r'|tennis\+|golf\+|\bfoot\+|automoto|motogp|infosport'
    # v3.6.2 — chaînes FR du guide sans marque explicite (6ter, Arte…) :
    r'|6ter|\barte\b|\bc8\b|\blci\b|\bbfm\b')

# v3.6.1 — les variantes ÉTRANGÈRES des mêmes groupes (Canal+ Sport SK/CZ/
# Poland, DAZN Spain/DE, beIN Arabic/Malaysia…) ne sont pas des sources
# françaises même si leur nom contient une marque FR. Testé sur le nom en
# minuscules, APRÈS le test de la langue explicite.
NOT_FRENCH_NAME_RE = re.compile(
    r'\b(sk|cz|pl|de|es|pt|it|nl|ro|rs|hr|bg|gr|tr|ua|ru)\b'
    r'|poland|spain|deutschland|germany|portugal|italy|belgium|netherlands'
    r'|nederland|romania|serbia|croatia|bulgaria|greece|hungary|slovakia'
    r'|slovenia|ukraine|russia|arabia|arabic|mena|t[uü]rkiye|turkey|malaysia'
    r'|australia|qatar|israel|kazakhstan|english|espa[nñ]ol|italiano'
    r'|portugu[eè]s|deutsch|nederlands|scandinavia|sweden|norway|denmark'
    r'|finland|usa',
    re.IGNORECASE)


def _french_only():
    """Réglage « Uniquement les sources françaises » (activé par défaut)."""
    try:
        return (ADDON.getSetting('french_only') or 'true').lower() == 'true'
    except Exception:
        return True


def _is_french_source(name, lang):
    """True si la chaîne/le serveur est en français (code pays, langue ou nom).
    v3.7.1 : un code langue explicite non français (PL, US, GB…) rejette la
    chaîne AVANT tout test de nom — fin des Canal+ Sport 3/5 polonaises."""
    try:
        lang_s = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip().lower()
        name_s = six.ensure_text(name or u'', encoding='utf-8', errors='replace').lower()
    except Exception:
        return False
    if lang_s in FRENCH_LANGS:
        return True
    if lang_s and lang_s in NON_FR_LANGS:
        return False
    if name_s and NOT_FRENCH_NAME_RE.search(name_s):
        return False
    return bool(name_s and FRENCH_NAME_RE.search(name_s))


def _db_channel(channel_name):
    """Cherche une chaîne par nom dans la base locale (channels.json).
    v3.7.8 : la base est indexée UNE fois par processus (dict nom -> entrée)
    au lieu d'un parcours linéaire à chaque appel — la grille de 100 matchs
    s'affiche nettement plus vite quand la base de chaînes est grosse."""
    try:
        wanted = (channel_name or '').replace(' ', '')
        return _db_index().get(wanted)
    except Exception:
        return None


_DB_INDEX = {'built': False, 'map': {}}


def _db_index():
    if _DB_INDEX['built']:
        return _DB_INDEX['map']
    idx = {}
    try:
        for channel in load_data_from_json():
            idx[channel.get('channelName', '').replace(' ', '')] = channel
    except Exception:
        pass
    _DB_INDEX['map'] = idx
    _DB_INDEX['built'] = True
    return idx


_LANG_ALIASES = {'french': u'fr', u'français': u'fr', 'francais': u'fr',
                 'english': u'en', 'eng': u'en'}


def _channel_lang(name, lang):
    """v3.7.10 — code langue affichable d'une chaîne (« fr », « de », « it »…).
    Le code explicite du guide/de la base fait foi (règle 3.7.1 : un code
    non français rejette la chaîne) ; sans code, une chaîne reconnue
    française par son nom vaut « fr », une inconnue renvoie '' (rien à
    afficher plutôt qu'un code faux)."""
    lang_s = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip().lower()
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
    """v3.7.10 — langues distinctes d'un match, « fr » d'abord puis ordre
    alphabétique ('fr' ; 'de', 'it' ; 'de it us'…). Mêmes sources que la liste
    des liens à l'ouverture (dicts du guide ou base locale channels.json) :
    ce qui est écrit dans le titre est exactement ce qu'on trouve dedans."""
    found = []
    try:
        for ch in channels or []:
            if isinstance(ch, dict):
                tag = _channel_lang(ch.get('name'), ch.get('language'))
            else:
                nm = six.ensure_text(ch, encoding='utf-8', errors='replace')
                row = _db_channel(nm)
                if row is not None:
                    tag = _channel_lang(row.get('channelName'), row.get('language'))
                else:
                    tag = _channel_lang(nm, u'')
            if tag and tag not in found:
                found.append(tag)
    except Exception:
        pass
    rest = sorted(l for l in found if l != u'fr')
    return ([u'fr'] + rest) if u'fr' in found else rest


def _lang_suffix(langs):
    """v3.7.11 — suffixe du titre : codes langue en MAJUSCULES (FR, DE, IT…)
    en MAGENTA — couleur non utilisée ailleurs dans l'add-on (or = équipes,
    vert = EN DIRECT et FR de la liste des liens, cyan = heure, gris =
    miroirs) ; « FR » toujours premier et en gras, les autres suivent dans
    l'ordre alphabétique. '' si aucune langue identifiée (titre inchangé)."""
    parts = []
    for l in langs:
        if l == u'fr':
            parts.append(u'[COLOR magenta][B]FR[/B][/COLOR]')
        else:
            parts.append(u'[COLOR magenta]{0}[/COLOR]'.format(l.upper()))
    return (u' ' + u' '.join(parts)) if parts else u''


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


# ---------------------------------------------------------------------------
# v3.7.8 — ergonomie / visuel
# ---------------------------------------------------------------------------

def _setting_bool(key, default='true'):
    """Petit accesseur réglage booléen (settings.xml), tolérant aux pannes."""
    try:
        return (ADDON.getSetting(key) or default).strip().lower() == 'true'
    except Exception:
        return default == 'true'


def _plain(text):
    """Retire les balises Kodi ([B] [I] [CR] [COLOR x]…) pour les champs
    infoLabels (le panneau d'information du skin les affiche en clair)."""
    try:
        return re.sub(r'\[/?(?:B|I|CR|UPPERCASE|LOWERCASE|COLOR[^\]]*)\]',
                      u'', text or u'', flags=re.IGNORECASE).strip()
    except Exception:
        return text or u''


# Ordre IMPORTANT : 'american' avant 'foot' (american football), 'table'
# avant 'tennis' (table tennis), 'nba'/'nhl'/'ufc' etc. d'abord car plus précis.
_SPORT_EMOJI = (
    (u'american', u'🏈'), (u'nfl', u'🏈'),
    (u'table', u'🏓'), (u'ping', u'🏓'),
    (u'basket', u'🏀'), (u'nba', u'🏀'),
    (u'foot', u'⚽'), (u'soccer', u'⚽'), (u'calcio', u'⚽'),
    (u'futbol', u'⚽'), (u'fussball', u'⚽'),
    (u'tennis', u'🎾'), (u'atp', u'🎾'), (u'wta', u'🎾'),
    (u'wimbledon', u'🎾'), (u'roland', u'🎾'),
    (u'hockey', u'🏒'), (u'nhl', u'🏒'),
    (u'baseball', u'⚾'), (u'mlb', u'⚾'),
    (u'volley', u'🏐'),
    (u'handball', u'🤾'), (u'hand', u'🤾'),
    (u'boxe', u'🥊'), (u'boxing', u'🥊'), (u'ufc', u'🥊'),
    (u'mma', u'🥊'), (u'fight', u'🥊'),
    (u'rugby', u'🏉'),
    (u'cricket', u'🏏'),
    (u'golf', u'⛳'),
    (u'f1', u'🏎'), (u'formula', u'🏎'), (u'formule', u'🏎'),
    (u'moto', u'🏍'), (u'nascar', u'🏎'),
    (u'dart', u'🎯'),
    (u'snooker', u'🎱'), (u'billard', u'🎱'), (u'pool', u'🎱'),
    (u'cycl', u'🚴'),
    (u'swim', u'🏊'), (u'natation', u'🏊'), (u'polo', u'🤽'),
    (u'ski', u'⛷'), (u'snowboard', u'🏂'),
    (u'chess', u'♟'), (u'échec', u'♟'), (u'echec', u'♟'),
    (u'esport', u'🎮'), (u'e-sport', u'🎮'), (u'counter', u'🎮'),
    (u'dota', u'🎮'), (u'league of legends', u'🎮'),
    (u'athlet', u'🏃'), (u'athlét', u'🏃'), (u'marathon', u'🏃'),
)


def _sport_emoji(sport, league):
    """Icône emoji du sport (réglage « Icônes emoji des sports »)."""
    if not _setting_bool('show_emoji'):
        return u''
    hay = u'{0} {1}'.format(sport or u'', league or u'').lower()
    for kw, emo in _SPORT_EMOJI:
        if kw in hay:
            return emo
    return u''


def _chan_label(chan, lang, fr):
    """Libellé d'un lien dans la liste d'un match : chaîne en or, langue en
    vert quand c'est du français (gris sinon)."""
    lang = six.ensure_text(lang or u'', encoding='utf-8', errors='replace').strip()
    tag = u''
    if fr:
        tag = u'[COLOR lime]{0}[/COLOR]'.format(lang.upper() or u'FR')
    elif lang:
        tag = u'[COLOR dimgray]{0}[/COLOR]'.format(lang)
    return u'[COLOR gold]{0}[/COLOR]{1}'.format(chan, u' ' + tag if tag else u'')


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
                fr = _is_french_source(chan, lang)
                if fr_only and not fr:
                    continue
                # v3.7.8 — chaîne en or, langue en vert FR / gris
                sstreams.append((link, _chan_label(chan, lang, fr)))

        else:
            # v3.7.14 — canal BÉTA SportOnline (dict avec spo_canal) : la
            # langue est DÉCLARÉE par la source (« fr,es ») -> test direct,
            # et la lecture passe par le résolveur site_play (module
            # site_sportonline), pas par resolve2 (résolveur DaddyLive).
            if event.get('spo_canal'):
                langs = [l for l in six.ensure_text(
                    event.get('language', ''), encoding='utf-8',
                    errors='replace').split(',') if l]
                fr = u'fr' in langs
                if fr_only and not fr:
                    continue
                chan = six.ensure_text(event.get('name', 'HD'),
                                       encoding='utf-8', errors='replace')
                if fr:
                    tag = u'[COLOR lime]FR[/COLOR]'
                elif langs:
                    tag = u'[COLOR dimgray]{0}[/COLOR]'.format(
                        u' '.join(l.upper() for l in langs))
                else:
                    tag = u''
                label = u'[COLOR gold]{0}[/COLOR]{1}'.format(chan, u' ' + tag if tag else u'')
                payload = json.dumps({'k': 'spo',
                                      'u': six.ensure_text(event.get('spo_canal'), encoding='utf-8', errors='replace'),
                                      't': six.ensure_text(event.get('spo_title') or u'', encoding='utf-8', errors='replace'),
                                      'p': ''})
                sstreams.append((payload, label))
                continue
            fr = _is_french_source(event.get('name'), event.get('language'))
            if fr_only and not fr:
                continue
            link = event.get('links', [])
            lang = six.ensure_text(event.get('language', ''), encoding='utf-8', errors='replace')
            chan = six.ensure_text(event.get('name', ''), encoding='utf-8', errors='replace')
            # v3.7.8 — chaîne en or, langue en vert FR / gris
            for url_ in link:
                sstreams.append((url_, _chan_label(chan, lang, fr)))

    if len(sstreams) < 1:
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
            # v3.7.8 — liens numérotés (1. 2. 3.…) pour s'y retrouver, miroir
            # (un/deux/trois) en gris discret, fiche en texte propre.
            for idx, i in enumerate(sstreams, 1):
                title, link = i[1], i[0]
                # v3.7.14 — payload JSON = canal BÉTA SportOnline -> résolveur
                # site_play ; sinon lien brut -> résolveur DaddyLive.
                is_spo = link.startswith('{')
                lbl = _link_label(link) if not is_spo else None
                if lbl and 'vecdn' not in link and str(link) != str(title):
                    title = u'{0} [COLOR dimgray]· {1}[/COLOR]'.format(title, lbl)
                num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
                info = {'title': _plain(title), 'sorttitle': '', 'plot': _plain(name)}
                addDir(num + title, link, 'site_play' if is_spo else 'play_stream',
                       ICON, name, isFolder=False, infoLabels=info)
            xbmcplugin.setContent(_handle, 'videos')
            xbmcplugin.endOfDirectory(_handle)

        else:
            link = sstreams[0][0]
            # v3.7.14 — payload JSON = canal BÉTA SportOnline -> site_play
            if link.startswith('{'):
                site_play(link)
            else:
                resolve2(name, link)


def xbmc_curl_encode(url, headers):
    return "{}|{}".format(url, urlencode(headers))


def _resolve2_impl(name, url):
    """v3.7.8 : extrait l'URL finale du flux et la RETOURNE (la lecture,
    elle, est faite par resolve2() qui affiche la roue d'attente pendant
    l'extraction)."""
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

        # v3.7.7 — clic accéléré : les réponses api/player.php (l'URL du
        # lecteur embarqué de chaque miroir) sont stables pendant toute la
        # durée d'un événement -> cache disque 30 min (fastcache) et
        # récupération EN PARALLÈLE sur tous les miroirs en une seule salve.
        # Avant : 2 à 4 serveurs interrogés l'un après l'autre, jusqu'à
        # ~2 min quand un miroir est tombé ; maintenant la salve attend le
        # plus lent (souvent < 2 s) et le 2e clic ne rappelle plus l'api.
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
                        '{0}://{1}/api/player.php?id={2}'.format(uri0.scheme, h, chan_id),
                        referer='{0}://{1}/'.format(uri0.scheme, h), timeout='12')
                    if r:
                        api_results[h] = six.ensure_text(r, encoding='utf-8', errors='replace')
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
                    # ni cache ni salve : un ultime essai direct, vite abandonné
                    api_resp = six.ensure_text(client.request(
                        '{0}://{1}/api/player.php?id={2}'.format(uri0.scheme, host, chan_id),
                        referer=api_ref, timeout='12') or '')
                cand_frame = json.loads(api_resp)['url']
                cfg_url = cand_frame + ('&' if '?' in cand_frame else '?') + 'ppcfg=1&_=' + str(int(_time.time() * 1000))
                try:
                    embed_html = six.ensure_str(
                        client.request(cfg_url, referer=api_ref, headers={'User-Agent': ua_win},
                                       timeout='12') or '')
                except Exception:
                    embed_html = ''  # e.g. 403 on ?ppcfg=1 -> plain page below
                method, cand = _extract_stream(embed_html)
                if not cand:  # ?ppcfg=1 refused -> plain frame page
                    try:
                        method, cand = _extract_stream(six.ensure_str(
                            client.request(cand_frame, referer=api_ref,
                                           headers={'User-Agent': ua_win}, timeout='12')))
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
        r = six.ensure_str(client.request(url, timeout='12'))
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
            r = six.ensure_str(requests.get(frame, params=params, headers=hdr, timeout=12).content)
        if 'fid=' in r:
            regex = '''<script>fid=['"](.+?)['"].+?text/javascript.*?src=['"](.+?)['"]></script>'''
            vid, getembed = re.findall(regex, r, re.DOTALL)[0]
            getembed = 'https:' + getembed if getembed.startswith('//') else getembed
            embed = six.ensure_str(client.request(getembed, timeout='12'))
            embed = re.findall(r'''document.write.+?src=['"](.+?player)=''', embed, re.DOTALL)[0]
            host = '{}=desktop&live={}'.format(embed, str(vid))
            data = six.ensure_str(client.request(host, referer=referer, timeout='12'))
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
            data = six.ensure_str(client.request(frame, referer=url, output=url, timeout='12'))
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
                        link_data = six.ensure_str(client.request(link, timeout='12'))
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
                        client.request(auth_url, referer=ref_for_headers, timeout='12')
                    except Exception:
                        pass

                    # server_lookup για server_key
                    lookup_url = urljoin(origin, '/server_lookup.php?channel_id=' + quote_plus(channel_key))
                    body = six.ensure_str(client.request(lookup_url, referer=ref_for_headers, timeout='12'))

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
                            chunk = client.request(u, referer=ref_for_headers, timeout='12')
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
                        ea = six.ensure_text(client.request(ea, timeout='12')).split('=')[1]
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
    return stream_url


def resolve2(name, url):
    """v3.7.8 — roue d'attente pendant la résolution (plus d'écran qui semble
    figé), message clair si aucun flux n'est extrait, lecture avec un titre
    propre et le bon type MIME HLS (démarrage plus fiable du lecteur)."""
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        stream_url = _resolve2_impl(name, url)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not stream_url:
        raise Exception(u'Flux introuvable pour ce lien — réessayez ou choisissez un autre serveur.')
    liz = xbmcgui.ListItem(six.ensure_str(_plain(name), encoding='utf-8', errors='replace'))
    liz.setArt({'icon': ICON, 'thumb': ICON, 'poster': ICON, 'fanart': FANART})
    liz.setInfo('video', {'title': _plain(name)})
    liz.setProperty("IsPlayable", "true")
    liz.setContentLookup(False)
    liz.setMimeType('application/vnd.apple.mpegurl')
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
    # v3.7.8 — lecture disque une seule fois par version du fichier (mtime) :
    # la base est lue en boucle par l'index de chaînes et le compteur FR.
    try:
        mtime = xbmcvfs.Stat(JSON_FILE_PATH).st_mtime()
    except Exception:
        mtime = None
    if _DB_CACHE['data'] is not None and _DB_CACHE['mtime'] == mtime:
        return _DB_CACHE['data']
    if not xbmcvfs.exists(JSON_FILE_PATH):
        return []
    f = control.openFile(JSON_FILE_PATH, 'r')
    contents = f.read()
    f.close()
    try:
        data = json.loads(contents)
    except Exception:
        return []
    _DB_CACHE['mtime'] = mtime
    _DB_CACHE['data'] = data
    _DB_INDEX['built'] = False      # l'index suit la nouvelle version
    return data


_DB_CACHE = {'mtime': None, 'data': None}


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


def addDir(name, url, mode, iconimage, description, isFolder=True, infoLabels=None, context=None):
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
    if context:
        # v3.7.8 — menu contextuel (appui long / touche C) sur l'élément
        try:
            liz.addContextMenuItems(context)
        except Exception:
            pass
    if not isFolder:
        if mode == 'settings' or mode == 'version' or mode == 'clear' or mode == 'play_stream' or mode == 'site_play':
            isFolder = False
        else:
            liz.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(_handle, url=u, listitem=liz, isFolder=isFolder)


def site_events_menu(key):
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
    if not events:
        # v3.7.6 — message clair au lieu d'une page vide quand le site ne
        # répond pas ou n'a aucun événement.
        control.infoDialog(u"[COLOR red]Aucun événement disponible pour le moment.[/COLOR]",
                           NAME, ICON, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return
    fr_only = _french_only()
    shown = 0
    for e in events:
        if fr_only and e.get('servers') and not _servers_have_french(e.get('servers')):
            continue
        _add_site_event(key, site, e)
        shown += 1
    if events and not shown:
        control.infoDialog(u"[COLOR gold]Aucune source française parmi les matchs de ce site.[/COLOR]", NAME, ICON, 6000)
    xbmcplugin.setContent(_handle, 'movies')
    xbmcplugin.endOfDirectory(_handle)


def _add_site_event(key, site, e):
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
    # carry the event's title + poster down the chain so the servers menu
    # and the player show the match, not the generic SportHD art.
    payload = json.dumps({'k': key, 's': e['servers'],
                          't': e['title'], 'p': e['poster']})
    addDir(label, payload, 'site_streams', e['poster'] or ICON, e['title'], True)


# ---------------------------------------------------------------------------
# v3.7.12 — BÉTA « SportOnline » (réglage beta_sportonline, désactivé par
# défaut) : source de test INDÉPENDANTE de DaddyLive. Grille texte du jour
# (sportsonline.st/prog.txt) où chaque chaîne DÉCLARE sa langue (« HD4 GREEK
# & FRENCH ») -> détection FR fiable. Affichage calqué sur get_events()
# (heure cyan, équipes or, langues magenta, ● EN DIRECT), liste de liens
# numérotée comme get_stream(), lecture via site_play (résolveur du module
# site_sportonline). Rien ne change tant que le réglage reste désactivé.
# ---------------------------------------------------------------------------


def spo_events_menu():
    if not _setting_bool('beta_sportonline', 'false'):
        control.infoDialog(u"[COLOR gold]Source BÉTA désactivée — active « BÉTA — Source SportOnline » dans Paramètres.[/COLOR]",
                           NAME, ICON, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return
    try:
        events = site_sportonline.list_events()
    except Exception as e:
        log_info('spo events failed: {0!r}'.format(e))
        events = []
    if not events:
        control.infoDialog(u"[COLOR red]Grille BÉTA SportOnline indisponible pour le moment (réseau ?).[/COLOR]",
                           NAME, ICON, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return
    fr_only = _french_only()
    event_list = []
    filtered_out = 0
    for e in events:
        langs = [six.ensure_text(l, encoding='utf-8', errors='replace')
                 for l in (e.get('langs') or [])]
        if fr_only and u'fr' not in langs:
            filtered_out += 1
            continue
        # habillage identique à get_events() (3.7.11) : heure cyan, équipes
        # or, badge EN DIRECT, langues magenta (fr premier, en gras)
        compare = int(e['start_ms'])
        ftime = time_convert(compare / 1000)
        is_live = e.get('status') == 'live'
        emo = _sport_emoji(e.get('sport'), e.get('title'))
        if is_live:
            m_color = 'lime'
            state = u' [COLOR lime][B]● EN DIRECT[/B][/COLOR]'
        else:
            m_color = 'gold'
            state = u''
        cnt = u''
        if _setting_bool('show_langs'):
            cnt = _lang_suffix(langs)
        ev_title = six.ensure_text(e['title'], encoding='utf-8', errors='replace')
        ftime_s = u'[COLOR cyan]{}[/COLOR]'.format(ftime)
        name = u'{0}{1} [COLOR {2}][B]{3}[/B][/COLOR]{4}{5}'.format(
            (emo + u' ') if emo else u'', ftime_s, m_color, ev_title, state, cnt)
        # fiche du match (infoLabels) — même structure que get_events()
        plot = [u'BÉTA SportOnline — {0}'.format(
            six.ensure_text(e.get('sport') or u'Sport', encoding='utf-8', errors='replace'))]
        plot.append(u'Début : {0}{1}'.format(_plain(ftime),
                                             u' — en direct' if is_live else u''))
        chan_names = []
        for srv in (e.get('servers') or []):
            try:
                lbl = six.ensure_text(srv[0], encoding='utf-8', errors='replace')
                langs_s = six.ensure_text(srv[2] if len(srv) > 2 else u'',
                                          encoding='utf-8', errors='replace').replace(',', ' ')
                chan_names.append(u'{0} ({1})'.format(lbl, langs_s) if langs_s else lbl)
            except Exception:
                continue
        if chan_names:
            plot.append(u'Chaînes : ' + u', '.join(chan_names))
        plot.append(u'Source de test indépendante de DaddyLive (réglage BÉTA).')
        info = {'title': _plain(ev_title), 'plot': u'\n'.join(plot),
                'genre': six.ensure_text(e.get('sport') or u'Sport',
                                         encoding='utf-8', errors='replace') or u'Sport'}
        event_list.append([name, compare, e.get('servers') or [], info])
    if not event_list:
        if filtered_out:
            control.infoDialog(u"[COLOR gold]Aucune chaîne française dans la grille BÉTA du moment.[/COLOR]",
                               NAME, ICON, 6000)
        else:
            control.infoDialog(u"[COLOR gold]Grille BÉTA vide pour le moment.[/COLOR]", NAME, ICON, 5000)
        xbmcplugin.endOfDirectory(_handle)
        return
    event_list.sort(key=lambda x: x[1])
    for ev in event_list:
        payload = json.dumps({'t': ev[3].get('title') or u'', 's': ev[2]})
        addDir(ev[0], payload, 'spo_streams', ICON, FANART, True, infoLabels=ev[3])
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def spo_streams_menu(payload):
    d = json.loads(payload)
    title = six.ensure_text(d.get('t') or u'Match', encoding='utf-8', errors='replace')
    fr_only = _french_only()
    items = []
    for idx, srv in enumerate(d.get('s') or [], 1):
        try:
            lbl = six.ensure_text(srv[0], encoding='utf-8', errors='replace')
            url = six.ensure_text(srv[1], encoding='utf-8', errors='replace')
            langs = [l for l in six.ensure_text(srv[2] if len(srv) > 2 else u'',
                                                encoding='utf-8', errors='replace').split(',') if l]
        except Exception:
            continue
        fr = u'fr' in langs
        if fr_only and not fr:
            continue
        # même style que get_stream() : numéro discret, chaîne en or,
        # langue française en VERT (étrangère en gris), hôte en gris
        if fr:
            tag = u'[COLOR lime]FR[/COLOR]'
        elif langs:
            tag = u'[COLOR dimgray]{0}[/COLOR]'.format(
                u' '.join(l.upper() for l in langs))
        else:
            tag = u''
        t = u'[COLOR gold]{0}[/COLOR]{1}'.format(lbl, u' ' + tag if tag else u'')
        hostlbl = _link_label(url)
        if hostlbl:
            t = u'{0} [COLOR dimgray]· {1}[/COLOR]'.format(t, hostlbl)
        num = u'[COLOR dimgray]{0}.[/COLOR] '.format(idx)
        info = {'title': _plain(t), 'sorttitle': '', 'plot': title}
        items.append((num + t, json.dumps({'k': site_sportonline.KEY, 'u': url,
                                           't': title, 'p': ''}), info))
    if not items:
        control.infoDialog(u"[COLOR gold]Aucune chaîne française pour ce match (BÉTA).[/COLOR]\n"
                           u"[COLOR lime]Réglage : « Uniquement les sources françaises ».[/COLOR]",
                           NAME, ICON, 7000)
        xbmcplugin.endOfDirectory(_handle)
        return
    for lbl, url, info in items:
        addDir(lbl, url, 'site_play', ICON, title, isFolder=False, infoLabels=info)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def fmov_menu():
    for s in site_futbolmov.SUBSITES:
        addDir(u'[B][COLOR deepskyblue]{0}[/COLOR][/B]'.format(s.NAME), s.KEY,
               'site_events', ICON, FANART, True,
               infoLabels={'title': s.NAME, 'plot': s.DESC})
    addDir(u'[B][COLOR gold]Rechercher[/COLOR][/B]', 'fmov', 'fmov_search', ICON, FANART, True,
           infoLabels={'title': u'Rechercher', 'plot': u'Rechercher dans les trois listes (équipe, championnat…).'})
    xbmcplugin.endOfDirectory(_handle)


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


def site_streams_menu(payload):
    d = json.loads(payload)
    poster = d.get('p') or ICON
    title = six.ensure_text(d.get('t', ''), encoding='utf-8', errors='replace')
    fr_only = _french_only()
    shown = 0
    for sname, surl in d['s']:
        # server names carry accents too (e.g. "Señal 1") -- byte-str .format()
        # on a unicode arg raises UnicodeEncodeError in py2, so keep it unicode.
        sname = six.ensure_text(sname, encoding='utf-8', errors='replace')
        if fr_only and not _is_french_source(sname, ''):
            continue
        info = {'title': u'{0} | {1}'.format(title, sname), 'plot': title}
        addDir(u'[B]{0}[/B]'.format(sname),
               json.dumps({'k': d['k'], 'u': surl, 't': title, 'p': d.get('p', '')}),
               'site_play', poster, title, isFolder=False, infoLabels=info)
        shown += 1
    if not shown:
        control.infoDialog(u"[COLOR gold]Aucune source française pour ce match.[/COLOR]", NAME, ICON, 6000)
    xbmcplugin.setContent(_handle, 'videos')
    xbmcplugin.endOfDirectory(_handle)


def site_play(payload):
    d = json.loads(payload)
    site = SITES[d['k']]
    title = d.get('t') or NAME
    poster = d.get('p') or ICON
    Dialog.notification(NAME, u"[COLOR skyblue]Résolution du lien en cours…[/COLOR]", ICON, 2000, False)
    # v3.7.8 — roue d'attente pendant la résolution (sites annexes)
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        result = site.resolve(d['u'])
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if not result:
        raise Exception('could not resolve stream for ' + d['u'])
    # resolve() normally returns just the m3u8 string; sites that chain
    # through intermediate hosts (e.g. futbollibre's tarjetarojita.xyz
    # wrapper) return (m3u8, final_page_url) since the CDN token's own
    # host, not the originally-clicked link, is what the player needs
    # for Referer/Origin.
    flink, referer_url = result if isinstance(result, tuple) else (result, d['u'])
    origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(referer_url))
    stream_headers = {'Referer': origin + '/', 'Origin': origin, 'User-Agent': site.UA}
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
        elif params['mode'] == 'refresh_events':
            # v3.7.8 — menu contextuel « Actualiser la grille »
            refresh_events(params.get('url') or Live_url)
        elif params['mode'] == 'clear':
            control.deleteFile(LAST_UPDATE_FILE)
            # v3.7.6 — le cache disque fastcache fait partie des données :
            # « Vider les données de l'add-on » le supprime aussi.
            try:
                import os as _os
                _fc = _os.path.join(ADDON_DATA, 'fastcache.json')
                if _os.path.exists(_fc):
                    _os.remove(_fc)
                if fastcache is not None:
                    fastcache._items = None   # sera rechargé au prochain accès
            except Exception:
                pass
            # v3.7.8 — la base de chaînes et son index sont aussi des données
            _DB_CACHE['data'] = None
            _DB_CACHE['mtime'] = None
            _DB_INDEX['built'] = False
            control.infoDialog(u"[COLOR gold]Fichiers effacés[/COLOR]", NAME,
                               ICON, 5000)
        elif params['mode'] == 'version':
            xbmc.executebuiltin('UpdateAddonRepos')
        elif params['mode'] == 'fmov_menu':
            fmov_menu()
        elif params['mode'] == 'fmov_search':
            fmov_search()
        elif params['mode'] == 'site_events':
            site_events_menu(params['url'])
        elif params['mode'] == 'site_streams':
            site_streams_menu(params['url'])
        elif params['mode'] == 'site_play':
            site_play(params['url'])
        elif params['mode'] == 'spo_events':
            # v3.7.12 — BÉTA SportOnline (réglage beta_sportonline)
            spo_events_menu()
        elif params['mode'] == 'spo_streams':
            spo_streams_menu(params['url'])
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
            # v3.7.8 — message d'erreur plus parlant qu'un simple dump technique
            control.infoDialog(
                u'[COLOR red]Impossible de lire ce flux.[/COLOR] '
                u'[COLOR dimgray]Réessayez ou choisissez un autre lien ({0})[/COLOR]'.format(snippet),
                NAME, ICON, 6000)
        except Exception:
            pass