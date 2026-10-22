import random
import noc_sim
from noc_sim import MESH_X, MESH_Y, NUM_VN, Mesh
REQ_VN, RSP_VN = (0, 1)
L1_VC = 0
P_SHARED = 0.3

def _ha_vc():
    return 1 if noc_sim.VC_PER_VN > 1 else 0

class Txn:
    __slots__ = ('txn_id', 'r', 'h', 's', 'shared', 'start_cycle', 'stage', 'req_pid', 'snp_pid', 'rsp_pid', 'cmp_pid')

    def __init__(self, txn_id, r, h, s, shared, start_cycle):
        self.txn_id = txn_id
        self.r, self.h, self.s, self.shared = (r, h, s, shared)
        self.start_cycle = start_cycle
        self.stage = 'req_sent'
        self.req_pid = self.snp_pid = self.rsp_pid = self.cmp_pid = None

def run_coherence_workload(num_transactions, max_cycles, seed, injection_window, p_shared=P_SHARED):
    rng = random.Random(seed)
    mesh = Mesh()
    all_nodes = [(x, y) for x in range(MESH_X) for y in range(MESH_Y)]
    txns = {}
    pid_to_txn = {}
    start_schedule = []
    for i in range(num_transactions):
        r = rng.choice(all_nodes)
        h = rng.choice(all_nodes)
        shared = rng.random() < p_shared
        s = None
        if shared:
            others = [n for n in all_nodes if n != r and n != h]
            s = rng.choice(others) if others else None
            shared = s is not None
        cyc = rng.randrange(injection_window)
        start_schedule.append((cyc, i, r, h, s, shared))
    start_schedule.sort()
    completed_latencies = []
    idx = 0
    for cyc in range(max_cycles):
        while idx < len(start_schedule) and start_schedule[idx][0] == cyc:
            _, i, r, h, s, shared = start_schedule[idx]
            txn = Txn(i, r, h, s, shared, cyc)
            pid = mesh.inject_packet(r, h, REQ_VN, 1, cyc, vc=L1_VC)
            txn.req_pid = pid
            pid_to_txn[pid] = (i, 'req')
            txns[i] = txn
            idx += 1
        mesh.step()
        for flit, dest_xy, arrive_cycle in mesh.delivered:
            if flit.packet_id not in pid_to_txn:
                continue
            txn_id, stage = pid_to_txn.pop(flit.packet_id)
            txn = txns[txn_id]
            if stage == 'req':
                if txn.shared:
                    pid = mesh.inject_packet(txn.h, txn.s, REQ_VN, 1, arrive_cycle, vc=_ha_vc())
                    txn.snp_pid = pid
                    pid_to_txn[pid] = (txn_id, 'snp')
                else:
                    pid = mesh.inject_packet(txn.h, txn.r, RSP_VN, 5, arrive_cycle, vc=_ha_vc())
                    txn.cmp_pid = pid
                    pid_to_txn[pid] = (txn_id, 'cmp')
            elif stage == 'snp':
                pid = mesh.inject_packet(txn.s, txn.h, RSP_VN, 1, arrive_cycle, vc=L1_VC)
                txn.rsp_pid = pid
                pid_to_txn[pid] = (txn_id, 'rsp')
            elif stage == 'rsp':
                pid = mesh.inject_packet(txn.h, txn.r, RSP_VN, 5, arrive_cycle, vc=_ha_vc())
                txn.cmp_pid = pid
                pid_to_txn[pid] = (txn_id, 'cmp')
            elif stage == 'cmp':
                completed_latencies.append(arrive_cycle - txn.start_cycle)
        mesh.delivered.clear()
        queues_empty = all((not q for q in mesh._injection_queues.values()))
        if idx >= len(start_schedule) and queues_empty and (not pid_to_txn) and (len(completed_latencies) == num_transactions) and (cyc > injection_window):
            return {'status': 'OK', 'cycles_used': cyc, 'latencies': completed_latencies}
    return {'status': 'TIMEOUT', 'cycles_used': max_cycles, 'latencies': completed_latencies, 'completed': len(completed_latencies), 'total': num_transactions}
