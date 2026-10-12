import argparse
import time

import pandas as pd

from Config import PlantConfig
from Data_Loader import DataLoader
from Forecaster import FORECAST_COLUMNS, CALENDAR_COLUMNS
from PPA_Pacer import PPA_Pacer
from MPC_Window import KudligiMPCWindow
from Rolling_Horizon_Controller import RollingHorizonController


class RunAnalytics:
    def __init__(self,
                 config,
                 data_loader,):

        self.config = config
        self.data_loader = data_loader
        self.ground_truth = self.data_loader.load_ground_truth()

    # This builds the dataframe that goes to the spreadsheet showing where energy generated goes each hour
    def compute_hourly_revenue(self, results: pd.DataFrame) -> pd.DataFrame:

        results = results.copy()
        for ppa in self.config.ppas:
            results[f"Revenue__{ppa.company_name}"] = (
                results["PPA_GSS"].apply(lambda d, name=ppa.company_name: d[name]) * ppa.tariff
            )
        results["Revenue__Exchange"] = results["Exchange_GSS"] * results["ExchangePrice"]

        revenue_cols = [c for c in results.columns if c.startswith("Revenue__")]
        results["Revenue__Total"] = results[revenue_cols].sum(axis=1)
        return results


    # This runs the optimal solution for BESS dispatch across the whole year to benchmark
    def run_perfect_foresight_ceiling(self, tee: bool = False) -> dict:

        # A "window" that is actually the entire year to get max possible revenue
        full_window = self.ground_truth[["t"] + CALENDAR_COLUMNS + FORECAST_COLUMNS].copy()
        full_window["offset"] = full_window["t"]
        full_window["is_forecast"] = False

        ppa_pacing = PPA_Pacer(self.config)
        # Each PPA's full annual minimum, evaluated with nothing yet delivered
        ppa_states = ppa_pacing.init_ppa_states()
        requirements = ppa_pacing.build_pacing_book(self.config.n_hours - 1)

        # Build and solve the model
        model = KudligiMPCWindow(
            config=self.config,
            window_df=full_window,
            init_soc_kwh=self.config.min_soc_kwh,
            ppa_window_requirements=requirements,
            enforce_terminal_soc=False,
        )
        model.build_and_solve(tee=tee)

        mo = model.model
        ppa_revenue = {}
        for ppa in self.config.ppas:
            if ppa.company_name in mo.PPA_24H:
                ppa_revenue[ppa.company_name] = self.pyo_value(mo.PPA_24H_Revenue[ppa.company_name])
            else:
                ppa_revenue[ppa.company_name] = self.pyo_value(mo.PPA_LimitedH_Revenue[ppa.company_name])
        exchange_revenue = sum(self.pyo_value(mo.Exchange_Revenue[t]) for t in mo.T)
        total_shortfall_kwh = sum(self.pyo_value(mo.PPA_Shortfall[p]) for p in ppa_revenue)

        return {
            "total_revenue_rs": sum(ppa_revenue.values()) + exchange_revenue,
            "ppa_revenue_rs": ppa_revenue,
            "exchange_revenue_rs": exchange_revenue,
            "total_shortfall_kwh": total_shortfall_kwh,  # should be ~0: full year should hit every minimum
        }

    # Function just returns a pyomo variables value
    def pyo_value(self, x):
        """Tiny local alias so this file doesn't need `import pyomo.environ as pyo`
        just for `pyo.value(...)` calls in two places above."""
        import pyomo.environ as pyo
        return pyo.value(x)

    # Exports results to an excel spreadsheet
    def export_to_excel(self, hourly_results: pd.DataFrame, ppa_states: dict, path: str):


        flat = hourly_results.copy()
        for dict_col in ["PPA_Inject", "PPA_GSS", "PPA_Shortfall_this_window"]:
            expanded = flat[dict_col].apply(pd.Series)
            expanded.columns = [f"{dict_col}__{c}" for c in expanded.columns]
            flat = pd.concat([flat.drop(columns=[dict_col]), expanded], axis=1)

        ppa_summary_rows = []
        for ppa in self.config.ppas:
            state = ppa_states[ppa.company_name]
            delivered = state.delivered_so_far_kwh
            ppa_summary_rows.append({
                "Company": ppa.company_name,
                "Discharge Period": ppa.discharge_period,
                "Tariff (Rs/kWh)": ppa.tariff,
                "Annual Minimum (kWh)": ppa.min_contracted_energy,
                "Delivered by MPC (kWh)": delivered,
                "Shortfall vs Minimum (kWh)": max(0.0, ppa.min_contracted_energy - delivered),
                "Minimum Met": delivered >= ppa.min_contracted_energy - 1e-6,
                "Revenue (Rs)": delivered * ppa.tariff,
            })
        ppa_summary = pd.DataFrame(ppa_summary_rows)

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            flat.to_excel(writer, sheet_name="MPC Hourly Dispatch", index=False)
            ppa_summary.to_excel(writer, sheet_name="PPA Annual Summary", index=False)

    # This runs the main solver
    def main(self):
        parser = argparse.ArgumentParser(description="Run the Kudligi rolling-horizon MPC backtest.")
        parser.add_argument("--hours", type=int, default=None,
                            help="Number of hours to simulate (default: full year, 8760).")
        parser.add_argument("--skip-ceiling", action="store_true",
                            help="Skip the perfect-foresight ceiling calculation (faster).")
        parser.add_argument("--output", type=str, default="Kudligi_MPC_Dispatch.xlsx")
        args = parser.parse_args()

        cfg = PlantConfig()
        print(f"Loading ground-truth data from '{cfg.spreadsheet_path}' ...")

        data_loader = DataLoader(cfg)

        n_hours = args.hours or cfg.n_hours
        print(f"\n=== Running rolling MPC: {n_hours} hourly re-solves, "
            f"{cfg.horizon_hours}h look-ahead each ===")
        t0 = time.time()

        RollingHorizon = RollingHorizonController(cfg, data_loader, n_hours)
        hourly_results, ppa_states = RollingHorizon.run()
        mpc_wall_time = time.time() - t0
        print(f"Rolling MPC finished in {mpc_wall_time/60:.1f} minutes.")

        hourly_results = self.compute_hourly_revenue(hourly_results)
        mpc_total_revenue = hourly_results["Revenue__Total"].sum()

        print(f"\n=== MPC RESULTS (first {n_hours} hours) ===")
        print(f"Total revenue: Rs {mpc_total_revenue/1e7:,.2f} Cr "
            f"(Rs {mpc_total_revenue/1e6:,.2f} Mn)")
        print(f"Total curtailed energy: {hourly_results['Curtailed'].sum()/1e6:,.3f} GWh")

        print("\nPPA pacing status at end of run:")

        ppa_pacer = PPA_Pacer(cfg)
        last_hour_simulated = int(hourly_results["t_abs"].iloc[-1])
        for name, state in ppa_states.items():
            status = ppa_pacer.pacing_status(state, last_hour_simulated, cfg.n_hours)
            flag = "OK" if status["ahead_by_kwh"] >= -1e-6 else "BEHIND"
            print(f"  [{flag:6s}] {name:35s} delivered {status['delivered_so_far_kwh']/1e6:7.3f} GWh "
                f"vs pace target {status['pace_target_kwh']/1e6:7.3f} GWh")

        self.export_to_excel(hourly_results, ppa_states, args.output)
        print(f"\nSaved: {args.output}")

        if not args.skip_ceiling and n_hours == cfg.n_hours:
            print(f"\n=== Solving perfect-foresight ceiling for comparison "
                f"(one-shot, whole-year, full information) ===")
            t0 = time.time()
            ceiling = self.run_perfect_foresight_ceiling()
            ceiling_wall_time = time.time() - t0
            print(f"Ceiling solve finished in {ceiling_wall_time:.1f}s.")
            print(f"Ceiling total revenue: Rs {ceiling['total_revenue_rs']/1e7:,.2f} Cr")
            print(f"Ceiling PPA shortfall (should be ~0): "
                f"{ceiling['total_shortfall_kwh']:,.1f} kWh")

            gap = ceiling["total_revenue_rs"] - mpc_total_revenue
            gap_pct = 100 * gap / ceiling["total_revenue_rs"]
            print(f"\n=== MPC vs CEILING ===")
            print(f"Ceiling (perfect foresight): Rs {ceiling['total_revenue_rs']/1e7:,.2f} Cr")
            print(f"MPC (48h rolling, no lookahead): Rs {mpc_total_revenue/1e7:,.2f} Cr")
            print(f"Gap (cost of imperfect foresight): Rs {gap/1e7:,.2f} Cr ({gap_pct:.2f}%)")

            # THE key correctness check for the whole project: if this ever
            # fails, the MPC is somehow seeing information it shouldn't --
            # investigate forecasting.py's causality assertions first.
            assert mpc_total_revenue <= ceiling["total_revenue_rs"] + 1.0, (
                "MPC revenue exceeds the perfect-foresight ceiling -- this "
                "should be IMPOSSIBLE and indicates a lookahead bug somewhere "
                "in the pipeline."
            )
            print("\n[OK] MPC revenue is <= the perfect-foresight ceiling, as it must be.")
        elif n_hours != cfg.n_hours:
            print("\n(Skipping ceiling comparison: it's only a fair year-vs-year "
                "comparison when the MPC run also covers the full year. "
                "Run without --hours to get the full comparison.)")


if __name__ == "__main__":

    cfg = PlantConfig()
    data_loader = DataLoader(cfg)
    analytics = RunAnalytics(cfg, data_loader)
    analytics.main()
