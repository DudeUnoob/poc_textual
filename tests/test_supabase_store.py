"""Fault tests of the optimistic RPC protocol, without a live account."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from workbench.cloud.store import SupabaseStore, StoreContentionError


class RpcClient:
    def __init__(self):
        self.version = 0
        self.documents = {}
        self.before_commit = None

    def rpc(self, name, params):
        def execute():
            if name == 'workbench_version':
                result = self.version
            elif name == 'workbench_read':
                result = deepcopy(self.documents.get(params['document_path']))
            elif name == 'workbench_list':
                result = [dict(v, id=k.rsplit('/', 1)[-1]) for k, v in self.documents.items()
                          if k.rsplit('/', 1)[0] == params['collection_path']]
            elif name == 'workbench_commit':
                if self.before_commit:
                    self.before_commit(self)
                result = params['expected_version'] == self.version
                if result and params['writes']:
                    for item in params['writes']:
                        self.documents[item['path']] = deepcopy(item['value'])
                    self.version += 1
            return SimpleNamespace(data=result)
        return SimpleNamespace(execute=execute)


def test_conflicting_writer_reexecutes_complete_callback_without_lost_update():
    client = RpcClient()
    client.documents['rows/one'] = {'revision': 0}
    def competing_write(c):
        c.documents['rows/one'] = {'revision': 7}
        c.version += 1
        c.before_commit = None
    client.before_commit = competing_write
    seen = []
    def update(tx):
        row = tx.get('rows/one')
        seen.append(row['revision'])
        tx.set('rows/one', {'revision': row['revision']+1})
        tx.set('audit/event', {'from': row['revision']})
        return row['revision']+1
    assert SupabaseStore(client).atomic(update) == 8
    assert seen == [0, 7]
    assert client.documents['audit/event'] == {'from': 7}


def test_stale_domain_failure_is_retried_but_current_failure_preserved():
    client = RpcClient()
    calls = []
    def callback(tx):
        calls.append(True)
        if len(calls) == 1:
            client.version += 1
            raise ValueError('stale view')
        return 'fresh'
    assert SupabaseStore(client).atomic(callback) == 'fresh'
    assert len(calls) == 2
    with pytest.raises(ValueError, match='real failure'):
        SupabaseStore(client).atomic(lambda tx: (_ for _ in ()).throw(ValueError('real failure')))


def test_reads_cached_and_staged_values_isolated():
    client = RpcClient()
    client.documents['rows/one'] = {'value': 1}
    def callback(tx):
        first = tx.get('rows/one')
        first['value'] = 99
        assert tx.get('rows/one')['value'] == 1
        tx.set('rows/one', {'value': 2})
        assert tx.get('rows/one')['value'] == 2
    SupabaseStore(client).atomic(callback)
    assert client.documents['rows/one']['value'] == 2


def test_contention_is_bounded_and_no_partial_writes():
    client = RpcClient()
    client.before_commit = lambda c: setattr(c, 'version', c.version+1)
    with pytest.raises(StoreContentionError):
        SupabaseStore(client, max_attempts=2).atomic(lambda tx: tx.set('rows/one', {'a': 1}))
    assert client.documents == {}


def test_network_failure_after_possible_commit_is_not_blindly_replayed():
    client = RpcClient()
    def unavailable(c):
        raise ConnectionError('uncertain network result')
    client.before_commit = unavailable
    calls = []
    with pytest.raises(ConnectionError):
        SupabaseStore(client).atomic(lambda tx: calls.append(True))
    assert len(calls) == 1


def test_nested_collection_listing_does_not_include_descendants():
    client = RpcClient()
    client.documents = {'rows/one': {'n': 1}, 'rows/one/audit/two': {'n': 2}}
    assert SupabaseStore(client).list('rows') == [{'n': 1, 'id': 'one'}]
