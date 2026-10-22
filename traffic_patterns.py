import random
from noc_sim import MESH_X, MESH_Y

def build_schedule(num_packets, injection_window, seed, pattern='uniform', flit_sizes=(1, 1, 2, 5), hotspot_frac=0.6):
    rng = random.Random(seed)
    all_nodes = [(x, y) for x in range(MESH_X) for y in range(MESH_Y)]
    hotspot = (MESH_X - 1, MESH_Y - 1)
    schedule = []
    for _ in range(num_packets):
        src = rng.choice(all_nodes)
        if pattern == 'hotspot' and src != hotspot and (rng.random() < hotspot_frac):
            dst = hotspot
        elif pattern == 'transpose':
            tdst = (src[1], src[0])
            dst = tdst if tdst != src else rng.choice([n for n in all_nodes if n != src])
        else:
            dst = rng.choice([n for n in all_nodes if n != src])
        vn = rng.randrange(2)
        nf = rng.choice(flit_sizes)
        cyc = rng.randrange(injection_window)
        schedule.append((cyc, src, dst, vn, nf))
    schedule.sort()
    return schedule
