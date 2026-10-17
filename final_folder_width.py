"""Final-unit, expansion-only folder width with an exactly protected median pair."""
from __future__ import annotations
import copy
import math
from statistics import median
from typing import Any, Mapping, Sequence
import numpy as np

POLICY={"version":1,"method":"final-side-q90-median-pair-width-floor-v1",
    "targetDistance":.4,"quantile":.9,"asinhScale":.75,"minimumCharts":4,
    "minimumPositiveSpread":1e-8,"expansionOnly":True,"compressionApplications":1,
    "intervals":"conditional-fixed-width-location-and-selection-no-new-evidence"}


def width_parameters(points: Sequence[Mapping[str,Any]], level:int) -> dict:
    usable=[p for p in points if isinstance(p.get('value'),(int,float)) and math.isfinite(p['value'])
            and not isinstance(p['value'],bool) and not p.get('capDefault',False)]
    values=sorted(float(p['value']) for p in usable)
    result={"level":level,"midpoint":level+.5,"populationChartIds":sorted(str(p['id']) for p in usable),
        "ratedCharts":len(values),"basis":"unavailable","locationShift":0.,"medianHalfGap":0.,
        "lowerReference":None,"upperReference":None,"provisional":True,"policy":copy.deepcopy(POLICY)}
    if len(values)<4 or values[-1]-values[0]<=POLICY['minimumPositiveSpread']:
        return result
    center=median(values);i=len(values)//2
    gap=0. if len(values)%2 else (values[i]-values[i-1])/2
    result.update(basis='folder',locationShift=level+.5-center,medianHalfGap=gap,
        provisional=len(values)<5)
    for side,name in [(-1,'lowerReference'),(1,'upperReference')]:
        distances=[abs(v-center) for v in values if side*(v-center)>1e-12]
        result[name]=float(np.quantile(distances,.9,method='linear')) if distances else None
    return result


def width_value(value:float|None, parameters:Mapping[str,Any]) -> float|None:
    if value is None:return None
    if isinstance(value,bool) or not math.isfinite(value):raise ValueError('Width input must be finite.')
    m=parameters['midpoint'];d=value+parameters['locationShift']-m;z=abs(d);b=parameters['medianHalfGap']
    ref=parameters['lowerReference' if d<0 else 'upperReference'];t=POLICY['targetDistance'];k=POLICY['asinhScale']
    if (parameters['basis']=='folder' and ref is not None and ref<t and t>b
            and ref-b>POLICY['minimumPositiveSpread'] and z>b):
        z=max(z,b+k*math.asinh((z-b)/(ref-b)*math.sinh((t-b)/k)))
    return m+math.copysign(z,d)
