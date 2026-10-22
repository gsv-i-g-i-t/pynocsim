import json
import time
from pathlib import Path
from param_sweep import BUF_DEPTHS, NUM_NODES, SEEDS, set_params
from coherence_traffic import run_coherence_workload
WINDOW = 400
MAX_CYCLES = 30000
RATES = [0.02, 0.04, 0.06, 0.08, 0.1, 0.15, 0.2, 0.25, 0.3]
VC_PER_VN = 2

def sweep_buf_depth(bd):
    set_params(bd, VC_PER_VN)
    points = []
    for rate in RATES:
        n = max(1, int(round(rate * WINDOW * NUM_NODES)))
        runs = [run_coherence_workload(n, MAX_CYCLES, seed, WINDOW) for seed in SEEDS]
        ok = [r for r in runs if r['status'] == 'OK']
        if not ok:
            points.append({'rate': rate, 'status': 'SATURATED'})
            continue
        all_lat = [l for r in ok for l in r['latencies']]
        points.append({'rate': rate, 'status': 'OK', 'lat_avg': sum(all_lat) / len(all_lat)})
    return points

def knee_rate(points, factor=2.5):
    ok_points = [p for p in points if p['status'] == 'OK']
    if len(ok_points) < 2:
        return None
    base = ok_points[0]['lat_avg']
    for p in ok_points:
        if p['lat_avg'] > factor * base:
            return p['rate']
    return None
if __name__ == '__main__':
    t0 = time.time()
    results = {}
    print('=== Coherence-transaction traffic: BUF_DEPTH sweep (VC_PER_VN=2 fixed) ===')
    for bd in BUF_DEPTHS:
        pts = sweep_buf_depth(bd)
        results[f'bd{bd}'] = {'bd': bd, 'points': pts}
        knee = knee_rate(pts)
        print(f'  BUF_DEPTH={bd:<3} knee_rate(txn/cyc/node)={knee!s:<6}  (elapsed {time.time() - t0:.1f}s)')
    out = Path(__file__).parent / 'coherence_sweep_results.json'
    out.write_text(json.dumps(results, indent=2))
    print(f'\nTotal time: {time.time() - t0:.1f}s. Raw results written to {out}')
    set_params(4, 2)
