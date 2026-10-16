import test from 'node:test';
import assert from 'node:assert/strict';
import {libraryView} from './library-view.js';

// Synthetic entries only; no real API, database, or user note is used.
function row(id,decision='inbox',published='2026-10-06'){
  return {run_id:id,title:'AI practice '+id,author:'Developer',domain:'example.com',published,saved:'2026-10-10T12:00:00Z',note:'',classification:{kind:'practice'},curation:{latest:{payload:{decision,category:'workflow'}}}};
}
test('default view includes retained material and excludes archived entries',()=>{
  const input=[row('inbox'),row('kept','kept'),row('archived','archived')];
  const before=JSON.stringify(input);
  assert.deepEqual(libraryView(input,{}).map(item=>item.run_id),['inbox','kept']);
  assert.deepEqual(libraryView(input,{filter:'archived'}).map(item=>item.run_id),['archived']);
  assert.equal(JSON.stringify(input),before);
});
test('publication date stays unknown instead of using the saved date',()=>{
  const items=libraryView([row('missing','inbox',null),row('invalid','inbox','2026-02-30'),row('recent','inbox','2026-10-09')],{});
  assert.equal(items[0].run_id,'recent');
  assert.equal(items[0].publicationDate,'2026-10-09');
  assert.equal(items[1].publicationDate,null);
  assert.equal(items[2].publicationDate,null);
});
test('search includes author and source and composes with category filtering',()=>{
  const entry=row('match');entry.author='Armin Ronacher';entry.domain='lucumr.pocoo.org';
  assert.equal(libraryView([entry],{query:'ronacher',category:'practice'}).length,1);
  assert.equal(libraryView([entry],{query:'lucumr',category:'technical'}).length,0);
});
test('a legacy workflow topic does not imply practice and unknown categories stay unclassified',()=>{
  const legacy=row('legacy');delete legacy.classification;
  const invalid=row('invalid');invalid.classification={kind:'unrecognized'};
  assert.deepEqual(libraryView([legacy,invalid],{category:'unclassified'}).map(item=>item.run_id),['legacy','invalid']);
  assert.equal(libraryView([legacy],{category:'practice'}).length,0);
});
test('notes view uses personal notes and leaves technical review remarks out',()=>{
  const technical=row('technical');technical.review_note='Synthetic technical check';
  const personal=row('personal','kept');personal.note='Synthetic personal note';
  assert.deepEqual(libraryView([technical,personal],{filter:'notes'}).map(item=>item.run_id),['personal']);
});

test('personal note titles and tags are searchable without changing source titles',()=>{
  const entry=row('note');entry.curation.latest.payload.note_title='Local FTS experiment';
  entry.curation.latest.payload.tags=['SQLite','context'];
  assert.equal(libraryView([entry],{query:'fts experiment'}).length,1);
  assert.equal(libraryView([entry],{query:'sqlite'}).length,1);
  assert.equal(entry.title,'AI practice note');
});
