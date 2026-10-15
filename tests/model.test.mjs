import test from 'node:test';
import assert from 'node:assert/strict';
import {simulate,stateFor} from '../examples/token-bucket/model.mjs';
const c={capacity:4,refill_per_second:1,initial_tokens:4,request_cost:1};
test('burst consumes stock, rejects remaining requests',()=>{
 const s=simulate(c,Array.from({length:6},(_,id)=>({time:0,id})),0);
 assert.deepEqual([s.tokens,s.accepted,s.rejected],[0,4,2]);
});
test('refill clamps to capacity and transfer question uses the same model',()=>{
 assert.equal(simulate(c,[],20).tokens,4);
 const s=stateFor('transfer',1,c);
 assert.deepEqual([s.tokens,s.accepted,s.rejected],[0,2,1]);
});
test('frame seeking does not depend on previous frames',()=>{
 const first=stateFor('mechanism',.6,c);stateFor('transfer',1,c);
 assert.deepEqual(stateFor('mechanism',.6,c),first);
});

import * as kit from '../vendor/anatomy/kit/iso-kit.mjs';
import {mount} from '../examples/token-bucket/mechanism.mjs';
import {readFileSync} from 'node:fs';
test('sample geometry mounts against the real upstream kit API',()=>{
 const job=JSON.parse(readFileSync(new URL('../examples/token-bucket/job.json',import.meta.url),'utf8'));
 const element={innerHTML:'',querySelectorAll:()=>[]};
 const mechanism=mount(element,{kit,job});
 assert.ok(element.innerHTML.startsWith('<svg'));
 assert.ok(!element.innerHTML.includes('NaN'));
 for(const scene of job.scenes) mechanism.frame({scene,progress:.8,setReadout:()=>{}});
});
