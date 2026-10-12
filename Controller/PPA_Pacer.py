from dataclasses import dataclass
from ppa_class import PPA
from Forecaster import BuildForecast

class PPAState:
    def __init__(self,
                 company_name,
                 min_contracted_energy_kwh,
                 discharge_period,
                 tariff_rs_per_kwh,
                 delivered_so_far_kwh = 0):
        
        self.company_name = company_name
        self.min_contracted_energy_kwh = min_contracted_energy_kwh   # annual guaranteed minimum (from the PPA contract)
        self.discharge_period = discharge_period            # "24H-RTC" or "LimitedH-RTC"
        self.tariff_rs_per_kwh = tariff_rs_per_kwh
        self.delivered_so_far_kwh = delivered_so_far_kwh  # running total of ACTUAL GSS energy delivered this year

class PPA_Pacer:

    def __init__(self, config):

        self.config = config
        self.ppas = config.ppas

    # Begin a book to keep track of the progress on PPAs resolved annually
    def init_ppa_states(self):

        self.ppas_dict = {
            ppa.company_name: PPAState(
                company_name=ppa.company_name,
                min_contracted_energy_kwh=ppa.min_contracted_energy,
                discharge_period=ppa.discharge_period,
                tariff_rs_per_kwh=ppa.tariff
            )
            for ppa in self.ppas
        }
        return self.ppas_dict

    # Find the target energy that needs to have been supplied
    def pace_target_to_date(self, state, hour_of_year, n_hours):

        fraction_of_year_elapsed = (hour_of_year + 1) / n_hours
        return state.min_contracted_energy_kwh * fraction_of_year_elapsed

    # Find the requirement for the specific window
    def window_requirement(self, state, window_end_t, n_hours):

        target = self.pace_target_to_date(state, window_end_t, n_hours)
        shortfall_vs_flat_schedule = target - state.delivered_so_far_kwh
        return max(0.0, shortfall_vs_flat_schedule)

    # Update how much has been delivered
    def update_delivered(self, state: PPAState, delivered_this_hour_kwh: float) -> None:

        state.delivered_so_far_kwh += delivered_this_hour_kwh

    # Show the pacing status
    def pacing_status(self, state: PPAState, hour_of_year: int, n_hours: int) -> dict:

        target = self.pace_target_to_date(state, hour_of_year, n_hours)
        return {
            "company_name": state.company_name,
            "delivered_so_far_kwh": state.delivered_so_far_kwh,
            "pace_target_kwh": target,
            "ahead_by_kwh": state.delivered_so_far_kwh - target,
            "annual_minimum_kwh": state.min_contracted_energy_kwh,
        }

# Build the dictionary of where all the PPAs are upto
    def build_pacing_book(self, window_end):
    
        self.pacing_book = {name: self.window_requirement(states, window_end, self.config.n_hours) 
                            for name, states in self.ppas_dict.items()}
        return self.pacing_book


if __name__ == "__main__":
    # Manual sanity check on the arithmetic, using the actual PPA book.
    from Config import PlantConfig

    cfg = PlantConfig()

    PPA_pacer = PPA_Pacer(cfg)
    states = PPA_pacer.init_ppa_states()

    ppa_name = "Linde India Ltd (Orissa)"  # the largest single PPA in the book
    s = states[ppa_name]

    print(f"{ppa_name}: annual minimum = {s.min_contracted_energy_kwh/1e6:,.2f} GWh")
    print(f"Pace target at hour 0 (first hour of year): "
        f"{PPA_pacer.pace_target_to_date(s, 0, cfg.n_hours)/1e3:,.1f} kWh")
    print(f"Pace target at hour 4379 (halfway through year): "
        f"{PPA_pacer.pace_target_to_date(s, 4379, cfg.n_hours)/1e6:,.2f} GWh "
        f"(expect ~half of annual minimum)")
    print(f"Pace target at hour 8759 (last hour of year): "
        f"{PPA_pacer.pace_target_to_date(s, 8759, cfg.n_hours)/1e6:,.2f} GWh "
        f"(expect == annual minimum)")

    # Simulate having delivered nothing yet and check the window
    # requirement for the very first 48-hour window.
    req = PPA_pacer.window_requirement(s, window_end_t=47, n_hours=cfg.n_hours)
    print(f"\nWindow requirement for hours [0,47] with zero delivered so far: "
        f"{req/1e3:,.1f} kWh")
