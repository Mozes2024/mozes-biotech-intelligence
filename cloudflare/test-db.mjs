import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
export function database() {
  const sqlite=new DatabaseSync(':memory:');
  for(const name of ['0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql'])
    sqlite.exec(readFileSync(new URL(`./migrations/${name}`,import.meta.url),'utf8'));
  const counts={queries:0,writes:0};
  const db={sqlite,counts,prepare(query) {
    let values=[];
    const statement={bind(...args){values=args;return statement;},
      async all(){counts.queries++;return {results:sqlite.prepare(query).all(...values)};},
      async first(){counts.queries++;return sqlite.prepare(query).get(...values)||null;},
      async run(){counts.queries++;const r=sqlite.prepare(query).run(...values);counts.writes+=Number(r.changes);return {meta:{changes:Number(r.changes)}};}};
    return statement;
  },async batch(statements){sqlite.exec('BEGIN');try{const results=[];for(const s of statements)results.push(await s.run());sqlite.exec('COMMIT');return results;}catch(e){sqlite.exec('ROLLBACK');throw e;}}};
  sqlite.exec("INSERT INTO edge_issuers VALUES('123','ZZZZ','Novel Bio',0.99,'SEC-v2C-equity',1,'2026-10-08')");
  return db;
}
export function state() {
  const memory=new Map();let alarm=null;
  return {memory,storage:{get:async k=>structuredClone(memory.get(k)),put:async(k,v)=>{
    if(Buffer.byteLength(JSON.stringify(v))>128*1024)throw Error('DO value too large');memory.set(k,structuredClone(v));
  },getAlarm:async()=>alarm,setAlarm:async v=>{alarm=v}}};
}
