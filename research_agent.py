from dataclasses import dataclass
from typing import Dict, List, Optional
import yaml
from backtester import Backtester
from strategy import Strategy

@dataclass
class ResearchResult:
    best_config: Dict
    backtest_report: Dict
    trials: List[Dict]

class ResearchAgent:
    def __init__(self, data_connector, backtester: Backtester, strategy_factory):
        self.data_connector = data_connector
        self.backtester = backtester
        self.strategy_factory = strategy_factory

    def run_research(self, hypothesis, symbol, lookback_days, target_metric="sharpe"):
        # 1. Parse hypothesis (simple keyword-based for now; later use LLM)
        strategy_type = self._infer_strategy_type(hypothesis)
        param_space = self._get_param_space(strategy_type)

        # 2. Run hyperparameter search (e.g., grid search or Optuna)
        trials = []
        best_metric = -float("inf")
        best_config = None

        for params in self._generate_params(param_space):
            config = {
                "symbol": symbol,
                "strategy": strategy_type,
                "params": params
            }
            report = self.backtester.run_single_strategy(config, lookback_days)
            metric = report["metrics"][target_metric]

            trials.append({"config": config, "metric": metric, "report": report})
            if metric > best_metric:
                best_metric = metric
                best_config = config

        return ResearchResult(best_config=best_config, backtest_report=trials[-1]["report"], trials=trials)

    def _infer_strategy_type(self, hypothesis: str) -> str:
        # Simple rule-based; replace with LLM later
        hypothesis = hypothesis.lower()
        if "mean" in hypothesis and "reversion" in hypothesis:
            return "rsi_reversal"
        elif "trend" in hypothesis and "breakout" in hypothesis:
            return "ema_crossover"
        elif "smc" in hypothesis or "order block" in hypothesis:
            return "composite_smc"
        else:
            return "ema_crossover"  # fallback

    def _get_param_space(self, strategy_type: str) -> Dict:
        # Define hyperparameter spaces per strategy
        spaces = {
            "ema_crossover": {
                "fast_ema": [5, 9, 12],
                "slow_ema": [21, 26, 50]
            },
            "rsi_reversal": {
                "oversold": [25, 30, 35],
                "overbought": [65, 70, 75]
            },
            "composite_smc": {
                "min_confirmations": [2, 3],
                "trend_filter": [True, False]
            }
        }
        return spaces.get(strategy_type, {})

    def _generate_params(self, param_space: Dict):
        # Simple grid product; replace with Optuna if needed
        from itertools import product
        keys = list(param_space.keys())
        values = [param_space[k] for k in keys]
        for combo in product(*values):
            yield dict(zip(keys, combo))