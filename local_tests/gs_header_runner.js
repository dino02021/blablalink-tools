"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const context = vm.createContext({input, SpreadsheetApp:{BorderStyle:{SOLID:"solid",SOLID_MEDIUM:"medium"}}},
  {codeGeneration:{strings:false,wasm:false}});
vm.runInContext(fs.readFileSync(path.join(__dirname,"fixtures/gs_header_reference.js"),"utf8"),context,{timeout:1000});
vm.runInContext(`
output = input.map(item => {
  const values=new Map(), merges=[], widths=[];
  const sheet={
    getLastRow(){return 6;},
    setColumnWidth(column,width){widths.push([column,width]);},
    getRange(row,column,height=1,width=1){
      return {
        clearContent(){for(let r=row;r<row+height;r++)for(let c=column;c<column+width;c++)values.delete(r+':'+c);return this;},
        merge(){merges.push([row-1,row-1+height,column-1,column-1+width]);return this;},
        setValue(value){values.set(row+':'+column,value);return this;},
        setBackground(){return this;},setFontColor(){return this;},setFontWeight(){return this;},setBorder(){return this;}
      };
    }
  };
  const traitLabel=_hcBuildTraitHeaderLabelFromTraits(item.traits);
  if(item.kind==='standard')_hcSetStandardTemplate(sheet,2,item.name,traitLabel);
  else _hcApplyFixedWidthTemplate(sheet,3,item.name,{traitLabel});
  const start=item.kind==='standard'?2:3;
  const titleRow=item.kind==='standard'?2:1;
  const rows=[titleRow,titleRow+1].map(row=>Array.from({length:12},(_,index)=>values.get(row+':'+(start+index))??null));
  return {rows,merges,widths,traitLabel,headers:HEALTHCHECK_SHEET_HEADERS,legacyHeaders:HEALTHCHECK_LEGACY_SHEET_HEADERS,
    elements:HEALTHCHECK_ELEMENT_ORDER,abbreviations:HEALTHCHECK_TRAIT_ABBREVIATIONS,
    isolated:typeof UrlFetchApp==='undefined'&&typeof PropertiesService==='undefined'&&typeof require==='undefined'};
});
`,context,{timeout:1000});
process.stdout.write(JSON.stringify(context.output));
