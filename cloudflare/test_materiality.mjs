import assert from "node:assert/strict";
import { fastOutcome } from "./src/hot-edge.js";
for (const headline of ["Company announces topline Phase 2 results", "Company reports pivotal trial results",
  "FDA issues decision regarding NDA", "Company reports interim Phase 3 analysis", "PDUFA decision extended"])
  assert.deepEqual(fastOutcome(headline), { material: true, polarity: "unknown" });
assert.deepEqual(fastOutcome("Company met its primary endpoint"), { material: true, polarity: "positive" });
assert.equal(fastOutcome("Positive update: trial did not meet its primary endpoint").polarity, "negative");
assert.equal(fastOutcome("Company appoints a new chief financial officer").material, false);
console.log("edge independent materiality: passed");
