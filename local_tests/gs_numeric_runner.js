"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sandbox = Object.create(null);
const source = fs.readFileSync(path.join(__dirname, "fixtures", "gs_numeric_reference.js"), "utf8");
vm.runInNewContext(source, sandbox, { timeout: 1000, codeGeneration: { strings: false, wasm: false } });
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const result = {
  numbers: (data.numbers || []).map(value => sandbox._toScoreNumber(value)),
  comparisons: (data.comparisons || []).map(pair => sandbox.meetWithMaps(pair[0], pair[1])),
  rounds: (data.rounds || []).map(value => Math.round(value)),
  ranks: Array.from(sandbox._buildCompetitionRankMap(
    (data.ranks || []).map(pair => pair[0]), new Map(data.ranks || [])
  ).entries()),
  isolated: typeof sandbox.require === "undefined" && typeof sandbox.fetch === "undefined",
};
process.stdout.write(JSON.stringify(result));
