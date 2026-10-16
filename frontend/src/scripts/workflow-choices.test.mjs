import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowChoices} from './workflow-choices.js';

const rows=[
  {run_id:'old',url:'https://example.com/article',created:'2026-10-01'},
  {run_id:'new',url:'https://example.com/article',created:'2026-10-02'},
  {run_id:'other',url:'https://example.com/other',created:'2026-10-03'},
];
test('latest run per URL is visible; source rows and historical access are preserved',()=>{
  const before=JSON.stringify(rows);
  assert.deepEqual(workflowChoices(rows,'new').map(row=>row.run_id),['other','new']);
  assert.deepEqual(workflowChoices(rows,'old').map(row=>[row.run_id,row.historical]),[
    ['other',false],['new',false],['old',true],
  ]);
  assert.equal(JSON.stringify(rows),before);
  assert.deepEqual(workflowChoices([],'unknown'),[]);
});
