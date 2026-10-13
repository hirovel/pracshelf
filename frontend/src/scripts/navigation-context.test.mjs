import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeContext,navigation,resolveContext} from './navigation-context.js';

const a='a'.repeat(32),b='b'.repeat(32);
test('reading to workflow and back preserves article, mode and chosen copy',()=>{
 const read=mergeContext(resolveContext('/read/'+a,'?mode=parallel&result_id=7',{}),{runId:a,readable:true,title:'Synthetic article'});
 const work=mergeContext(resolveContext('/pipeline','?run='+a,read),{runId:a,readable:true,operation:'core'});
 assert.equal(navigation(work).reading,'/read/'+a+'?mode=parallel&result_id=7');
 assert.equal(navigation(work).pipeline,'/pipeline?run='+a+'&operation=core');
});
test('an explicit different article clears stale title, copy and operation',()=>{
 const previous={runId:a,title:'First article',readable:true,reader:{mode:'translation',resultId:'7'},operation:'load'};
 const selected=resolveContext('/read/'+b,'?mode=source',previous);
 assert.equal(selected.runId,b);assert.equal(selected.title,'');assert.equal(selected.reader.resultId,null);assert.equal(selected.operation,null);
 const loaded=mergeContext(selected,{runId:b,readable:true,title:'Second article'});
 assert.equal(navigation(loaded).reading,'/read/'+b+'?mode=source');
 assert.equal(previous.title,'First article');
});
test('no selected or readable article never invents a reader link',()=>{
 assert.equal(navigation({}).reading,null);assert.equal(navigation({}).pipeline,'/pipeline');
 assert.equal(navigation({runId:a,readable:false}).reading,null);
 assert.deepEqual(resolveContext('/pipeline','?run=javascript:bad',{runId:a,readable:true}),{});
});
test('library and collection retain the last real context; unknown fields are dropped',()=>{
 const previous={runId:a,title:'Article',readable:true,reader:{mode:'invalid',resultId:'not-an-id'},note:'Not presentation state',operation:'bad'};
 const current=resolveContext('/inbox','?state=pending',previous);
 assert.equal(navigation(current).reading,'/read/'+a);assert.equal(navigation(current).publication,'/publication?run='+a);
 assert.equal(current.note,undefined);assert.equal(current.operation,null);assert.equal(current.reader.resultId,null);
});
