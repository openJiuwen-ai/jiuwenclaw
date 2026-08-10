from jiuwenswarm.symphony.optimization.convergence import ConvergenceDetector


def test_no_convergence_while_improving():
    det = ConvergenceDetector(threshold=0.01, window=3)
    for reward in (0.2, 0.4, 0.6, 0.8):
        state = det.update(reward)
    assert state.converged is False
    assert state.best == 0.8


def test_convergence_on_plateau():
    det = ConvergenceDetector(threshold=0.01, window=3)
    states = [det.update(r) for r in (0.5, 0.9, 0.9, 0.9, 0.9)]
    assert any(s.converged for s in states)
    assert states[-1].converged is True


def test_convergence_on_target():
    det = ConvergenceDetector(threshold=0.01, window=3, target=0.75)
    state = det.update(0.8)
    assert state.converged is True
    assert "target" in state.reason


def test_no_improvement_over_window_stops():
    det = ConvergenceDetector(threshold=0.05, window=2)
    # improves to 0.6 then stalls; after window of no >0.05 gains -> converged
    seen = [det.update(r) for r in (0.6, 0.61, 0.62)]
    assert seen[-1].converged is True
