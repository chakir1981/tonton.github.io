# -*- coding: utf-8 -*-
"""
fastcache — cache disque persistant + exécution parallèle (copie Foot FR
de la 3.7.4 SportHD, environnement renommé FOOTFR_FASTCACHE pour que les
deux add-ons ne partagent jamais le même fichier de cache).

API :
    fget(key, ttl)          -> valeur si entrée plus récente que ttl, sinon None
    fstale(key, max_age)    -> dernière valeur connue quel que soit le ttl
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
    _PATH = os.environ.get('FOOTFR_FASTCACHE') or '/tmp/footfr_fastcache.json'

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


def clear():
    """Vide entièrement le cache (menu « Vider les données »)."""
    global _items, _dirty
    with _lock:
        _items = {}
        _dirty = True
        try:
            os.remove(_PATH)
        except Exception:
            pass
        _dirty = False


def parallel_map(fn, items, workers=4):
    """fn(item) pour chaque item, en parallèle (ordres quelconques).
    Les exceptions de fn sont avalées. workers<=1 ou 1 seul item :
    exécution séquentielle directe."""
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


def _safe(fn):
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None
    return run
