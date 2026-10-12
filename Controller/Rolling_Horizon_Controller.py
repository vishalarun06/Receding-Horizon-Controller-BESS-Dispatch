import time
import pandas as pd


from Config import PlantConfig
from Data_Loader import DataLoader
from Forecaster import BuildForecast
from PPA_Pacer import PPA_Pacer
from MPC_Window import KudligiMPCWindow


class RollingHorizonController:
    def __init__(self,
                 config: PlantConfig,
                 data_loader: DataLoader,
                 n_hours=None,
                 print_every=168):

        self.config = config
        self.data_loader = data_loader
        self.data = self.data_loader.load_ground_truth()
        self.n_hours = n_hours
        self.print_every = print_every
    
    def run(self):

        n_hours = self.n_hours or self.config.n_hours


        soc_kwh = self.config.min_soc_kwh          # battery starting soc configured in config.py, same as the perfect-foresight model

        # Initialise the PPA tracking book
        ppa_pacing = PPA_Pacer(self.config)
        ppa_states = ppa_pacing.init_ppa_states()

        rows = []
        t0_wall = time.time()

        for t in range(n_hours):

            # Build Forecast
            forecast = BuildForecast(self.config, self.data, t)
            window = forecast.build()
            window_end_t = int(window["t"].iloc[-1])

            # Calculate energy needed for PPAs
            requirements = ppa_pacing.build_pacing_book(window_end_t)

            # Solve to find optimal 48-hour charging schedule.
            model = KudligiMPCWindow(
                config=self.config,
                window_df=window,
                init_soc_kwh=soc_kwh,
                ppa_window_requirements=requirements,
            )
            model.build_and_solve()

            # Only use the current decision for hour zero
            decision = model.extract_hour_zero_decision()
            rows.append(decision)

            # Update all the ppas and soc
            soc_kwh = decision["BESS_SoC"]
            for name, gss_delivered in decision["PPA_GSS"].items():
                ppa_pacing.update_delivered(ppa_states[name], gss_delivered)

            # Print the progress
            if self.print_every and (t % self.print_every == 0 or t == n_hours - 1):
                elapsed = time.time() - t0_wall
                rate = (t + 1) / elapsed if elapsed > 0 else float("nan")
                eta_min = (n_hours - t - 1) / rate / 60 if rate > 0 else float("nan")
                print(
                    f"  hour {t:5d}/{n_hours} | SoC {soc_kwh/1000:7.1f} MWh | "
                    f"{elapsed:6.1f}s elapsed | {rate:5.1f} hrs/s | "
                    f"ETA {eta_min:5.1f} min"
                )

        hourly_results = pd.DataFrame(rows)
        return hourly_results, ppa_states


if __name__ == "__main__":

    from Data_Loader import DataLoader

    cfg = PlantConfig()
    gt = DataLoader(cfg)

    TEST_HOURS = 96  # two weeks
    print(f"Running rolling MPC smoke test for the first {TEST_HOURS} hours...")
    rhc = RollingHorizonController(cfg, gt, n_hours=TEST_HOURS, print_every=1)
    results, final_states = rhc.run()
    

    print("\nFirst few committed hours:")
    print(results[["t_abs", "WindGen", "SolarGen1", "SolarGen2", "ExchangePrice",
                    "BESS_Charge", "BESS_Discharge", "BESS_SoC", "Curtailed"]].head(10).to_string(index=False))

    print("\nPacing status at end of smoke test (expect small numbers -- only 2 weeks in):")
    ppa_pacing = PPA_Pacer(cfg)
    for name, state in final_states.items():
        status = ppa_pacing.pacing_status(state, TEST_HOURS - 1, cfg.n_hours)
        print(f"  {name:35s} delivered {status['delivered_so_far_kwh']/1e6:6.3f} GWh "
              f"vs pace target {status['pace_target_kwh']/1e6:6.3f} GWh "
              f"(ahead by {status['ahead_by_kwh']/1e3:8.1f} MWh)")
