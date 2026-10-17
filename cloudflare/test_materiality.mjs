import assert from "node:assert/strict";
import { fastOutcome } from "./src/hot-edge.js";
const pick=({material,polarity})=>({material,polarity});
for (const headline of ["Company announces topline Phase 2 results", "Company reports pivotal trial results",
  "FDA issues decision regarding NDA", "Company reports interim Phase 3 analysis"])
  assert.deepEqual(pick(fastOutcome(headline)), { material: true, polarity: "unknown" });
assert.deepEqual(pick(fastOutcome("Company met its primary endpoint")), { material: true, polarity: "positive" });
assert.equal(fastOutcome("Positive update: trial did not meet its primary endpoint").polarity, "negative");
assert.equal(fastOutcome("Company appoints a new chief financial officer").material, false);
assert.equal(fastOutcome('PDUFA decision extended').polarity,'negative');
console.log("edge independent materiality: passed");
