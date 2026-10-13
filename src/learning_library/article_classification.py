"""Version-bound reading categories, independent of value judgments and personal notes."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .evidence import _align, _code_ranges
from .review import ReviewConflict
from .storage import now

ReadingKind = Literal['practice', 'technical', 'updates', 'unclassified']
LABELS = {'practice': '实践经验', 'technical': '技术原理', 'updates': '产品动态', 'unclassified': '未分类'}


class ClassificationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version_id: int = Field(gt=0, strict=True)
    submission_id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    kind: ReadingKind
    reason: str = Field(min_length=1, max_length=120)
    quote: str = Field(min_length=8, max_length=700)


class ArticleClassification:
    """inspect reads saved categories; record appends explicit corrections without model calls."""

    def __init__(self, store):
        self.store = store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS article_classifications (
                id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
                submission_id TEXT NOT NULL, created TEXT NOT NULL,
                origin TEXT NOT NULL CHECK(origin IN ('agent','human','demo')),
                payload TEXT NOT NULL, evidence TEXT NOT NULL,
                UNIQUE(version_id, submission_id)
            );
        ''')

    def inspect(self, version_id):
        self.store.version(version_id)
        base = {'version_id': version_id, 'kind': 'unclassified', 'label': LABELS['unclassified'],
                'origin': None, 'reason': '', 'evidence': None, 'record_id': None, 'insights_id': None}
        row = self.store.db.execute(
            'SELECT * FROM article_classifications WHERE version_id=? ORDER BY id DESC LIMIT 1',
            (version_id,),
        ).fetchone()
        if row:
            payload = json.loads(row['payload'])
            return base | {'kind': payload['kind'], 'label': LABELS[payload['kind']],
                           'origin': row['origin'], 'reason': payload['reason'],
                           'created': row['created'], 'record_id': row['id'],
                           'evidence': json.loads(row['evidence'])}
        if not self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='article_insights'").fetchone():
            return base
        result = self.store.db.execute('''
            SELECT id,finished,output FROM article_insights WHERE status='completed'
            AND json_extract(input,'$.version_id')=? ORDER BY id DESC LIMIT 1
        ''', (version_id,)).fetchone()
        output = json.loads(result['output']) if result and result['output'] else {}
        judgment = output.get('judgment', {})
        kind = judgment.get('reading_kind', 'unclassified')
        if kind not in LABELS or kind == 'unclassified' or output.get('quote_alignment') != 'passed':
            return base
        evidence = next((check for check in output.get('evidence_checks', [])
                         if check['target'] == 'classification' and check['matched']), None)
        if not evidence or not judgment.get('reading_reason'):
            return base
        return base | {'kind': kind, 'label': LABELS[kind], 'origin': 'model_api',
                       'reason': judgment['reading_reason'], 'evidence': evidence,
                       'created': result['finished'], 'insights_id': result['id']}

    def record(self, payload: ClassificationInput, *, origin):
        if origin not in {'agent', 'human', 'demo'}:
            raise ValueError('需要明确分类来源。')
        payload = ClassificationInput.model_validate(payload.model_dump())
        encoded = json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True)
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            text = self.store.version(payload.version_id)['document']['text']
            existing = self.store.db.execute(
                'SELECT * FROM article_classifications WHERE version_id=? AND submission_id=?',
                (payload.version_id, payload.submission_id),
            ).fetchone()
            if existing:
                if existing['payload'] != encoded or existing['origin'] != origin:
                    raise ReviewConflict('同一提交编号不能保存不同分类。')
            else:
                evidence = _align(payload.quote, [{'start': 0, 'text': text}], _code_ranges(text))
                if not evidence['matched']:
                    raise ValueError('分类依据没有匹配当前版本原文。')
                self.store.db.execute('''
                    INSERT INTO article_classifications(version_id,submission_id,created,origin,payload,evidence)
                    VALUES(?,?,?,?,?,?)
                ''', (payload.version_id, payload.submission_id, now(), origin, encoded,
                      json.dumps({'quote': payload.quote, **evidence}, ensure_ascii=False)))
        return self.inspect(payload.version_id)
