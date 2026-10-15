"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm"), crypto = require("node:crypto");
const input = JSON.parse(fs.readFileSync(0,"utf8"));
const requests = [], reads = [];
let uuid = 0;
const bytes = buffer => Array.from(buffer, value => value > 127 ? value - 256 : value);
const sandbox = {
  Date: class extends Date { static now() {return 1000000;} },
  PropertiesService: {getScriptProperties(){return {getProperty(name){reads.push(name);return name === "BBL_CONTROL_CONFIG" ? JSON.stringify(input.config) : input.key;}};}},
  Utilities: {getUuid(){return "mock-uuid-"+(++uuid);}, newBlob(value){return {getBytes(){return bytes(Buffer.from(value,"utf8"));}};},
    DigestAlgorithm:{SHA_256:"sha256"},Charset:{UTF_8:"utf8"},
    computeDigest(algorithm,value){return bytes(crypto.createHash("sha256").update(value,"utf8").digest());},
    computeHmacSha256Signature(value,key){return bytes(crypto.createHmac("sha256",key).update(value,"utf8").digest());}},
  UrlFetchApp:{fetch(url,options){requests.push({url,options});if(input.response === "timeout")throw new Error("mock timeout");
    return {getResponseCode(){return input.response === "rejected" ? 403 : 202;},
      getContentText(){return JSON.stringify(input.response === "bad_receipt" ? {state:"completed"} : {state:"accepted",job_id:"job_mock"});}};}},
  event:{range:{getSheet(){return {getSheetId(){return input.sheet_id ?? 1;},getParent(){return {getId(){return input.spreadsheet_id ?? "book_mock";}};}};},
    getRow(){return input.row ?? 4;},getLastRow(){return input.row ?? 4;},getColumn(){return 2;},getLastColumn(){return 2;}}}
};
const context=vm.createContext(sandbox,{codeGeneration:{strings:false,wasm:false}});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../integrations/gs/local_control_bridge.gs"),"utf8"),context,{timeout:1000});
vm.runInContext("outcome="+(input.is_timer ? "reconcileTimer()" : "onEdit(event)"),context,{timeout:1000});
process.stdout.write(JSON.stringify({outcome:context.outcome,requests,reads}));
