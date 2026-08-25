# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility for older openjiuwen evolution-rail constructors."""

from jiuwenswarm.common.openjiuwen_rail_compat import (
    _wrap_init_for_extra_kwargs,
    filter_unsupported_kwargs,
    install_evolution_rail_kwargs_compat,
)


def test_filter_drops_signal_trigger_when_constructor_lacks_it():
    def _init(self, *, review_trigger=None):
        del self
        return review_trigger

    filtered = filter_unsupported_kwargs(
        _init,
        {"review_trigger": False, "signal_trigger": True},
    )
    assert filtered == {"review_trigger": False}


def test_wrapped_rail_init_ignores_unknown_kwargs():
    class DummyRail:
        def __init__(self, *, review_trigger=None):
            self.review_trigger = review_trigger

    _wrap_init_for_extra_kwargs(DummyRail)
    rail = DummyRail(review_trigger=False, signal_trigger=True)
    assert rail.review_trigger is False


def test_install_evolution_rail_kwargs_compat_is_idempotent():
    install_evolution_rail_kwargs_compat()
    install_evolution_rail_kwargs_compat()
    from openjiuwen.harness.rails import SkillEvolutionRail

    assert getattr(SkillEvolutionRail.__init__, "_jiuwenswarm_kwargs_compat", False)
