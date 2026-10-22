import json
import time
from pathlib import Path
import noc_sim
from param_sweep import MAX_CYCLES, NUM_NODES, RATES, SEEDS, WINDOW, run_once, set_params
ALGORITHMS = ['xy', 'yx', 'west_first', 'adaptive_unsafe']
BUF_DEPTH, VC_PER_VN = (4, 2)

def sweep_algo(algo, pattern='uniform'):
    noc_sim.ROUTING_ALGO = algo
    points = []
    for rate in RATES:
        num_packets = max(1, int(round(rate * WINDOW * NUM_NODES)))
        runs = [run_once(num_packets, MAX_CYCLES, seed, WINDOW, pattern) for seed in SEEDS]
        ok = [r for r in runs if r['status'] == 'OK']
        if not ok:
            points.append({'rate': rate, 'status': 'SATURATED_OR_DEADLOCK'})
            continue
        points.append({'rate': rate, 'status': 'OK', 'lat_avg': sum((r['lat_avg'] for r in ok)) / len(ok), 'lat_max': max((r['lat_max'] for r in ok)), 'link_imbalance': sum((r['link_max'] for r in ok)) / len(ok) / max(1e-09, sum((r['link_avg'] for r in ok)) / len(ok))})
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
    set_params(BUF_DEPTH, VC_PER_VN)
    t0 = time.time()
    results = {'uniform': {}, 'hotspot': {}}
    for pattern in ('uniform', 'hotspot'):
        print(f'\n=== Routing algorithm comparison, {pattern} traffic (BUF_DEPTH={BUF_DEPTH}, VC_PER_VN={VC_PER_VN}) ===')
        for algo in ALGORITHMS:
            pts = sweep_algo(algo, pattern)
            results[pattern][algo] = pts
            knee = knee_rate(pts)
            n_bad = sum((1 for p in pts if p['status'] != 'OK'))
            imbalance_at_mid = next((p['link_imbalance'] for p in pts if p['status'] == 'OK' and p['rate'] == 0.3), None)
            print(f'  {algo:<16} knee_rate={knee!s:<6} link_imbalance@0.3={imbalance_at_mid!s:<6} saturated_points={n_bad}  (elapsed {time.time() - t0:.1f}s)')
    noc_sim.ROUTING_ALGO = 'xy'
    out = Path(__file__).parent / 'routing_sweep_results.json'
    out.write_text(json.dumps(results, indent=2))
    print(f'\nTotal time: {time.time() - t0:.1f}s. Raw results written to {out}')
