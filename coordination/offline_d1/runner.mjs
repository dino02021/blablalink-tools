import fs from "node:fs";
import childProcess from "node:child_process";
import {createHash} from "node:crypto";
import { syncBuiltinESMExports } from "node:module";

// The parent attaches its Job Object before opening this one-use start gate.
const gate=fs.readFileSync(0,"utf8");
const objectSession=Boolean(process.argv[4]);
if(gate!=="start\n" || !process.argv[2] || fs.statSync(process.argv[2]).size>(objectSession?24*1024*1024:65536))process.exit(2);
const input=JSON.parse(fs.readFileSync(process.argv[2],"utf8"));
if(objectSession&&(!Array.isArray(input.operations)||input.operations.length<1||input.operations.length>2048||input.operations.some(operation=>operation.kind!=="batch"||!Array.isArray(operation.statements)||operation.statements.length<1||operation.statements.length>50||Buffer.byteLength(JSON.stringify(operation))>65536)))process.exit(2);
function bindValue(value){
  if(value===null||typeof value!=="object")return value;
  if(Object.keys(value).sort().join(',')!=="base64,sha256,type"||value.type!=="d1_binary_v1"||typeof value.base64!=="string"||value.base64.length>43692)throw new Error("invalid binary control value");
  const bytes=Buffer.from(value.base64,"base64");
  if(bytes.length<1||bytes.length>32768||bytes.toString("base64")!==value.base64||createHash("sha256").update(bytes).digest("hex")!==value.sha256)throw new Error("binary control integrity mismatch");
  return new Uint8Array(bytes);
}
function encodeBinaryResults(value){
  if(value&&typeof value==="object"){
    if(Array.isArray(value.results))for(const row of value.results){
      if(row&&Array.isArray(row.chunk_data)){
        const bytes=Buffer.from(row.chunk_data);
        if(bytes.length<1||bytes.length>32768)throw new Error("binary read bound exceeded");
        row.chunk_data={type:"d1_binary_v1",base64:bytes.toString("base64"),sha256:createHash("sha256").update(bytes).digest("hex")};
      }
      encodeBinaryResults(row);
    }
    for(const [key,child] of Object.entries(value))if(key!=="results"){
      if(Array.isArray(child))child.forEach(encodeBinaryResults);
      else if(child&&typeof child==="object")encodeBinaryResults(child);
    }
  }
  return value;
}
const children=[];
const spawn=childProcess.spawn;
childProcess.spawn=function(...arguments_){const child=spawn.apply(this,arguments_);children.push(child);return child;};
syncBuiltinESMExports();
const {Miniflare,Log,LogLevel}=await import("miniflare");
const {compatibilityDate,version:workerdVersion}=await import("workerd");
const manifest=JSON.parse(fs.readFileSync(new URL("node_modules/miniflare/package.json",import.meta.url),"utf8"));
let emulator;
let blocked=0;
let result;
try{
  emulator=new Miniflare({
    modules:true,
    script:`export default {async fetch(request){
      if(new URL(request.url).pathname==='/blocked-probe'){
        const response=await fetch('https://mock-provider.invalid/');
        return new Response(response.status===403?'blocked':'unexpected',{status:response.status===403?200:500});
      }
      return new Response('disabled',{status:503});
    }};`,
    compatibilityDate,
    host:"127.0.0.1",port:0,inspectorPort:0,
    d1Databases:["DB"],d1Persist:process.argv[3]??false,
    outboundService(){blocked++;return new Response("local-network-denied",{status:403});},
    log:new Log(LogLevel.NONE),
  });
  const db=await emulator.getD1Database("DB");
  if(input.hold_after_ready===true){
    process.stdout.write(JSON.stringify({is_runtime_ready:true,child_pids:children.map(child=>child.pid)})+"\n");
    await new Promise(()=>{});
  }
  // D1 exec() parses lines differently from SQLite executescript(). Import only
  // these trusted, semicolon-delimited fixture statements via prepare().run().
  const schemaStatements=Array.isArray(input.schema_statements)?input.schema_statements:
    input.schema.replace(/^\s*--.*$/gm,"").split(";").map(statement=>statement.trim()).filter(Boolean);
  if(schemaStatements.length)await db.batch(schemaStatements.map(statement=>typeof statement==="string"?
    db.prepare(statement):db.prepare(statement.sql).bind(...statement.parameters.map(bindValue))));
  const operations=[];
  const collect=operation=>{
    const encoded=encodeBinaryResults(operation);
    if(objectSession&&Buffer.byteLength(JSON.stringify(encoded))>65536)throw new Error("object reply frame exceeds 64 KiB");
    operations.push(encoded);
  };
  for(const operation of input.operations){
    try{
      if(operation.kind==="batch"){
        collect({outcome:"completed",results:await db.batch(operation.statements.map(statement=>
          db.prepare(statement.sql).bind(...statement.parameters.map(bindValue))))});
      }else if(operation.kind==="parallel_batches"){
        const batches=await Promise.all(operation.batches.map(async statements=>{
          try{return {outcome:"completed",results:await db.batch(statements.map(statement=>db.prepare(statement.sql).bind(...statement.parameters.map(bindValue))))};}
          catch(error){return {outcome:"rejected",error_name:error.name,error_message:String(error.message).slice(0,1000)};}
        }));
        operations.push({outcome:"completed",batches});
      }else if(operation.kind==="lookup_or_batch"){
        const lookup=await db.prepare(operation.lookup.sql).bind(...operation.lookup.parameters.map(bindValue)).all();
        if(lookup.results.length>1)throw new Error("receipt lookup cardinality differs");
        if(lookup.results.length===1){
          const row=lookup.results[0],expected=operation.receipt;
          if(Object.keys(row).length!==Object.keys(expected).length||Object.keys(expected).some(key=>row[key]!==expected[key]))throw new Error("receipt lookup intent differs");
          operations.push({outcome:"completed",mode:"lookup_replay",lookup_result:lookup,receipt:row});
        }else{
          operations.push({outcome:"completed",mode:"batch",lookup_result:lookup,
            results:await db.batch(operation.statements.map(statement=>db.prepare(statement.sql).bind(...statement.parameters.map(bindValue))))});
        }
      }else if(operation.kind==="query"){
        operations.push({outcome:"completed",result:await db.prepare(operation.sql).bind(...operation.parameters.map(bindValue)).all()});
      }else throw new Error("unsupported local contract operation");
    }catch(error){operations.push({outcome:"rejected",error_name:error.name,error_message:String(error.message).slice(0,1000)});}
  }
  let isNetworkBlocked=true;
  if(input.probe_network===true){
    const response=await emulator.dispatchFetch("http://local-fixture.invalid/blocked-probe");
    isNetworkBlocked=response.status===200&&(await response.text())==="blocked"&&blocked===1;
  }
  result={is_mock:true,network_providers:[],miniflare_version:manifest.version,workerd_version:workerdVersion,
    compatibility_date:compatibilityDate,operations,blocked_outbound_attempts:blocked,is_network_blocked:isNetworkBlocked,
    is_environment_sanitized:["CLOUDFLARE_API_TOKEN","DISCORD_TOKEN","GOOGLE_APPLICATION_CREDENTIALS","NODE_OPTIONS"].every(key=>!(key in process.env)),
    sdk_child_count:children.length};
}catch(error){result={is_mock:true,outcome:"emulator_failed",error_name:error.name,error_message:String(error.message).slice(0,1000)};}
finally{if(emulator)await emulator.dispose();}
// dispose() must reap workerd before successful evidence leaves this process.
for(const child of children){
  if(child.exitCode===null&&child.signalCode===null)await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error("SDK child did not close")),3000);
    child.once("exit",()=>{clearTimeout(timer);resolve();});
  });
}
result.are_sdk_children_closed=children.every(child=>child.exitCode!==null||child.signalCode!==null);
const serialized=JSON.stringify(encodeBinaryResults(result));
if(objectSession){
  if(Buffer.byteLength(serialized)>32*1024*1024)throw new Error("object session reply exceeds bound");
  fs.writeFileSync(process.argv[4],serialized,{flag:"wx"});
  process.stdout.write(JSON.stringify({reply_size:Buffer.byteLength(serialized),reply_sha256:createHash("sha256").update(serialized).digest("hex")})+"\n");
}else process.stdout.write(serialized+"\n");
