class PPA:

    def __init__(self,
            company_name,
            Annual_Contracted,
            Annual_Guaranteed,
            RTC_Quantum,
            tariff,
            discharge_period):

        self.company_name = company_name
        self.RTC_Quantum = RTC_Quantum
        self.actual_contracted_energy = Annual_Contracted
        self.min_contracted_energy = Annual_Guaranteed
        self.tariff = tariff
        self.discharge_period = discharge_period


        
