# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Registration and opt-in mount tests for the governance rails.

Asserts each rail is declared in the manifest catalog, registers into
openjiuwen's rail-provider registry, builds through its factory, and is mounted
by ``config_specs`` only when the team config enables it.
"""

from __future__ import annotations

import unittest

from openjiuwen.agent_teams.harness.manifest import (
    get_catalog,
    resolve_factory,
)
from openjiuwen.harness.schema import deep_agent_spec as das

from jiuwenswarm.agents.swarm import register_swarm_providers, registry
from jiuwenswarm.agents.swarm.config_specs import _governance_rails
from jiuwenswarm.agents.harness.common.rails.quality_gate_rail import QualityGateRail
from jiuwenswarm.agents.harness.common.rails.quality_gate_scorers import (
    has_scorer,
    register_scorer,
    resolve_scorer,
)
from jiuwenswarm.agents.harness.common.rails.usage_report_rail import UsageReportRail


class TestRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        register_swarm_providers()

    def test_constants_exported(self) -> None:
        self.assertEqual(registry.QUALITY_GATE, "swarm.quality_gate")
        self.assertEqual(registry.USAGE_REPORT, "swarm.usage_report")

    def test_elements_in_catalog(self) -> None:
        catalog = get_catalog()
        self.assertIn("swarm.quality_gate", catalog)
        self.assertIn("swarm.usage_report", catalog)

    def test_elements_registered_as_rails(self) -> None:
        self.assertIn("swarm.quality_gate", das._RAIL_PROVIDER_REGISTRY)
        self.assertIn("swarm.usage_report", das._RAIL_PROVIDER_REGISTRY)

    def test_registry_catalog_parity(self) -> None:
        # Every swarm.* constant must have a catalog descriptor (parity
        # invariant the upstream manifest test enforces globally).
        catalog = get_catalog()
        for name in (registry.QUALITY_GATE, registry.USAGE_REPORT):
            self.assertIn(name, catalog, f"missing descriptor for {name}")

    def test_quality_gate_factory_builds_rail(self) -> None:
        descriptor = get_catalog()["swarm.quality_gate"]
        factory = resolve_factory(descriptor.factory_ref)
        rail = factory({"scorer": "always_pass", "threshold": 0.7, "gate_name": "g"}, None)
        self.assertIsInstance(rail, QualityGateRail)
        self.assertEqual(rail.threshold, 0.7)
        self.assertEqual(rail.gate_name, "g")

    def test_usage_report_factory_builds_rail(self) -> None:
        descriptor = get_catalog()["swarm.usage_report"]
        factory = resolve_factory(descriptor.factory_ref)
        rail = factory({"report_path": "/tmp/r.json", "default_label": "x"}, None)
        self.assertIsInstance(rail, UsageReportRail)


class TestOptInMount(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        self.assertEqual(_governance_rails({}), [])
        self.assertEqual(_governance_rails({"usage_report": {}, "quality_gate": {}}), [])
        self.assertEqual(
            _governance_rails({"usage_report": {"enabled": False}, "quality_gate": {"enabled": False}}),
            [],
        )

    def test_quality_gate_enabled(self) -> None:
        specs = _governance_rails({
            "quality_gate": {"enabled": True, "scorer": "min_length", "threshold": 0.8},
        })
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].type, registry.QUALITY_GATE)
        self.assertEqual(specs[0].params["scorer"], "min_length")

    def test_both_enabled(self) -> None:
        specs = _governance_rails({
            "usage_report": {"enabled": True},
            "quality_gate": {"enabled": True},
        })
        self.assertEqual({s.type for s in specs}, {registry.USAGE_REPORT, registry.QUALITY_GATE})

    def test_enabled_mounts_one_spec(self) -> None:
        specs = _governance_rails({"usage_report": {"enabled": True, "report_path": "/tmp/u.json"}})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].type, registry.USAGE_REPORT)
        self.assertEqual(specs[0].params["report_path"], "/tmp/u.json")

    def test_only_declared_keys_are_forwarded(self) -> None:
        specs = _governance_rails({
            "usage_report": {"enabled": True, "default_label": "stage", "unknown_key": 1},
        })
        self.assertEqual(specs[0].params, {"default_label": "stage"})


class TestScorerRegistry(unittest.TestCase):
    def test_builtin_scorers_present(self) -> None:
        self.assertTrue(has_scorer("always_pass"))
        self.assertTrue(has_scorer("always_fail"))
        self.assertTrue(has_scorer("min_length"))

    def test_register_and_resolve(self) -> None:
        from jiuwenswarm.agents.harness.common.rails.quality_gate_rail import GateVerdict

        def custom(text, context):
            return GateVerdict(score=0.5, passed=False)

        register_scorer("custom_test_scorer", custom)
        self.assertIs(resolve_scorer("custom_test_scorer"), custom)

    def test_resolve_unknown_raises(self) -> None:
        with self.assertRaises(KeyError):
            resolve_scorer("nope_not_registered")


if __name__ == "__main__":
    unittest.main()
