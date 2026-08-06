import assert from "node:assert/strict";
import { formatUsd, formatModelCost } from "../dist/core/commands/builtins/usage.js";

assert.equal(formatUsd(0), "$0.00");
assert.equal(formatUsd(-1), "$0.00");
assert.equal(formatUsd(Number.NaN), "$0.00");
assert.equal(formatUsd(0.0042), "$0.0042");
assert.equal(formatUsd(0.01), "$0.01");
assert.equal(formatUsd(1.234), "$1.23");

// Fully priced: show the money.
assert.equal(formatModelCost({ total_cost: 1.5, unpriced: false }), "$1.50");

// Fully unpriced: never pretend it was free.
assert.equal(formatModelCost({ total_cost: 0, unpriced: true }), "unpriced");

// Partial / understated: keep the USD visible so /usage does not hide a real total.
assert.equal(
  formatModelCost({ total_cost: 0.42, unpriced: true }),
  "$0.42 (understated)",
);

console.log("usage-cost.test.mjs: ok");
