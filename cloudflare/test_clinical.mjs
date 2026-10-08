import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {classifyClinical} from './src/clinical-events.js';
import {fastOutcome} from './src/hot-edge.js';
const cases=JSON.parse(readFileSync(new URL('../tests/fixtures/clinical_announcements.json',import.meta.url),'utf8'));
for(const item of cases){
 const text=item.headline+'\n'+item.summary, result=classifyClinical(text,fastOutcome(text));
 for(const [key,value] of Object.entries(item.expected)) assert.equal(result[key],value,`${item.id}.${key}`);
}
console.log('shared clinical upcoming/confirmed classification: passed');
