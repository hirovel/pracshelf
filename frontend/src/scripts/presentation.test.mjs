import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../../src/learning_library/ui.js',import.meta.url),'utf8');
function fixture({saved=null,system=false,blocked=false}={}){
  const values=new Map(saved?[['workspace-motion',saved]]:[]),events=new EventTarget();
  let systemChange,cancelled=0,animations=0;
  const media={matches:system,addEventListener:(_,callback)=>{systemChange=callback;}};
  const document={documentElement:{dataset:{}},addEventListener:()=>{},
    getAnimations:()=>[{cancel:()=>cancelled++}]};
  const localStorage={getItem:key=>{if(blocked)throw new Error('Storage blocked');return values.get(key)??null;},
    setItem:(key,value)=>{if(blocked)throw new Error('Storage blocked');values.set(key,value);}};
  const context=vm.createContext({document,localStorage,matchMedia:()=>media,Event,
    addEventListener:events.addEventListener.bind(events),dispatchEvent:events.dispatchEvent.bind(events)});
  vm.runInContext(source,context);
  const ui=vm.runInContext('readingUI',context);
  const element={getAnimations:()=>[],animate:()=>animations++};
  return {ui,document,values,events,element,
    system(value){media.matches=value;systemChange();},
    storage(key,value){const event=new Event('storage');Object.assign(event,{key,newValue:value});events.dispatchEvent(event);},
    get animations(){return animations;},get cancelled(){return cancelled;}};
}

test('saved motion preference is applied before content; system choice can restore effects',()=>{
  const state=fixture({saved:'reduce'});let updates=0;
  assert.equal(state.ui.motionPreference(),'reduce');
  assert.equal(state.document.documentElement.dataset.motion,'reduce');
  state.ui.change(state.element,()=>updates++);
  assert.equal(updates,1);assert.equal(state.animations,0);
  assert.equal(state.ui.setMotionPreference('system'),true);
  assert.equal(state.values.get('workspace-motion'),'system');
  state.ui.change(state.element,()=>updates++);
  assert.equal(updates,2);assert.equal(state.animations,1);
});

test('system changes cancel effects and explicit reduce remains effective',()=>{
  const state=fixture();
  state.ui.change(state.element,()=>{});assert.equal(state.animations,1);
  state.system(true);assert.equal(state.ui.reduceMotion(),true);
  assert.equal(state.document.documentElement.dataset.motion,'reduce');
  assert.ok(state.cancelled>0);
  state.ui.change(state.element,()=>{});assert.equal(state.animations,1);
  state.ui.setMotionPreference('reduce');state.system(false);
  assert.equal(state.ui.reduceMotion(),true);
  state.ui.setMotionPreference('system');assert.equal(state.ui.reduceMotion(),false);
});

test('same-origin preference changes synchronize existing pages and notify controls',()=>{
  const state=fixture();let notifications=0;
  state.events.addEventListener('reading:motion',()=>notifications++);
  state.storage('workspace-theme','reduce');assert.equal(notifications,0);
  state.storage('workspace-motion','reduce');assert.equal(state.ui.reduceMotion(),true);
  assert.equal(notifications,1);
  state.storage('workspace-motion','system');assert.equal(state.ui.reduceMotion(),false);
  state.storage(null,null);assert.equal(state.ui.motionPreference(),'system');
});

test('unavailable storage leaves a usable in-page preference',()=>{
  const state=fixture({blocked:true});
  assert.equal(state.ui.motionPreference(),'system');
  assert.equal(state.ui.setMotionPreference('reduce'),false);
  assert.equal(state.ui.reduceMotion(),true);
  assert.equal(state.document.documentElement.dataset.motion,'reduce');
});
