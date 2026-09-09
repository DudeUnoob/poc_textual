"""Small transaction adapter: the same domain operations run against Firestore and tests."""
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


class FirebaseStore:
    def __init__(self, db):
        self.db = db

    def get(self, path):
        return self.db.document(path).get().to_dict()

    def list(self, collection):
        return [dict(s.to_dict(), id=s.id) for s in self.db.collection(collection).stream()]

    def atomic(self, callback):
        from google.cloud import firestore
        db = self.db
        @firestore.transactional
        def run(transaction):
            # Stage writes so every Firestore read precedes every write.
            writes = {}
            class Transaction:
                def get(self, path):
                    if path in writes:
                        return deepcopy(writes[path])
                    return db.document(path).get(transaction=transaction).to_dict()
                def set(self, path, value):
                    writes[path] = deepcopy(value)
            result = callback(Transaction())
            for path, value in writes.items():
                transaction.set(db.document(path), value)
            return result
        return run(db.transaction())
