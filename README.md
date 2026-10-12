# Receding-Horizon BESS Dispatch Controller

A model-predictive control (MPC) backtest for dispatching a battery on a hybrid wind-solar plant that sells into fixed-tariff PPAs and a spot exchange. Every hour the controller solves a 48-hour linear programme, commits only the first hour, updates its state, and re-solves. A whole-year perfect-foresight solve provides an upper bound on achievable revenue.

Built with Python, [Pyomo](https://www.pyomo.org/) and [HiGHS](https://highs.dev/).

## How it works

```
        ┌──────────────┐   history only   ┌──────────────┐
 Excel ─►  DataLoader  ├─────────────────►  Forecaster   │
        └──────────────┘                  └──────┬───────┘
                                                 │ 48h forecast window
 ┌───────────┐  pacing requirements       ┌──────▼───────┐
 │ PPA_Pacer ├────────────────────────────►  MPC window   │  Pyomo LP, HiGHS
 └─────▲─────┘                            └──────┬───────┘
       │ delivered energy, SoC                   │ hour-0 decision
       └─────────────────────────────────────────┘
                  RollingHorizonController
```

1. **Forecast.** A persistence forecast built only from data available at decision time: each future hour takes the value from the same hour 7 days earlier, falling back to 24 hours earlier, then to the current observation. The current hour is observed.
2. **Pacing.** For each PPA, the requirement for the window is the pro-rata annual minimum up to the end of the window, minus energy already delivered (floored at zero).
3. **Optimise.** Maximise revenue from PPA tariffs and exchange sales, minus a penalty on any pacing shortfall.
4. **Commit and roll.** Apply hour 0 only, update the battery state of charge and each PPA's delivered energy, then move forward one hour.

## Model constraints

Each window LP enforces:

- **Energy balance.** Wind, solar and battery discharge must equal PPA injections, exchange sales, battery charging and curtailment. No energy is created.
- **Battery dynamics.** State of charge evolves with charge and discharge energy, with round-trip losses split equally between charging and discharging, and stays within its minimum and maximum.
- **Converter limits.** Charging and discharging power are each capped by the installed inverter (PCS) capacity.
- **Grid connectivity.** Total injection across all PPAs and the exchange cannot exceed the plant's connectivity limit.
- **PPA delivery.** Each contract has a maximum hourly quantum, and limited-hours contracts can only be served inside their delivery window.
- **Transmission loss.** Energy injected at the pooling substation is reduced by a fixed loss factor before it is counted as delivered or sold.
- **Pacing.** Delivered energy plus a shortfall slack must meet each PPA's requirement for the window. The slack is penalised heavily in the objective, so pacing is effectively a hard constraint that can never make the problem infeasible.
- **Terminal state of charge.** The battery must end the window at least as full as it started, so the plan cannot empty it just because the horizon ends.

The round-trip efficiency is derived in `Config.py` from the efficiency chain (cabling, transformer, inverter, DC-DC and discharge efficiency, FAT-to-SAT factor) including auxiliary consumption.

## Benchmark

`RunAnalytics` also solves the same problem once over the whole year with perfect information and no terminal-state-of-charge constraint, then reports the gap between that ceiling and the rolling-horizon revenue. Because the MPC sees less information, its revenue cannot exceed the ceiling, and the run asserts this.

The gap bundles three effects: forecast error, the finite 48-hour horizon, and the pacing heuristic. It should not be read as the cost of forecast error alone.

## Limitations

These are modelling simplifications that affect how the results should be read. Several are natural extensions.

- **Simplified contract model.** The 24H-RTC quantum is treated as a maximum hourly delivery, not a firm obligation, so a contract can be served at zero in some hours as long as its annual energy is met. Real round-the-clock contracts usually require a flat or near-flat profile. The model also does not cap delivery at each contract's annual contracted energy, so energy above it can still earn tariff. Together these tend to overstate PPA revenue.
- **Naive forecast.** Persistence forecasting uses the same hour a week or a day earlier and ignores weather information. It is a weak baseline, so the controller's revenue is a conservative estimate of what a forecast-driven MPC could achieve, and the gap to the perfect-foresight ceiling is correspondingly larger.
- **Deterministic treatment of uncertainty.** Within each window the controller treats the forecast as exact. It carries no safety margin and no recourse for forecast error, so it can over-commit to energy that does not materialise. Robust and scenario-based formulations would address this.
- **Smoothed price signal.** The default exchange price is a historical average rather than realised hourly prices. Averaging removes price spikes, which are where much of the arbitrage value of storage lies, so battery value is likely understated and the results say little about spike capture.
- **Pacing heuristic.** Requirements follow a straight-line schedule through the year, ignoring that wind and solar output are seasonal. A flat schedule can force costly dispatch in low-resource periods and does not reflect when delivery is actually cheapest. The large shortfall penalty also makes pacing dominate the objective whenever the two conflict.
- **Finite horizon and terminal condition.** Requiring the battery to end each 48-hour window at least as full as it started stops it being emptied at the end, but it also gives no value to energy carried beyond the window and can bias the plan towards holding charge. The gap to the ceiling therefore includes this effect.
- **No degradation or cycling cost.** The battery can cycle freely within converter and state-of-charge limits. Real warranties limit throughput and cycling wears the asset, so the model likely overstates the profitable amount of cycling.
- **Single market and no balancing.** Revenue comes from PPAs and one exchange price only. There is no day-ahead versus real-time distinction, no ancillary or balancing services, and no imbalance charges.
- **Relaxed charge-discharge exclusivity.** Simultaneous charging and discharging is not explicitly forbidden, since that would need binary variables. It is harmless while round-trip efficiency is below one and prices are positive, but it could appear if prices were negative.
- **Illustrative parameters.** Plant and contract values in `Config.py` are illustrative, so absolute revenue figures should not be read as results for any real asset.

## Repository structure

| File | Role |
|---|---|
| `Config.py` | Plant, efficiency, PPA and controller parameters (`PlantConfig` dataclass) |
| `ppa_class.py` | `PPA` contract definition |
| `Data_Loader.py` | Reads generation and exchange-price data from Excel |
| `Forecaster.py` | Builds the causal persistence forecast window |
| `PPA_Pacer.py` | Tracks annual delivery against the pro-rata schedule |
| `MPC_Window.py` | Pyomo LP for one window, built once and updated each hour |
| `Rolling_Horizon_Controller.py` | The receding-horizon loop |
| `RunAnalytics.py` | Entry point: backtest, perfect-foresight ceiling, Excel export |

## Installation

Requires Python 3.10+.

```bash
git clone https://github.com/vishalarun06/Receding-Horizon-Controller-BESS-Dispatch.git
cd Receding-Horizon-Controller-BESS-Dispatch
pip install pandas numpy openpyxl pyomo highspy
```

### Input data

The repository does not include plant data. Provide an Excel workbook at the path set in `PlantConfig.spreadsheet_path` with:

- Sheet `Wind-Solar+BESS` (header on row 3): columns `Month`, `Day`, `Hours` (hour of day), `Wind Generation`, `Solar Generation`, `Solar Generation2`, in kWh per hour, 8,760 rows.
- Sheet `Exchange` (header on row 2): a price column named by `PlantConfig.exchange_price_column`, in Rs/kWh, 8,760 rows.

### Running

```bash
# Short smoke test (first 96 hours, no ceiling)
python RunAnalytics.py --hours 96 --skip-ceiling

# Full-year backtest plus perfect-foresight comparison
python RunAnalytics.py --output dispatch_results.xlsx
```

Outputs an Excel workbook with an hourly dispatch sheet and a per-PPA annual summary, and prints revenue, curtailment, pacing status and the MPC-versus-ceiling gap.

## Author

Vishal Arun, MSci Mathematics, UCL.
