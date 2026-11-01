# -*- coding: utf-8 -*-
"""
Grille TV Sport (France) — module EPG autonome, ajouté en v3.0.0.

Source : XMLTV FR (https://xmltvfr.fr, projet racacax/XML-TV-Fr), guide
France complet : https://xmltvfr.fr/xmltv/xmltv_fr.xml.gz
(~9,75 Mo compressé, ~77 Mo XML, 401 chaînes, 9 jours d'avance,
 mise à jour quotidienne vers 02:45).

Ce module ne diffuse AUCUN flux : il fournit uniquement la grille des
programmes (titres, affiches, horaires). La couche données est en python
pur (testable hors Kodi) ; seuls les dialogues/chemins passent par xbmc.
"""
import io
import json
import os
import time
import calendar
import gzip
import xml.etree.ElementTree as ET

try:
    import requests  # déjà dépendance de l'add-on
except ImportError:  # pragma: no cover - tests hors Kodi
    requests = None

EPG_URL = 'https://xmltvfr.fr/xmltv/xmltv_fr.xml.gz'
TTL = 24 * 3600  # la source est régénérée une fois par jour (~02:45)

# Chaînes visées : id XMLTV FR -> libellé affiché. Les ids absents du
# fichier du jour sont ignorés (rattrapage insensible à la casse en secours).
WANTED = [
    ('beINSPORTS1.fr', 'beIN SPORTS 1'),
    ('beINSPORTS2.fr', 'beIN SPORTS 2'),
    ('beINSPORTS3.fr', 'beIN SPORTS 3'),
    ('beINSPORTSMAX4.fr', 'beIN SPORTS MAX 4'),
    ('beINSPORTSMAX5.fr', 'beIN SPORTS MAX 5'),
    ('beINSPORTSMAX6.fr', 'beIN SPORTS MAX 6'),
    ('beINSPORTSMAX7.fr', 'beIN SPORTS MAX 7'),
    ('beINSPORTSMAX8.fr', 'beIN SPORTS MAX 8'),
    ('beINSPORTSMAX9.fr', 'beIN SPORTS MAX 9'),
    ('beINSPORTSMAX10.fr', 'beIN SPORTS MAX 10'),
    ('CanalPlusFoot.fr', 'Canal+ Foot'),
    ('CanalPlusSport.fr', 'Canal+ Sport'),
    ('CanalPlusSport360.fr', 'Canal+ Sport 360'),
    ('CanalPlus.fr', 'CANAL+'),
    ('CanalPlusLive1.fr', 'Canal+ Live 1'),
    ('CanalPlusLive2.fr', 'Canal+ Live 2'),
    ('CanalPlusLive3.fr', 'Canal+ Live 3'),
    ('CanalPlusLive4.fr', 'Canal+ Live 4'),
    ('CanalPlusLive5.fr', 'Canal+ Live 5'),
    ('CanalPlusLive6.fr', 'Canal+ Live 6'),
    ('CanalPlusLive7.fr', 'Canal+ Live 7'),
    ('CanalPlusLive8.fr', 'Canal+ Live 8'),
    ('CanalPlusLive9.fr', 'Canal+ Live 9'),
    ('CanalPlusLive10.fr', 'Canal+ Live 10'),
    ('CanalPlusLive11.fr', 'Canal+ Live 11'),
    ('CanalPlusLive12.fr', 'Canal+ Live 12'),
    ('CanalPlusLive13.fr', 'Canal+ Live 13'),
    ('CanalPlusLive14.fr', 'Canal+ Live 14'),
    ('CanalPlusLive15.fr', 'Canal+ Live 15'),
    ('CanalPlusLive16.fr', 'Canal+ Live 16'),
    ('CanalPlusLive17.fr', 'Canal+ Live 17'),
    ('CanalPlusLive18.fr', 'Canal+ Live 18'),
    ('CanalPlusLive19.fr', 'Canal+ Live 19'),
    ('Ligue1Plus.fr', 'Ligue 1+'),
    ('Ligue1Plus2.fr', 'Ligue 1+ 2'),
    ('Ligue1Plus3.fr', 'Ligue 1+ 3'),
    ('Ligue1Plus4.fr', 'Ligue 1+ 4'),
    ('Ligue1Plus5.fr', 'Ligue 1+ 5'),
    ('Ligue1Plus6.fr', 'Ligue 1+ 6'),
    ('Ligue1Plus7.fr', 'Ligue 1+ 7'),
    ('Ligue1Plus8.fr', 'Ligue 1+ 8'),
    ('Ligue1Plus9.fr', 'Ligue 1+ 9'),
    ('Ligue1Plus10.fr', 'Ligue 1+ 10'),
    ('RMCSport1.fr', 'RMC Sport 1'),
    ('RMCSport2.fr', 'RMC Sport 2'),
    ('RMCSport3.fr', 'RMC Sport 3'),
    ('RMCSport4.fr', 'RMC Sport 4'),
    ('RMCSportAccess1.fr', 'RMC Sport Access 1'),
    ('RMCSportUHD.fr', 'RMC Sport UHD'),
    ('DAZN.fr', 'DAZN'),
    ('Eurosport1.fr', 'Eurosport 1'),
    ('Eurosport2.fr', 'Eurosport 2'),
    ('TF1.fr', 'TF1'),
    ('M6.fr', 'M6'),
    ('W9.fr', 'W9'),
    ('TMC.fr', 'TMC'),
    ('TFX.fr', 'TFX'),
    ('6ter.fr', '6ter'),
    ('cstar.fr', 'CSTAR'),
    ('TF1SeriesFilms.fr', 'TF1 Séries-Films'),
]

_LABELS = dict(WANTED)
_DESC_MAX = 300
_profile = None
_cache = None


def set_profile(path):
    """Pointe le cache vers un dossier (tests / usage hors Kodi)."""
    global _profile, _cache
    _profile = path
    _cache = None


def _profile_dir():
    global _profile
    if _profile:
        return _profile
    import xbmcaddon  # uniquement sous Kodi
    return xbmcaddon.Addon().getAddonInfo('profile')


def _data_path():
    return os.path.join(_profile_dir(), 'epg_cache.json')


def _tvtime_to_epoch(s):
    """'20261101204500 +0100' -> epoch UTC (int) ; 0 si illisible."""
    try:
        d, off = s.split(' ')
        y, mo, da = int(d[0:4]), int(d[4:6]), int(d[6:8])
        h, mi, se = int(d[8:10]), int(d[10:12]), int(d[12:14])
        sign = 1 if off[:1] == '+' else -1
        oh, om = int(off[1:3]), int(off[3:5])
        return calendar.timegm((y, mo, da, h, mi, se, 0, 0, 0)) - sign * (oh * 3600 + om * 60)
    except Exception:
        return 0


def fmt_hour(epoch):
    """Epoch -> 'HH:MM' en heure locale."""
    return time.strftime('%H:%M', time.localtime(epoch))


def fmt_day(epoch):
    """Epoch -> 'dd/mm' en heure locale."""
    return time.strftime('%d/%m', time.localtime(epoch))


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(_data_path(), 'rb') as f:
            _cache = json.loads(f.read().decode('utf-8'))
    except Exception:
        _cache = {'fetched': 0, 'channels': {}, 'progs': {}}
    return _cache


def _save(channels, progs):
    global _cache
    _cache = {'fetched': int(time.time()), 'channels': channels, 'progs': progs}
    try:
        if not os.path.isdir(_profile_dir()):
            os.makedirs(_profile_dir())
        tmp = _data_path() + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(json.dumps(_cache, ensure_ascii=False).encode('utf-8'))
        os.replace(tmp, _data_path())
    except Exception:
        pass


def is_stale():
    return (time.time() - _load().get('fetched', 0)) > TTL


def refresh(force=False, timeout=120):
    """Télécharge et extrait la grille si périmée (ou si force=True).
    Retourne True si un cache exploitable est disponible à la fin."""
    if not force and not is_stale():
        return True
    if requests is None:
        return False
    try:
        r = requests.get(EPG_URL, timeout=timeout,
                         headers={'User-Agent': 'MediaHubMX/2'})
        r.raise_for_status()
        raw = r.content
    except Exception:
        # réseau indisponible : un cache périmé vaut mieux que rien
        return bool(_load().get('progs'))
    return _extract(raw)


def _extract(raw_gz):
    """Parse le .gz XMLTV : une seule passe, on ne garde que WANTED."""
    wanted_ids = set(_LABELS)
    wanted_low = {k.lower(): k for k in _LABELS}
    names = {}   # id -> display-name (optionnel)
    progs = {}   # id -> [[start, stop, title, subtitle, desc, icon], ...]
    now = time.time()
    cutoff = now - 2 * 3600  # garde aussi ce qui a commencé il y a < 2 h

    def keep(cid):
        return cid in wanted_ids or cid.lower() in wanted_low

    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw_gz)) as f:
            for _ev, el in ET.iterparse(f, events=('end',)):
                tag = el.tag.split('}')[-1]
                if tag == 'channel':
                    cid = el.get('id') or ''
                    if keep(cid):
                        names[cid] = el.findtext('display-name') or ''
                    el.clear()
                elif tag == 'programme':
                    cid = el.get('channel') or ''
                    if keep(cid):
                        stop = _tvtime_to_epoch(el.get('stop') or '')
                        if stop > cutoff:
                            icon_el = el.find('icon')
                            progs.setdefault(cid, []).append([
                                _tvtime_to_epoch(el.get('start') or ''),
                                stop,
                                (el.findtext('title') or '').strip(),
                                (el.findtext('sub-title') or '').strip(),
                                (el.findtext('desc') or '').strip()[:_DESC_MAX],
                                (icon_el.get('src') if icon_el is not None else '') or '',
                            ])
                    el.clear()
    except Exception:
        return False

    # ids canoniques (rattrapage casse) + libellés
    channels = {}
    for cid in list(progs.keys()):
        can = cid if cid in _LABELS else wanted_low.get(cid.lower(), cid)
        label = _LABELS.get(can) or names.get(cid) or names.get(can) or can
        channels[can] = label
        if can != cid:
            progs[can] = progs.pop(cid)
    for can in list(progs):
        progs[can].sort(key=lambda p: p[0])
        if not channels.get(can):
            channels[can] = _LABELS.get(can) or can
    if not progs:
        return False
    _save(channels, progs)
    return True


def channels():
    """[(id, libellé)] trié par libellé, pour les chaînes présentes en cache."""
    c = _load()
    out = [(cid, c['channels'].get(cid) or _LABELS.get(cid) or cid)
           for cid in c.get('progs', {}) if c.get('progs', {})[cid]]
    out.sort(key=lambda t: t[1].lower())
    return out


def programs(chid):
    """Programmes d'une chaîne : [[start, stop, title, subtitle, desc, icon], ...]."""
    return _load().get('progs', {}).get(chid, [])


def now_next(chid):
    """(programme_en_cours, programme_suivant) — None si absent."""
    now = time.time()
    cur = nxt = None
    for p in programs(chid):
        if p[0] <= now < p[1]:
            cur = p
            idx = programs(chid).index(p)
            lst = programs(chid)
            if idx + 1 < len(lst):
                nxt = lst[idx + 1]
            break
    return cur, nxt


def cache_age_hours():
    return round((time.time() - _load().get('fetched', 0)) / 3600.0, 1)
