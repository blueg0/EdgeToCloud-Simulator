# distributions.py

from numpy.random import Generator, default_rng
from typing import Optional

class ExponentialDistribution:
    """
    Exponential (Poisson process inter-arrival) distribution.
    Params:
      rate (float): the rate λ > 0
      rng (Generator, optional): a NumPy Generator instance
    """
    def __init__(self, rate: float, rng: Optional[Generator] = None):
        self.scale = 1.0 / rate
        # Each instance gets its own RNG if not provided
        self.rng = rng if rng is not None else default_rng()

    def __call__(self) -> float:
        return self.rng.exponential(self.scale)

class DeterministicDistribution:
    """
    Fixed-interval (periodic) distribution.
    Params:
      interval (float): constant interval between events
    """
    def __init__(self, interval: float):
        self.interval = interval

    def __call__(self) -> float:
        return self.interval

class DeterministicStartDistribution:
    """
    One-shot start delay, then fixed intervals thereafter.
    Params:
      start (float): delay before first event
      interval (float): delay between subsequent events
    """
    def __init__(self, start: float, interval: float):
        self.start = start
        self.interval = interval
        self._first = True

    def __call__(self) -> float:
        if self._first:
            self._first = False
            return self.start
        return self.interval

class UniformDistribution:
    """
    Uniform distribution over [low, high].
    Params:
      low (float): lower bound
      high (float): upper bound
      rng (Generator, optional): a NumPy Generator instance
    """
    def __init__(self, low: float, high: float, rng: Optional[Generator] = None):
        self.low = low
        self.high = high
        self.rng = rng if rng is not None else default_rng()

    def __call__(self) -> float:
        return self.rng.uniform(self.low, self.high)

class ParetoDistribution:
    """
    Pareto (heavy-tailed) distribution.
    Params:
      alpha (float): shape parameter > 0
      scale (float): scale parameter x_m > 0
      rng (Generator, optional): a NumPy Generator instance
    """
    def __init__(self, alpha: float, scale: float = 1.0, rng: Optional[Generator] = None):
        self.alpha = alpha
        self.scale = scale
        self.rng = rng if rng is not None else default_rng()

    def __call__(self) -> float:
        # Classic Pareto on [x_m, ∞): scale * (1 + U^(−1/alpha) )? Use pareto()+1
        return self.scale * (self.rng.pareto(self.alpha) + 1)

class PoissonDistribution:
    """
    Poisson count distribution (number of events per unit time).
    Params:
      lam (float): expected number of events
      rng (Generator, optional): a NumPy Generator instance
    Note: Use for counts; if you need inter-arrival times use ExponentialDistribution.
    """
    def __init__(self, lam: float, rng: Optional[Generator] = None):
        self.lam = lam
        self.rng = rng if rng is not None else default_rng()

    def __call__(self) -> int:
        return int(self.rng.poisson(self.lam))

class NormalDistribution:
    """
    Gaussian distribution.
    Params:
      mu (float): mean
      sigma (float): standard deviation
      rng (Generator, optional): a NumPy Generator instance
    """
    def __init__(self, mu: float, sigma: float, rng: Optional[Generator] = None):
        self.mu = mu
        self.sigma = sigma
        self.rng = rng if rng is not None else default_rng()

    def __call__(self) -> float:
        return float(self.rng.normal(self.mu, self.sigma))

