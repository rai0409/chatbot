from rag_core import answer_evidence, retrieval


def test_evidence_uses_generation_time_versions(monkeypatch):
    monkeypatch.setattr(retrieval, 'chunk_row', lambda *args: {'text': 'changed after retrieval'})
    monkeypatch.setattr(answer_evidence, 'active_generation_id', lambda: 'new-generation')
    record = answer_evidence.build_evidence(request_id='r', tenant_id='t', payload={'answer_text': 'answer', 'citations': [{'number': 1, 'chunk_id': 'c'}]}, trace={'cited': [{'number': 1, 'chunk_id': 'c', 'source_row_sha256': answer_evidence.sha256_text('retrieved text'), 'index_generation': 'old-generation'}], 'active_generation_id': 'old-generation'})
    assert record['cited'][0]['source_row_sha256'] == answer_evidence.sha256_text('retrieved text')
    assert record['active_generation_id'] == 'old-generation'


def test_evidence_access_requires_matching_scope(monkeypatch):
    monkeypatch.setattr(answer_evidence, '_RECENT', {'r': {'request_id': 'r', 'tenant_id': 't', 'access_scope': 'acl:alice'}})
    assert answer_evidence.lookup_evidence('r', 't') is None  # caller scope is unknown


def test_scope_matching_and_legacy_records(monkeypatch):
    monkeypatch.setattr(answer_evidence, '_RECENT', {
        'r': {'request_id': 'r', 'tenant_id': 't', 'access_scope': 'acl:alice'},
        'old': {'request_id': 'old', 'tenant_id': 't'},
        'shared': {'request_id': 'shared', 'tenant_id': 't', 'access_scope': ''},
    })
    assert answer_evidence.lookup_evidence('r', 't', access_scope='acl:alice') is not None
    assert answer_evidence.lookup_evidence('r', 't', access_scope='acl:bob') is None
    assert answer_evidence.lookup_evidence('r', 'other', access_scope='acl:alice') is None
    assert answer_evidence.lookup_evidence('old', 't', access_scope='') is None
    assert answer_evidence.lookup_evidence('shared', 't', access_scope='') is not None
    assert answer_evidence.lookup_evidence('shared', 't', access_scope=None) is None


def test_merge_unknown_version_is_not_promoted():
    from rag_grounded import Chunk, merge_by_page
    a = Chunk('a', 'first', 'd', (1,), meta={'index_generation': 'g'})
    b = Chunk('b', 'second', 'd', (1,))
    c = Chunk('c', 'third', 'd', (1,), meta={'index_generation': 'g'})
    assert merge_by_page([a, b, c])[0].meta is None


def test_parent_and_child_generations_must_agree(monkeypatch):
    from types import SimpleNamespace
    from rag_core import qa
    import config
    import time
    parent = {'id': 'p', 'text': 'parent body', 'tenant_id': 'default', 'index_generation': 'g2'}
    monkeypatch.setattr(config, 'ENABLE_PARENT_EXPANSION', True)
    monkeypatch.setattr(retrieval, '_index_for_collection', lambda _: SimpleNamespace(id_to_index={'p': 0}, row=lambda _: parent))
    child = retrieval.RetrievedChunk(text='child', score=0.1, metadata={'id': 'c', 'parent_chunk_id': 'p', 'index_generation': 'g1', '_evidence_source_row_sha256': answer_evidence.sha256_text('child')})
    expanded = retrieval.expand_parent_chunks([child])
    grounded = qa._to_grounded(expanded)
    assert grounded[0].id == 'c' and grounded[0].text == 'parent body'
    assert grounded[0].meta['source_row_sha256'] == answer_evidence.sha256_text('child')
    assert grounded[0].meta['index_generation'] == 'g1'
    trace = {}
    qa._set_final_trace(trace, time.perf_counter(), grounded, guard_reason=None, used_fallback=False, answer_mode='grounded', citations_count=1)
    assert trace['active_generation_id'] is None


import pytest


@pytest.mark.parametrize('parent_generation,child_generation,expected', [
    ('g1', 'g1', 'g1'),
    ('g1', 'g2', None),
    (None, 'g2', None),
    ('g1', None, None),
    (None, None, None),
])
def test_direct_parent_then_better_child_preserves_generation_provenance(monkeypatch, parent_generation, child_generation, expected):
    from types import SimpleNamespace
    from rag_core import qa
    import config
    import time

    parent_hash = answer_evidence.sha256_text('parent body')
    child_hash = answer_evidence.sha256_text('child body')
    parent_meta = {'id': 'p', 'chunk_role': 'parent', 'tenant_id': 'default',
                   'source_doc': 'doc', 'source_pages': [1], '_evidence_source_row_sha256': parent_hash}
    child_meta = {'id': 'c', 'parent_chunk_id': 'p', 'chunk_role': 'child', 'tenant_id': 'default',
                  'source_doc': 'doc', 'source_pages': [1], '_evidence_source_row_sha256': child_hash}
    if parent_generation is not None:
        parent_meta['index_generation'] = parent_generation
    if child_generation is not None:
        child_meta['index_generation'] = child_generation
    parent = retrieval.RetrievedChunk(text='parent body', metadata=parent_meta, score=0.3)
    child = retrieval.RetrievedChunk(text='child body', metadata=child_meta, score=0.1)
    monkeypatch.setattr(config, 'ENABLE_PARENT_EXPANSION', True)
    monkeypatch.setattr(retrieval, '_index_for_collection', lambda _: SimpleNamespace(
        id_to_index={'p': 0}, row=lambda _: {'id': 'p', 'text': 'parent body', 'tenant_id': 'default'}))
    result = retrieval.expand_parent_chunks([parent, child])
    assert len(result) == 1 and result[0].score == 0.1
    grounded = qa._to_grounded(result)
    assert grounded[0].id == 'c' and grounded[0].text == 'parent body'
    assert grounded[0].meta['source_row_sha256'] == child_hash != parent_hash
    assert grounded[0].meta.get('index_generation') == child_generation
    trace = {}
    qa._set_final_trace(trace, time.perf_counter(), grounded, guard_reason=None,
                        used_fallback=False, answer_mode='grounded', citations_count=1)
    assert trace['active_generation_id'] == expected


@pytest.mark.parametrize('direct_parent', [True, False])
def test_better_second_child_keeps_parent_body_generation(monkeypatch, direct_parent):
    from types import SimpleNamespace
    from rag_core import qa
    import config
    import time

    parent_row = {'id': 'p', 'text': 'parent body', 'chunk_role': 'parent', 'tenant_id': 'default',
                  'index_generation': 'parent-generation', 'source_doc': 'doc', 'source_pages': [1]}
    monkeypatch.setattr(config, 'ENABLE_PARENT_EXPANSION', True)
    monkeypatch.setattr(retrieval, '_index_for_collection', lambda _: SimpleNamespace(
        id_to_index={'p': 0}, row=lambda _: parent_row))
    parent = retrieval.RetrievedChunk(text='parent body', metadata=parent_row, score=0.5)
    first = retrieval.RetrievedChunk(text='first child', score=0.3, metadata={
        'id': 'c1', 'parent_chunk_id': 'p', 'tenant_id': 'default', 'index_generation': 'parent-generation',
        '_evidence_source_row_sha256': answer_evidence.sha256_text('first child')})
    second = retrieval.RetrievedChunk(text='second child', score=0.1, metadata={
        'id': 'c2', 'parent_chunk_id': 'p', 'tenant_id': 'default', 'index_generation': 'other-generation',
        '_evidence_source_row_sha256': answer_evidence.sha256_text('second child')})
    expanded = retrieval.expand_parent_chunks(([parent] if direct_parent else []) + [first, second])
    assert len(expanded) == 1 and expanded[0].text == 'parent body' and expanded[0].score == 0.1
    grounded = qa._to_grounded(expanded)
    assert grounded[0].id == 'c2'
    assert grounded[0].meta['index_generation'] == 'other-generation'
    assert grounded[0].meta['source_row_sha256'] == answer_evidence.sha256_text('second child')
    trace = {}
    qa._set_final_trace(trace, time.perf_counter(), grounded, guard_reason=None,
                        used_fallback=False, answer_mode='grounded', citations_count=1)
    assert trace['active_generation_id'] is None
