"""Isolated synthetic originals and labels; no real model or personal records."""

from pathlib import Path

import pytest

from learning_library.article_classification import ArticleClassification, ClassificationInput
from learning_library.review import ReviewConflict
from learning_library.storage import Store


def version(store, text):
    store.save('https://example.com/synthetic', {'text': text, 'title': 'Synthetic', 'url': 'https://example.com/synthetic'}, None)
    return store.documents()[0]['version_id']


def test_classification_is_version_bound_append_only_and_retries_do_not_duplicate(tmp_path: Path):
    store = Store(tmp_path)
    try:
        old = version(store, 'Synthetic explanation of tool orchestration.')
        module = ArticleClassification(store)
        payload = ClassificationInput(version_id=old, submission_id='synthetic', kind='technical',
                                      reason='合成技术解释。', quote='explanation of tool orchestration.')
        first = module.record(payload, origin='agent')
        assert module.record(payload, origin='agent') == first
        assert first['kind'] == 'technical' and first['origin'] == 'agent'
        assert first['evidence']['matched']
        with pytest.raises(ReviewConflict):
            module.record(payload.model_copy(update={'kind': 'practice'}), origin='agent')
        new = version(store, 'Synthetic updated practice with a different main purpose.')
        assert module.inspect(new)['kind'] == 'unclassified'
        assert module.inspect(old)['record_id'] == first['record_id']
        assert store.db.execute('SELECT COUNT(*) FROM article_classifications').fetchone()[0] == 1
    finally:
        store.db.close()


def test_classification_rejects_missing_evidence_and_does_not_change_original(tmp_path):
    store = Store(tmp_path)
    try:
        identifier = version(store, 'Synthetic practice from a specific task.')
        module = ArticleClassification(store)
        before = store.version(identifier)
        payload = ClassificationInput(version_id=identifier, submission_id='bad', kind='practice',
                                      reason='合成分类。', quote='This does not occur in the source.')
        with pytest.raises(ValueError, match='没有匹配'):
            module.record(payload, origin='agent')
        assert module.inspect(identifier)['kind'] == 'unclassified'
        assert store.version(identifier) == before
        assert store.db.execute('SELECT COUNT(*) FROM article_classifications').fetchone()[0] == 0
    finally:
        store.db.close()
