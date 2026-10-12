from dataclasses import dataclass, field
from ppa_class import PPA


@dataclass
class PlantConfig:


    # Total Capacity Parameters


    # Total Hybrid Connectivity
    hybrid_connectivity_mw: float = 300

    # Wind Generation Parameters
    no_wind_turbines: int = 91
    turbine_capacity_mw: float = 3.3

    # Solar Generation Parameters
    ac_solar_capacity_mw: float = 175
    dc_ac_overload: float = 1.5

    # BESS and PCS Installed Hardware


    # Bi-Directional Invertors
    bi_d_inverter_size_mw: float = 2.5
    no_bi_d_inverters: int = 40

    # PCS and Battery Parameters
    battery_block_size_mwh: float = 6.25
    battery_blocks_per_pcs: int = 2


  
    # Battery Depth of Discharge

    max_soc_pct: float = 100
    min_soc_pct: float = 0

    
    # Efficiencies

    # FAT to SAT efficiency
    FAT_to_SAT_efficiency = 98

    # Percentage loss from Pooling Sub-Station ---> Grid Sub-Station
    pss_gss_loss_pct: float = 2

    # Charging and Discharging Efficiencies
    PoC_IDT_cable_loss_perc: float = 99.8
    IDT_loss: float = 98.95
    IDT_to_PCS: float = 99.8
    PCS_loss: float = 98.5
    DC_cable: float = 99.7

    # Round-Trip Efficiency
    DC_DC_RTE: float = 94
    BESS_discharge_efficiency: float = 96


    # Auxiliary Consumption Charging and Discharging Parameters


    BESS_aux_consumption_per_unit_kwh: float = 20
    PCS_aux_consumption_per_unit_kwh: float = 5
    IDT_aux_consumption_per_unit_kwh: float = 0.3

    Time_for_charging: float = 5


    # Default PPA Contracts
 
    ppas: list = field(default_factory=lambda: [
        PPA("Fake Company 1", 60000000, 53_118_000, 8, 3.67, "24H-RTC"),
        PPA("Fake Company 2", 24070000, 24_363_000, 5, 3.67, "24H-RTC"),
        PPA("Fake Company 3", 52060000, 52_254_000, 8, 3.67, "24H-RTC"),
        PPA("Fake Company 4", 55450000, 53_901_000, 8, 3.67, "24H-RTC"),
        PPA("Fake Company 5", 56637273, 50_973_546.387101404, 8, 3.58, "24H-RTC"),
        PPA("Fake Company 6", 388800000, 349_920_000, 68, 3.67, "24H-RTC"),
        PPA("Fake Company 7", 64500000, 58_050_000, 10, 3.65, "24H-RTC"),
        PPA("Fake Company 8", 31870000, 28_683_000, 5, 4.10, "24H-RTC"),
        PPA("Fake Company 9", 550000000, 495_000_000, 94.17808219178082, 3.70, "LimitedH-RTC"),
    ])


    # Path to Actual Generation Data
    spreadsheet_path: str = "Kudligi Spreadsheet.xlsx"
    exchange_price_column: str = "Average_Historical"

    # MPC Controller Paramaters

    # Total hours in the backtest year.
    n_hours: int = 8760

    # Length of the look-ahead window solved at every step, in hours
    horizon_hours: int = 48

    # How many steps into the look-ahead window we commit to before re-optimising
    control_interval_hours: int = 1

    # How many days we look back in the forecast
    forecast_lookback_days: int = 7

    # The penalty for falling short of the PPA
    ppa_shortfall_penalty_rs_per_kwh: float = 500.0

    solver_name: str = "appsi_highs"


    def __post_init__(self):


        # Total Connectivity Parameters in kW


        # Calculate Hybrid Connectivity in kW
        self.hybrid_conn_kw = self.hybrid_connectivity_mw * 1000

        # Calculate Wind Capacity in kW
        self.wind_capacity_kw = self.no_wind_turbines * self.turbine_capacity_mw * 1000

        # Calculate Solar AC/DC Capacity in kW
        self.ac_solar_capacity_kw = self.ac_solar_capacity_mw * 1000
        self.dc_solar_capacity_kw = self.ac_solar_capacity_kw * self.dc_ac_overload

        # PCS Cap in kW
        self.bess_pcs_cap_kw = self.bi_d_inverter_size_mw * self.no_bi_d_inverters * 1000

        # BESS Depth of Discharge
        self.BESS_DoD = (self.max_soc_pct - self.min_soc_pct) / 100

        # Calculate Installed BESS Capacity
        self.bess_installed_capacity_kwh = (
            self.battery_block_size_mwh * self.battery_blocks_per_pcs
            * self.no_bi_d_inverters * 1000
        )

        # Total Efficiency from Bus to Battery
        self.bus_to_battery_efficiency = ((self.PoC_IDT_cable_loss_perc / 100) * (self.IDT_loss / 100) *
                                            (self.IDT_to_PCS / 100) * (self.PCS_loss / 100) * (self.DC_cable / 100))

        self.FAT_to_SAT_efficiency_fac = self.FAT_to_SAT_efficiency / 100
        self.BESS_discharge_efficiency_fac = self.BESS_discharge_efficiency / 100


        # BESS Auxiliary Consumption Calculations


        # Circuit Efficiency
        self.aux_power_circuit_efficiency = ((self.PoC_IDT_cable_loss_perc / 100) * (self.IDT_loss / 100) * 
                                             (self.IDT_to_PCS / 100))

        # Total Units for Auxiliary Power to be used 
        self.BESS_total_units = self.no_bi_d_inverters * self.battery_blocks_per_pcs
        self.PCS_total_units = self.no_bi_d_inverters
        self.IDT_total_units = self.PCS_total_units / 2

        # Total Auxiliary Consumption in kWh
        self.BESS_total_aux_cons_kWh = self.BESS_aux_consumption_per_unit_kwh * self.BESS_total_units * self.Time_for_charging
        self.PCS_total_aux_cons_kWh = self.PCS_aux_consumption_per_unit_kwh * self.PCS_total_units * self.Time_for_charging
        self.IDT_total_aux_cons_kWh = self.IDT_aux_consumption_per_unit_kwh * self.IDT_total_units * self.Time_for_charging

        self.total_aux_consumption_charging = (self.BESS_total_aux_cons_kWh + self.PCS_total_aux_cons_kWh + self.IDT_total_aux_cons_kWh) / 1000

        # Total Auxiliary Power Needed
        self.aux_power_during_charge_discharge_kwh = self.total_aux_consumption_charging / self.aux_power_circuit_efficiency * 1000

        
        # Battery Maximum/Minimum State of Charge Calculations 


        self.battery_charge_efficiency = (self.DC_DC_RTE / self.BESS_discharge_efficiency)
        self.battery_usable_capacity_kwh = self.bess_installed_capacity_kwh * self.FAT_to_SAT_efficiency_fac * self.BESS_DoD
        self.charging_energy_required_at_poc = self.battery_usable_capacity_kwh / (self.bus_to_battery_efficiency * self.battery_charge_efficiency)

        self.bess_charging_capacity_kwh = (self.charging_energy_required_at_poc + self.aux_power_during_charge_discharge_kwh)

        self.max_soc_kwh = self.bess_charging_capacity_kwh * self.rte_ch * self.max_soc_pct / 100
        self.min_soc_kwh = self.bess_charging_capacity_kwh * self.rte_ch * self.min_soc_pct / 100

        #  Energy Delivered at the PoC 
        self.energy_delivered_at_poc_no_aux = self.battery_usable_capacity_kwh * self.BESS_discharge_efficiency_fac * self.bus_to_battery_efficiency
        self.energy_delivered_at_poc = self.energy_delivered_at_poc_no_aux - self.aux_power_during_charge_discharge_kwh

        # Total Round-Trip Efficiency split equally between charging and discharging
        self.rte = (self.energy_delivered_at_poc / self.bess_charging_capacity_kwh)
        self.rte_ch = self.rte ** 0.5
        self.rte_disch = self.rte ** 0.5

        # PSS to GSS Energy Loss
        self.pss_gss_factor = (100 - self.pss_gss_loss_pct) / 100

if __name__ == "__main__":

    config = PlantConfig()

    print("RTE: " + str(config.rte))
    print(config.battery_usable_capacity_kwh)
    print(config.charging_energy_required_at_poc)
    print(config.aux_power_during_charge_discharge_kwh)
    print("Max SoC: " + str(config.bess_charging_capacity_kwh))
    print(config.energy_delivered_at_poc)

    

