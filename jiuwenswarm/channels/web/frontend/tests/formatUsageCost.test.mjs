import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { formatUsageCostLine } from '../node_modules/.cache/format-usage-cost/utils/formatUsageCost.js';

describe('formatUsageCostLine', () => {
  it('shows total only for provider-billed cost without split', () => {
    assert.equal(
      formatUsageCostLine({ total_cost: 0.0038 }),
      '$0.0038 total',
    );
  });

  it('makes displayed total equal displayed in + out', () => {
    const line = formatUsageCostLine({
      input_cost: 0.00196,
      output_cost: 0.00196,
      total_cost: 0.00392,
    });
    assert.equal(line, '$0.0020 in / $0.0020 out / $0.0040 total');
  });

  it('drops conflicting split when billed total disagrees', () => {
    assert.equal(
      formatUsageCostLine({
        input_cost: 0.001,
        output_cost: 0.002,
        total_cost: 0.0015,
      }),
      '$0.0015 total',
    );
  });

  it('keeps partial marker on total-only line', () => {
    assert.equal(
      formatUsageCostLine({ total_cost: 0.01, cost_status: 'partial' }),
      '$0.0100 total (partial)',
    );
  });

  it('returns null when there is no money', () => {
    assert.equal(formatUsageCostLine({ total_cost: 0 }), null);
    assert.equal(formatUsageCostLine({}), null);
  });
});
