import random
from collections import defaultdict
from noc_sim import Mesh, Port, MESH_X, MESH_Y, NUM_VN

def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

def test_single_packet():
    print('=== Test 1: single deterministic packet, known path ===')
    mesh = Mesh()
    src, dst = ((0, 0), (3, 1))
    hops_expected = manhattan(src, dst)
    pid = mesh.inject_packet(src, dst, vn=0, num_flits=1, cycle=0)
    max_cycles = 100
    for _ in range(max_cycles):
        mesh.step()
        if mesh.delivered:
            break
    assert mesh.delivered, f'packet never delivered within {max_cycles} cycles'
    flit, dest_xy, arrive_cycle = mesh.delivered[0]
    assert flit.packet_id == pid
    assert dest_xy == dst, f'delivered at {dest_xy}, expected {dst}'
    latency = arrive_cycle - flit.inject_cycle
    print(f'  src={src} dst={dst} hops={hops_expected} injected@{flit.inject_cycle} delivered@{arrive_cycle} latency={latency}')
    expected_ballpark = 1 + 2 * hops_expected
    assert abs(latency - expected_ballpark) <= 2, f'latency {latency} far from the ~{expected_ballpark}-cycle model (1 + 2*hops) -- investigate before trusting the model'
    print(f'  PASS -- within +/-2 cycles of the 1+2*hops={expected_ballpark} model\n')

def test_random_stress(num_packets=400, max_cycles=4000, seed=1):
    print(f'=== Test 2: random stress, {num_packets} packets, seed={seed} ===')
    rng = random.Random(seed)
    mesh = Mesh()
    all_nodes = [(x, y) for x in range(MESH_X) for y in range(MESH_Y)]
    injected = {}
    pending_to_inject = []
    inject_window = max_cycles // 2
    for _ in range(num_packets):
        src = rng.choice(all_nodes)
        dst = rng.choice([n for n in all_nodes if n != src])
        vn = rng.randrange(NUM_VN)
        num_flits = rng.choice([1, 1, 2, 5])
        cycle = rng.randrange(inject_window)
        pending_to_inject.append((cycle, src, dst, vn, num_flits))
    pending_to_inject.sort()
    seq_tracker = defaultdict(list)
    dest_tracker = {}
    delivered_ids = []
    idx = 0
    for cyc in range(max_cycles):
        while idx < len(pending_to_inject) and pending_to_inject[idx][0] == cyc:
            _, src, dst, vn, nf = pending_to_inject[idx]
            pid = mesh.inject_packet(src, dst, vn, nf, cyc)
            injected[pid] = (src, dst, vn, nf, cyc)
            dest_tracker[pid] = dst
            idx += 1
        mesh.step()
        for flit, dest_xy, arrive_cycle in mesh.delivered:
            seq_tracker[flit.packet_id].append((flit.seq, arrive_cycle))
            if flit.ftype.name in ('TAIL', 'HEADTAIL'):
                delivered_ids.append(flit.packet_id)
        mesh.delivered.clear()
        queues_empty = all((not q for q in mesh._injection_queues.values()))
        if idx >= len(pending_to_inject) and queues_empty and (cyc > inject_window) and all((len(seq_tracker.get(pid, [])) == injected[pid][3] for pid in injected)):
            break
    missing = [pid for pid in injected if len(seq_tracker.get(pid, [])) != injected[pid][3]]
    assert not missing, f'{len(missing)} packets never fully delivered: {missing[:5]}...'
    duplicated = [pid for pid in injected if len(delivered_ids) and delivered_ids.count(pid) > 1]
    assert not duplicated, f'packets delivered more than once: {duplicated[:5]}'
    reordered = []
    for pid, entries in seq_tracker.items():
        seqs = [s for s, _ in entries]
        if seqs != sorted(seqs) or seqs != list(range(len(seqs))):
            reordered.append((pid, seqs))
    assert not reordered, f'flits arrived out of order for packets: {reordered[:5]}'
    latencies = []
    hop_counts = []
    for pid, (src, dst, vn, nf, inj_cyc) in injected.items():
        arrive_cycles = [c for _, c in seq_tracker[pid]]
        latencies.append(max(arrive_cycles) - inj_cyc)
        hop_counts.append(manhattan(src, dst))
    print(f'  all {num_packets} packets delivered exactly once, in order, correct destination')
    print(f'  latency: min={min(latencies)} avg={sum(latencies) / len(latencies):.2f} max={max(latencies)}')
    print(f'  hops:    min={min(hop_counts)} avg={sum(hop_counts) / len(hop_counts):.2f} max={max(hop_counts)}')
    print('  PASS\n')
if __name__ == '__main__':
    test_single_packet()
    test_random_stress()
    print('=== ALL TESTS PASSED ===')
