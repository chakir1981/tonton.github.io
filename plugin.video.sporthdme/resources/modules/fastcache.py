# -*- coding: utf-8 -*-
"""
fastcache — cache disque persistant + exécution parallèle (v3.7.4).

Pourquoi : jusqu'à la 3.7.3, tous les caches de l'add-on (grille DaddyLive,
pages clubs footao, scan 365scores, pages joueur) vivent dans la mémoire du
processus. Kodi relance le processus à chaque navigation : les caches meurent,
et chaque clic re-télécharge tout (jusqu'à 60-100 s pour le premier affichage
du menu). fastcache stocke les entrées dans un JSON du profil de l'add-on
(gzip pour les grosses chaînes HTML), surviv aux navigations, et fournit des
utilitaires de parallélisation compatibles Python 2/3.

API :
    fget(key, ttl)          -> valeur si entrée plus récente que ttl, sinon None
    fstale(key, max_age)    -> dernière valeur connue quel que soit le ttl
                               (grace hors-ligne ; None si > max_age ou absente)
    fset(key, value)        -> mémorise (écriture disque différée, ~0.4 s)
    flush()                 -> force l'écriture disque
    parallel_map(fn, items) -> exécute fn(item) pour chaque item en parallèle
    run_jobs(fns)           -> exécute chaque callable en parallèle

Toute panne (disque plein, JSON corrompu) est silencieuse : l'add-on
fonctionne comme avant, juste sans cache persistant.
"""
import os
import io
import json
import gzip
import base64
import time
import threading

try:
    import xbmcaddon
    _PATH = os.path.join(xbmcaddon.Addon().getAddonInfo('profile'),
                         'fastcache.json')
except Exception:
    _PATH = os.environ.get('SPORTHD_FASTCACHE') or '/tmp/sporthd_fastcache.json'

MAX_AGE = 7 * 86400          # une entrée jamais lue depuis 7 j est supprimée
MAX_ENTRIES = 500            # garde-fou taille fichier
GRACE_DEFAULT = 6 * 3600     # fstale par défaut
_FLUSH_EVERY = 0.4           # écriture disque au plus 1x / 0.4 s
_GZ_MIN = 4096               # chaînes > 4 Ko stockées gzippées

_lock = threading.RLock()
_items = None                # {key: {'t': epoch, 'v': valeur}}
_dirty = False
_last_flush = 0.0


def _load():
    global _items
    if _items is not None:
        return
    _items = {}
    try:
        with io.open(_PATH, 'r', encoding='utf-8') as fh:
            data = json.loads(fh.read())
        now = time.time()
        for k, ent in (data.get('items') or {}).items():
            try:
                if now - float(ent.get('t', 0)) > MAX_AGE:
                    continue
                v = ent.get('v')
                if isinstance(v, dict) and '__gz__' in v:
                    v = gzip.decompress(base64.b64decode(v['__gz__'])) \
                        .decode('utf-8', 'replace')
                _items[k] = {'t': float(ent.get('t', 0)), 'v': v}
            except Exception:
                continue
    except Exception:
        _items = {}


def _pack(v):
    if isinstance(v, str) and len(v) > _GZ_MIN:
        try:
            return {'__gz__': base64.b64encode(
                gzip.compress(v.encode('utf-8'))).decode('ascii')}
        except Exception:
            return v
    return v


def flush():
    """Écriture disque atomique (tmp + rename). Silencieux en cas de panne."""
    global _dirty, _last_flush
    with _lock:
        if not _dirty:
            return
        try:
            now = time.time()
            out = {}
            # limite de taille : les MAX_ENTRIES entrées les plus récentes
            keys = sorted(_items.keys(),
                          key=lambda k: -_items[k]['t'])[:MAX_ENTRIES]
            for k in keys:
                ent = _items[k]
                if now - ent['t'] > MAX_AGE:
                    continue
                out[k] = {'t': ent['t'], 'v': _pack(ent['v'])}
            d = os.path.dirname(_PATH)
            if d and not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except Exception:
                    return
            tmp = _PATH + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(json.dumps({'v': 1, 'items': out},
                                    ensure_ascii=True))
            try:
                os.replace(tmp, _PATH)      # py3 : atomique
            except AttributeError:
                os.rename(tmp, _PATH)       # py2 : posix remplace
            _dirty = False
            _last_flush = now
        except Exception:
            pass


def _autosave(force=False):
    global _dirty, _last_flush
    if force or (time.time() - _last_flush) >= _FLUSH_EVERY:
        flush()
    else:
        _dirty = True


def fget(key, ttl):
    """Valeur si l'entrée a moins de ttl secondes, sinon None."""
    if not key or ttl <= 0:
        return None
    with _lock:
        _load()
        ent = _items.get(key)
        if not ent or (time.time() - ent['t']) >= ttl:
            return None
        return ent['v']


def fstale(key, max_age=GRACE_DEFAULT):
    """Dernière valeur connue, même périmée (grace hors-ligne)."""
    with _lock:
        _load()
        ent = _items.get(key)
        if not ent or (time.time() - ent['t']) > max_age:
            return None
        return ent['v']


def fset(key, value):
    global _dirty
    if not key:
        return
    with _lock:
        _load()
        _items[key] = {'t': time.time(), 'v': value}
        _dirty = True
        _autosave()


def fresh(key, ttl):
    """L'entrée est-elle présente et fraîche (sans charger la valeur) ?"""
    with _lock:
        _load()
        ent = _items.get(key)
        return bool(ent) and (time.time() - ent['t']) < ttl


# ---------------------------------------------------------------------------
# Parallélisation légère (Python 2/3, sans dépendance)
# ---------------------------------------------------------------------------

def parallel_map(fn, items, workers=4):
    """fn(item) pour chaque item, en parallèle (ordres quelconques).

    Les exceptions de fn sont avalées (l'appelant décide du repli).
    workers<=1 ou 1 seul item : exécution séquentielle directe.
    """
    items = list(items or [])
    if len(items) <= 1 or workers <= 1:
        for it in items:
            try:
                fn(it)
            except Exception:
                pass
        return
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
            list(ex.map(_safe(fn), items))
    except ImportError:
        # python 2 / Kodi 18 : threads bruts
        threads = []
        for it in items:
            t = threading.Thread(target=_safe(fn), args=(it,))
            t.daemon = True
            t.start()
            threads.append(t)
            if len(threads) >= workers:
                threads[0].join()      # fenêtre glissante simple
                threads.pop(0)
        for t in threads:
            t.join()


def run_jobs(fns, workers=2):
    """Exécute chaque callable en parallèle. Exceptions avalées."""
    parallel_map(lambda fn: fn(), list(fns or []), workers=workers)


def map_calls(fns, workers=3):
    """Chaque callable() en parallèle -> liste des résultats (None si lever)."""
    fns = list(fns or [])
    if not fns:
        return []
    if len(fns) == 1 or workers <= 1:
        out = []
        for fn in fns:
            try:
                out.append(fn())
            except Exception:
                out.append(None)
        return out
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(workers, len(fns))) as ex:
            return list(ex.map(lambda f: _safe(f)(), fns))
    except ImportError:
        out = [None] * len(fns)

        def _one(i, f):
            out[i] = _safe(f)()

        threads = [threading.Thread(target=_one, args=(i, f))
                   for i, f in enumerate(fns)]
        for t in threads:
            t.daemon = True
            t.start()
        for t in threads:
            t.join()
        return out


def _safe(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None
    return run


# ---------------------------------------------------------------------------
# Auto-test (hors Kodi : SPORTHD_FASTCACHE=/tmp/xx.json python3 fastcache.py)
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import tempfile
    _PATH = os.path.join(tempfile.gettempdir(), 'fc_test.json')
    try:
        os.remove(_PATH)
    except OSError:
        pass
    _items = None
    _dirty = False
    fset('a', 42)
    fset('html', '<div>x' * 3000)          # > 4 Ko -> gzip
    flush()
    _items = None                           # simule un redémarrage
    assert fget('a', 60) == 42
    time.sleep(0.02)
    assert fget('a', 0.01) is None          # ttl dépassé
    assert fstale('a', 60) == 42            # grace
    assert fget('html', 60) == '<div>x' * 3000
    assert fstale('inconnu') is None
    # parallèle
    acc = []
    lk = threading.Lock()
    def _w(i):
        time.sleep(0.2)
        with lk:
            acc.append(i)
    t0 = time.time()
    parallel_map(_w, range(8), workers=4)
    dt = time.time() - t0
    assert sorted(acc) == list(range(8))
    assert dt < 0.9, 'parallel_map trop lent: %.2f s' % dt
    # run_jobs
    out = []
    run_jobs([lambda: out.append(1), lambda: out.append(2)])
    assert sorted(out) == [1, 2]
    # map_calls (résultats ordonnés, exceptions -> None)
    def _v(x):
        if x == 2:
            raise ValueError
        return x * 10
    res = map_calls([lambda: _v(1), lambda: _v(2), lambda: _v(3)], workers=3)
    assert res == [10, None, 30], res
    print('fastcache self-test: OK (%.2f s parallèle, fichier %s)'
          % (dt, _PATH))
