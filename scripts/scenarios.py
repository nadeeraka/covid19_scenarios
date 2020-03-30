import csv
import os
import json
import numpy as np
from datetime import datetime
from scipy.stats import linregress
import sys
sys.path.append('..')
from paths import TMP_CASES, BASE_PATH, JSON_DIR
from scripts.tsv import parse as parse_tsv
from scripts.model import fit_population

# ------------------------------------------------------------------------
# Globals

SCENARIO_POPS = os.path.join(BASE_PATH, "populationData.tsv")
FIT_CASE_DATA = {}

# ------------------------------------------------------------------------
# Data fitter

ms_in_day = 1000*60*60*24

class Fitter:
    doubling_time   = 3.0
    serial_interval = 7.5
    fixed_slope     = np.log(2)/doubling_time
    cases_on_tMin   = 10
    under_reporting = 5
    delay           = 18
    fatality_rate   = 0.02

    def slope_to_r0(self, slope):
        return 1 + slope*self.serial_interval

    def fit(self, pop):
        # ----------------------------------
        # Internal functions

        def fit_cumulative(t, y):
            good_ind    = (y > 3) & (y < 500)
            t_subset    = t[good_ind]
            logy_subset = np.log(y[good_ind])
            num_data    = good_ind.sum()

            if num_data > 10:
                res = linregress(t_subset, logy_subset)
                return {"intercept" : res.intercept,
                        "slope"     : res.slope,
                        'rvalue'    : res.rvalue}
            elif num_data > 4:
                intercept = logy_subset.mean() - t_subset.mean()*self.fixed_slope
                return {"intercept" : intercept,
                        "slope"     : 1.0*self.fixed_slope,
                        'rvalue'    : np.nan}
            else:
                return None

        def to_ms(time):
            return datetime.strptime(time[:10], "%Y-%m-%d").toordinal()

        def from_ms(time):
            d = datetime.fromordinal(int(time))
            return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"

        # ----------------------------------
        # Body

        data = np.array([ ([to_ms(dp['time']), dp['cases'] or np.nan, dp['deaths'] or np.nan]) for dp in pop ])

        # Try to fit on death
        p = fit_cumulative(data[:,0], data[:,2])
        if p and p["slope"] > 0:
            tMin = (np.log(self.cases_on_tMin * self.fatality_rate) - p["intercept"]) / p["slope"] - self.delay
            return {'tMin': from_ms(tMin), 'initialCases': self.cases_on_tMin, 'r0':self.slope_to_r0(p["slope"])}
        else: # If no death, fit on case counts
            p = fit_cumulative(data[:,0], data[:,1])
            if p and p["slope"] > 0:
                tMin = (np.log(self.cases_on_tMin)/self.under_reporting - p["intercept"]) / p["slope"]
                return {'tMin': from_ms(tMin), 'initialCases': self.cases_on_tMin, 'r0':self.slope_to_r0(p["slope"])}

        return None

# ------------------------------------------------------------------------
# Parameter classes
#
# IMPORTANT: Keep in sync with algorithm parameters of input [AllParamsFlat]
#            covid19_scenarios/src/algorithm/types/Param.types.ts

class Object:
    def marshalJSON(self):
        return json.dumps(self, default=lambda x: x.__dict__, sort_keys=True, indent=4)

class PopulationParams(Object):
    def __init__(self, region, country, population, beds, icus):
        self.populationServed    = int(population)
        self.country             = country
        self.suspectedCasesToday = Fitter.cases_on_tMin
        self.importsPerDay       = round(max(3e-4 * float(population)**0.5, .1),1)
        self.hospitalBeds        = int(beds)
        self.ICUBeds             = int(icus)
        self.cases               = region

class EpidemiologicalParams(Object):
    def __init__(self, region, hemisphere):
        self.latencyTime     = 5
        self.infectiousPeriod   = 3
        self.lengthHospitalStay = 4
        self.lengthICUStay      = 14
        if hemisphere:
            if hemisphere == 'Northern':
                self.seasonalForcing    = 0.2
                self.peakMonth          = 0
            elif hemisphere == 'Southern':
                self.seasonalForcing    = 0.2
                self.peakMonth          = 6
            elif hemisphere == 'Tropical':
                self.seasonalForcing    = 0
                self.peakMonth          = 6
            else:
                print(f'Error: Could not parse hemisphere for {region} in scenarios.py')
        else:
            self.seasonalForcing    = 0.2
            self.peakMonth          = 0
        self.overflowSeverity   = 2
        if region in FIT_CASE_DATA:
            self.r0 = round(FIT_CASE_DATA[region]['r0'],1)
        else:
            self.r0 = 2.7

class ContainmentParams(Object):
    def __init__(self):
        self.reduction    = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        # self.reduction    = [1.0, 0.8, 0.7, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6]
        self.numberPoints = len(self.reduction)

class DateRange(Object):
    def __init__(self, tMin, tMax):
        self.tMin = tMin
        self.tMax = tMax

class SimulationParams(Object):
    def __init__(self, region):
        tMin = FIT_CASE_DATA[region]['tMin'] if region in FIT_CASE_DATA else "2020-03-01"
        tMax = "2020-09-01"
        self.simulationTimeRange  = DateRange(tMin, tMax)
        self.numberStochasticRuns = 0

# TODO: Region and country provide redudant information
#       Condense the information into one field.
class AllParams(Object):
    def __init__(self, region, country, population, beds, icus, hemisphere):
        self.population      = PopulationParams(region, country, population, beds, icus)
        self.epidemiological = EpidemiologicalParams(region, hemisphere)
        self.simulation      = SimulationParams(region)
        self.containment     = ContainmentParams()

# ------------------------------------------------------------------------
# Functions

def marshalJSON(obj, wtr):
    return json.dump(obj, wtr, default=lambda x: x.__dict__, sort_keys=True, indent=4)

def fit_all_case_data():
    # Params = Fitter()
    def unpack(fit):
        return {"tMin": fit['tMin'], "r0": fit['params'].rates.R0, "initialCases": fit["initialCases"]}

    case_counts = parse_tsv()
    for region, data in case_counts.items():
        # fit = Params.fit(data)
        # if fit:
        print(f"Fitting '{region}'")
        FIT_CASE_DATA[region] = unpack(fit_population(region))

# ------------------------------------------------------------------------
# Main point of entry

def generate(OUTPUT_JSON):
    scenario = {}
    fit_all_case_data()

    with open(SCENARIO_POPS, 'r') as fd:
        rdr = csv.reader(fd, delimiter='\t')
        hdr = next(rdr)
        idx = {'name' : hdr.index('name'),
               'size' : hdr.index('populationServed'),
               'ages' : hdr.index('ageDistribution'),
               'beds' : hdr.index('hospitalBeds'),
               'icus' : hdr.index('ICUBeds'),
               'hemisphere' : hdr.index('hemisphere')}

        args = ['name', 'ages', 'size', 'beds', 'icus', 'hemisphere']
        for region in rdr:
            entry = [region[idx[arg]] for arg in args]
            scenario[region[idx['name']]] = AllParams(*entry)

    with open(OUTPUT_JSON, "w+") as fd:
        marshalJSON(scenario, fd)

if __name__ == '__main__':
    generate()
