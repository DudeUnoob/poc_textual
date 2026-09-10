"""Small transaction adapter: the same domain operations run against PostgreSQL and tests."""
from copy import deepcopy
from threading import RLock


class MemoryStore:
    """Serializable in-memory store for deterministic fault/concurrency tests only."""
    def __init__(self):
        self.documents = {}
        self.lock = RLock()

    def get(self, path):
        with self.lock:
            return deepcopy(self.documents.get(path))

    def list(self, collection):
        with self.lock:
            return [dict(deepcopy(v), id=k.rsplit('/', 1)[1]) for k, v in self.documents.items()
                    if k.rsplit('/', 1)[0] == collection]

    def atomic(self, callback):
        with self.lock:
            staged = deepcopy(self.documents)
            class Transaction:
                def get(self, path):
                    return deepcopy(staged.get(path))
                def set(self, path, value):
                    staged[path] = deepcopy(value)
            result = callback(Transaction())
            self.documents = staged
            return result


class StoreContentionError(RuntimeError):
    """The caller may retry its original operation ID after contention subsides."""


class SupabaseStore:
    """Optimistic serializable transactions over a service-role-only PostgreSQL RPC.

    Each successful write advances a global version under a row lock. Reads may
    happen over several HTTP requests, but no result or domain error is accepted
    unless that version is still unchanged. This conservative scheme prevents
    write skew and phantoms across independent documents at the five-editor scale.
    Network failures are surfaced, never blindly replayed after an uncertain commit;
    application operation receipts make a caller's subsequent retry safe.
    """
    def __init__(self, client, max_attempts=8):
        if max_attempts < 1:
            raise ValueError('max_attempts must be positive')
        self.client = client
        self.max_attempts = max_attempts

    def _rpc(self, name, params=None):
        return self.client.rpc(name, params or {}).execute().data

    def get(self, path):
        return self._rpc('workbench_read', {'document_path': path})

    def list(self, collection):
        return self._rpc('workbench_list', {'collection_path': collection}) or []

    def atomic(self, callback):
        import random
        import time
        store = self
        for attempt in range(self.max_attempts):
            version = self._rpc('workbench_version')
            writes, reads = {}, {}
            class Transaction:
                def get(self, path):
                    if path in writes:
                        return deepcopy(writes[path])
                    if path not in reads:
                        reads[path] = store.get(path)
                    return deepcopy(reads[path])
                def set(self, path, value):
                    writes[path] = deepcopy(value)
            try:
                result = callback(Transaction())
            except Exception:
                # A domain error from an inconsistent view must be retried too.
                if self._rpc('workbench_version') == version:
                    raise
            else:
                committed = self._rpc('workbench_commit', {
                    'expected_version': version,
                    'writes': [{'path': path, 'value': value} for path, value in writes.items()],
                })
                if committed:
                    return result
            if attempt + 1 < self.max_attempts:
                time.sleep(random.uniform(0.005, min(0.2, 0.01 * (2 ** attempt))))
        raise StoreContentionError('Concurrent updates prevented saving; retry the same operation ID')
