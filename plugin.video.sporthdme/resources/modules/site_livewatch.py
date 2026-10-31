# -*- coding: utf-8 -*-
"""LiveWatch (livewatch.icu) — 2e porte de secours vers les chaînes
françaises.

Découverte (module `livewatch.py` de vStream) : l'API de livewatch.icu
expose le même gisement de chaînes que Vavoo (928 chaînes France :
LIGUE1+, DAZN LIGUE 1 LIVE, CANAL+ FOOT, CANAL+ SPORT, RMC SPORT,
L'EQUIPE...) mais avec son propre proxy HLS et sa propre authentification :

    POST api/play/ticket              -> {ticket}        (Referer + UA)
    POST api/play/grant  {ticket}     -> {grant}         (~0,5 s plus tard)
    GET  api/stream/<id>              -> {proxy_url}     (X-Play-Grant)
    GET  <MAIN><proxy_url>            -> playlist m3u8   (Referer + UA)

Rôle dans l'add-on : quand un match n'a AUCUNE source française, 3 liens
« Lien 1/2/3 » utilisant les chaînes françaises correspondantes (mêmes
chaînes que le moteur Vavoo) sont proposés via cette porte ; les matchs
déjà servis en français ne sont pas modifiés. Le champ « quality » du
catalogue (4K/FHD/HD/'') est remonté pour afficher la résolution de
chaque lien et en proposer si possible trois différentes (backup_links).
Ouverture rapide : dès l'affichage du menu, les 3 liens sont pré-résolus
en arrière-plan avec UN ticket/grant partagé (prewarm) — au clic le lien
part du cache (~0,1 s) ; sinon la chaîne complète ne coûte que ~4 s
(le serveur exige un ticket âgé d'environ 2 s au moment du grant :
pause calée à 1,2 s). Le catalogue est préchauffé en arrière-plan et
mis en cache (mémoire + disque).

Contrat site_play : resolve('lw://<id>') renvoie (m3u8, page_referer)
pour que les en-têtes pointent vers livewatch.icu.
"""
import json
import re
import threading
import time

import requests
import six

requests.packages.urllib3.disable_warnings()

KEY = 'livewatch'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
MAIN = 'https://livewatch.icu/'
API = MAIN + 'api/'

CATALOG_TTL = 6 * 3600.0          # le catalogue bouge peu : 6 h en mémoire
GRANT_SLEEP = 1.2                 # le serveur refuse un grant demandé pour un
                                  # ticket de moins de ~2 s (mesuré) : 1,2 s de
                                  # pause + réseau = ~2,5 s d'âge, accepté
PREWARM_TTL = 100.0               # proxy_url encore vivante à +2 min (mesuré)
                                  # -> cache court avec marge

_catalog = {'ts': 0.0, 'data': None}
_lock = threading.Lock()
_warm_thread = [None]
_prewarm = {}                     # id -> {'ts': float, 'play': url proxy}
_prewarm_th = {}                  # id -> thread de préchauffage en cours
_prewarm_lock = threading.Lock()
_dead = {}                        # id -> ts : lien hors service (détection
                                  # au préchauffage), ignoré ~5 min
DEAD_TTL = 300.0

# variante préférée d'une même chaîne (la 1re de la liste servira d'URL
# principale, les suivantes passent dans 'alt' comme pour Vavoo)
_QUAL_ORDER = {'FHD': 0, 'HD': 1, '': 2, None: 2, 'SD': 3, '4K': 0}


def _headers(referer=True):
    h = {'User-Agent': UA, 'Accept': 'application/json'}
    if referer:
        h['Referer'] = MAIN
    return h


def _norm(s):
    """Même normalisation que le moteur Vavoo (import partagé)."""
    from resources.modules import site_vavoo
    return site_vavoo._norm(s)


# ---- cache disque (même modèle que site_vavoo) ------------------------------

def _cache_path():
    import os
    try:
        import xbmcaddon
        import xbmcvfs
        profile = xbmcaddon.Addon().getAddonInfo('profile')
        try:
            return os.path.join(xbmcvfs.translatePath(profile),
                                'lw_catalog.json')
        except AttributeError:  # Kodi 18
            import xbmc
            return os.path.join(xbmc.translatePath(profile),
                                'lw_catalog.json')
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), 'sporthd_lw_catalog.json')


def _disk_load(max_age):
    try:
        import os
        path = _cache_path()
        if time.time() - os.path.getmtime(path) >= max_age:
            return None
        with open(path) as f:
            blob = json.load(f)
        return blob.get('data') or None
    except Exception:
        return None


def _disk_save(data):
    try:
        with open(_cache_path(), 'w') as f:
            json.dump({'t': time.time(), 'data': data}, f)
    except Exception:
        pass


def clear_cache():
    """Vide le cache catalogue LiveWatch (mémoire + disque)."""
    global _catalog
    _catalog = {'ts': 0.0, 'data': None}
    try:
        import os
        os.remove(_cache_path())
        return True
    except Exception:
        return False


# ---- catalogue --------------------------------------------------------------

def _fetch_channels():
    r = requests.get(API + 'channels?country=France', headers=_headers(),
                     timeout=(8, 30), verify=False)
    r.raise_for_status()
    chans = (r.json() or {}).get('channels') or []
    out = []
    for c in chans:
        try:
            out.append({'name': (c.get('name') or '').strip(),
                        'id': c.get('id') or '',
                        'source': c.get('source') or '',
                        'quality': c.get('quality') or '',
                        'logo': c.get('logo') or ''})
        except Exception:
            continue
    return [c for c in out if c['name'] and c['id']]


def channels(max_age=CATALOG_TTL):
    """Catalogue France (mémoire -> disque -> réseau), [] si indisponible."""
    now = time.time()
    if _catalog['data'] and now - _catalog['ts'] < max_age:
        return _catalog['data']
    disk = _disk_load(max_age)
    if disk:
        _catalog['ts'] = now
        _catalog['data'] = disk
        return disk
    with _lock:
        now = time.time()
        if _catalog['data'] and now - _catalog['ts'] < max_age:
            return _catalog['data']
        try:
            data = _fetch_channels()
        except Exception:
            return _catalog['data'] or []
        if data:
            _catalog['ts'] = now
            _catalog['data'] = data
            _disk_save(data)
        return data or _catalog['data'] or []


def _index():
    """{nom normalisé: [entrées, ordre qualité]} du catalogue LiveWatch."""
    idx = {}
    for c in channels():
        idx.setdefault(_norm(c['name']), []).append(c)
    for lst in idx.values():
        lst.sort(key=lambda c: _QUAL_ORDER.get(c.get('quality'), 2))
    return idx


# ---- rapprochement des noms (moteur Vavoo -> LiveWatch) ---------------------

_QUAL_TOKENS = ('FHD', 'HD', 'UHD', 'SD', '4K')


def lookup(title, idx=None):
    """Entrée LiveWatch d'une chaîne nommée à la Vavoo, ou None.

    Ordre : égalité exacte -> variante qualité retirée -> pont
    « LIGUE 1 »/« LIGUE1 » -> préfixe unique.
    """
    if idx is None:
        idx = _index()
    n = _norm(title)
    if not n:
        return None
    for cand in (n,):
        if cand in idx:
            return idx[cand][0]
    toks = n.split()
    while len(toks) > 1 and toks[-1] in _QUAL_TOKENS:
        toks = toks[:-1]
        n2 = u' '.join(toks)
        if n2 in idx:
            return idx[n2][0]
    # pont Ligue 1+ : « LIGUE 1 2 » (grille) == « LIGUE1 2 » (plateforme)
    for cut in range(len(toks) + 1):
        head = u' '.join(toks[:cut])
        tail = u' '.join(toks[cut:])
        if head.startswith('LIGUE 1'):
            n2 = (u'LIGUE1' + head[len('LIGUE 1'):]
                  + (u' ' + tail if tail else u''))
            if n2 in idx:
                return idx[n2][0]
    if not toks:
        return None
    cands = [k for k in idx
             if k.split()[:len(toks)] == toks and len(k.split()) > len(toks)]
    if len(cands) == 1:
        return idx[cands[0]][0]
    return None


def backup_links(vavoo_chans, limit=3):
    """Jusqu'à `limit` liens LiveWatch pour les chaînes Vavoo du match,
    à résolutions différentes quand le catalogue le permet.

    vavoo_chans : liste {'title', ...} renvoyée par le moteur Vavoo
    (fallback_channels). Retour : liste plate
    [{'title', 'id', 'quality', 'vav_title', 'ids'}] dans l'ordre
    d'affichage. Sélection en deux passes :
      1re passe : un lien par chaîne du moteur (ordre de préférence),
        en choisissant pour chacune une qualité PAS ENCORE PROPOSÉE
        (FHD -> HD -> 4K -> '') — d'où des résolutions différentes dès
        que le catalogue en porte ;
      2e passe : s'il reste de la place, variantes supplémentaires des
        mêmes chaînes (autres résolutions, meilleures d'abord) pour
        atteindre `limit` liens même si tout est dans la même qualité.
    'ids' : toutes les variantes de la même chaîne — site_play les
    essaye dans l'ordre si le lien cliqué échoue.
    """
    idx = _index()
    chans, used_names = [], set()
    for ch in vavoo_chans or []:
        hit = lookup(ch.get('title') or '', idx)
        if not hit or hit['name'] in used_names:
            continue
        used_names.add(hit['name'])
        chans.append((hit['name'], idx[_norm(hit['name'])],
                      ch.get('title') or hit['name']))

    def _entry(name, variants, vav, v):
        return {'title': name, 'id': v['id'],
                'quality': v.get('quality') or '',
                'vav_title': vav,
                'ids': [c['id'] for c in variants]}

    sel, used_q = [], set()
    # 1re passe : une chaîne par lien, qualité différente quand possible
    for name, variants, vav in chans:
        if len(sel) >= limit:
            break
        pick = None
        for v in variants:
            if (v.get('quality') or '') not in used_q:
                pick = v
                break
        if pick is None:
            pick = variants[0]
        sel.append(_entry(name, variants, vav, pick))
        used_q.add(sel[-1]['quality'])
    # 2e passe : variantes supplémentaires des mêmes chaînes (autres
    # résolutions, meilleures d'abord) pour atteindre `limit` liens
    if len(sel) < limit:
        chosen = set(e['id'] for e in sel)
        for name, variants, vav in chans:
            for v in variants:
                if len(sel) >= limit:
                    break
                if v['id'] in chosen:
                    continue
                chosen.add(v['id'])
                sel.append(_entry(name, variants, vav, v))
            if len(sel) >= limit:
                break
    return sel


# ---- résolution (contrat site_play) -----------------------------------------

def _ticket_grant(errs):
    """ticket + grant partagés. Le serveur exige un ticket âgé d'environ
    2 s au moment du grant (mesuré : refus systématique en dessous) —
    pause calée juste au-dessus."""
    r = requests.post(API + 'play/ticket', headers=_headers(),
                      timeout=(8, 20), verify=False)
    tk = (r.json() or {}).get('ticket')
    if not tk:
        errs.append('ticket absent')
        return None
    time.sleep(GRANT_SLEEP)
    r = requests.post(API + 'play/grant', json={'ticket': tk},
                      headers={'User-Agent': UA},
                      timeout=(8, 20), verify=False)
    grant = (r.json() or {}).get('grant')
    if not grant:
        errs.append('grant refusé')
    return grant


def _stream_of(mid, grant, errs):
    """URL proxy d'un canal pour un grant donné, playlist vérifiée."""
    r = requests.get(API + 'stream/' + mid,
                     headers=dict(_headers(), **{'X-Play-Grant': grant}),
                     timeout=(8, 20), verify=False)
    proxy = (r.json() or {}).get('proxy_url')
    if not proxy:
        errs.append('pas de proxy_url')
        return None
    play = MAIN[:-1] + proxy
    # vérification express : le proxy doit servir une playlist
    r2 = requests.get(play, headers=_headers(), timeout=(8, 20),
                      verify=False)
    if r2.status_code == 200 and '#EXTM3U' in r2.text[:20]:
        return play
    errs.append('HTTP {0}'.format(r2.status_code))
    return None


def _cache_store(mid, play):
    with _prewarm_lock:
        _prewarm[mid] = {'ts': time.time(), 'play': play}


def prewarmed(mid):
    """URL proxy déjà résolue (et encore fraîche) pour ce canal, None sinon."""
    with _prewarm_lock:
        e = _prewarm.get(mid)
    if e and time.time() - e['ts'] < PREWARM_TTL:
        return e['play']
    return None


def resolve(server_url):
    """'lw://<id>' -> (m3u8_proxy, page_referer). Lève sur échec.

    Lien pré-résolu en arrière-plan (prewarm) -> servi instantanément.
    Préchauffage en cours pour le même canal -> attendu quelques secondes
    plutôt que de doubler la requête. Sinon chaîne complète ticket ->
    grant -> stream ; un nouvel essai à ticket frais est fait si elle
    échoue (refus transient).
    """
    mid = re.sub(r'^lw://', '', server_url or '', flags=re.I).strip()
    if not mid:
        raise Exception('livewatch : id de chaîne manquant')
    with _prewarm_lock:
        d = _dead.get(mid)
    if d and time.time() - d < DEAD_TTL:
        # hors service confirmé au préchauffage : échec immédiat, le
        # lecteur enchaîne aussitôt sur la variante suivante
        raise Exception('livewatch : lien hors service (préchauffage)')
    play = prewarmed(mid)
    if play:
        return play, MAIN + 'play'
    with _prewarm_lock:
        th = _prewarm_th.get(mid)
    if th is not None and th.is_alive():
        th.join(timeout=12.0)
        play = prewarmed(mid)
        if play:
            return play, MAIN + 'play'
    errs = []
    for attempt in range(2):
        try:
            grant = _ticket_grant(errs)
            if not grant:
                continue
            play = _stream_of(mid, grant, errs)
            if play:
                _cache_store(mid, play)
                return play, MAIN + 'play'
        except Exception as exc:
            errs.append(repr(exc))
    raise Exception('livewatch resolve: ' + ' ; '.join(errs))


# ---- préchauffage des liens (ouverture rapide) -------------------------------

def _prewarm_run(mids, idmap):
    """UN ticket/grant partagé, puis les canaux résolus en parallèle
    (mesuré : le serveur accepte un même grant pour plusieurs chaînes).
    Variante choisie hors service -> les autres variantes de la même
    chaîne sont essayées ; tout est mort -> marqué (DEAD_TTL)."""
    grant = None
    for _ in range(2):                # refus transient possible : nouvel essai
        try:
            grant = _ticket_grant([])
        except Exception:
            grant = None
        if grant:
            break
    if not grant:
        with _prewarm_lock:
            for mid in mids:
                _prewarm_th.pop(mid, None)
        return

    def _one(mid):
        try:
            cands = [mid] + [i for i in (idmap.get(mid) or []) if i != mid]
            for c in cands[:4]:
                errs = []
                try:
                    play = _stream_of(c, grant, errs)
                except Exception:
                    play = None
                if play:
                    _cache_store(mid, play)
                    return
                with _prewarm_lock:
                    _dead[c] = time.time()
        finally:
            with _prewarm_lock:
                _prewarm_th.pop(mid, None)

    ths = []
    for mid in mids:
        th = threading.Thread(target=_one, args=(mid,))
        th.daemon = True
        ths.append(th)
    for th in ths:
        th.start()
    for th in ths:
        th.join()


def prewarm(links):
    """Pré-résout en arrière-plan les liens du menu qui s'affiche.

    links : sortie de backup_links ([{'id', 'ids', ...}]). Appelé dès
    l'affichage du menu d'un match : quand l'utilisateur clique, le lien
    est déjà prêt (ouverture quasi instantanée). Un seul ticket/grant
    pour tout le lot, canaux déjà frais/morts/en cours ignorés, jamais
    bloquant ; si la variante choisie est hors service, les variantes
    suivantes de la même chaîne sont essayées à sa place.
    """
    todo, idmap = [], {}
    now = time.time()
    session = None
    with _prewarm_lock:
        for ch in links or []:
            mid = (ch.get('id') or '').strip()
            if not mid:
                continue
            idmap[mid] = [i for i in (ch.get('ids') or []) if i]
            e = _prewarm.get(mid)
            if e and now - e['ts'] < PREWARM_TTL:
                continue
            d = _dead.get(mid)
            if d and now - d < DEAD_TTL:
                continue
            th = _prewarm_th.get(mid)
            if th is not None and th.is_alive():
                continue
            if mid not in todo:
                todo.append(mid)
        if todo:
            session = threading.Thread(target=_prewarm_run,
                                       args=(todo, idmap))
            session.daemon = True
            for mid in todo:
                _prewarm_th[mid] = session
    if session is not None:
        session.start()


# ---- préchauffage ------------------------------------------------------------

def warmup():
    """Précharge le catalogue LiveWatch en arrière-plan (une fois à la
    fois, jamais bloquant)."""
    try:
        t = _warm_thread[0]
        if t is not None and t.is_alive():
            return
    except Exception:
        return

    def _run():
        try:
            channels()
        except Exception:
            pass

    try:
        th = threading.Thread(target=_run)
        th.daemon = True
        _warm_thread[0] = th
        th.start()
    except Exception:
        pass


def is_warm():
    try:
        return bool(channels())
    except Exception:
        return False


# ---- auto-test hors Kodi -----------------------------------------------------

if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    t0 = time.time()
    chs = channels()
    print('%d chaînes France (%.1fs)' % (len(chs), time.time() - t0))
    idx = _index()
    for probe in (u'CANAL+ FOOT', u'LIGUE1+', u'LIGUE 1 FHD',
                  u'DAZN LIGUE 1 LIVE 1 FHD', u'CANAL + LIGUE 1',
                  u'L EQUIPE LIVE 1'):
        hit = lookup(probe, idx)
        print('   %-28s -> %s' % (probe, hit['name'] if hit else '(aucun)'))
    print('   liens tels qu\'affichés dans le menu :')
    for chans in ([{'title': u'CANAL+ FOOT'}],
                  [{'title': u'LIGUE1+'}, {'title': u'CANAL+ FOOT'},
                   {'title': u'DAZN LIGUE 1 LIVE 1'}]):
        print('      pour %s' % u', '.join(c['title'] for c in chans))
        for i, b in enumerate(backup_links(chans, limit=3), 1):
            print('         Lien %d -> %s [%s]' % (
                i, b['title'], b['quality'] or 'qualité non annoncée'))
