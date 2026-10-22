from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from enum import IntEnum
import itertools
MESH_X = 4
MESH_Y = 4
VC_PER_VN = 2
NUM_VN = 2
BUF_DEPTH = 4
LINE_FLITS = 4
MAX_PACKET_FLITS = 1 + LINE_FLITS

class Port(IntEnum):
    N = 0
    S = 1
    E = 2
    W = 3
    L = 4

class FlitType(IntEnum):
    HEADTAIL = 0
    HEAD = 1
    BODY = 2
    TAIL = 3
DIR_DELTA = {Port.N: (0, -1), Port.S: (0, 1), Port.E: (1, 0), Port.W: (-1, 0)}
OPPOSITE = {Port.N: Port.S, Port.S: Port.N, Port.E: Port.W, Port.W: Port.E}

@dataclass
class Flit:
    ftype: FlitType
    dest: tuple
    vc_id: int
    packet_id: int
    seq: int
    inject_cycle: int
WATCH: set = set()
_CUR_CYCLE = [0]

def trace(loc, msg, packet_id=None):
    if packet_id is not None and packet_id not in WATCH:
        return
    print(f'cyc={_CUR_CYCLE[0]:>5} | {loc:<24} | {msg}')
ROUTING_ALGO = 'xy'

def route_compute(my_xy, dest_xy) -> Port:
    mx, my = my_xy
    dx, dy = dest_xy
    if dx != mx:
        return Port.E if dx > mx else Port.W
    if dy != my:
        return Port.S if dy > my else Port.N
    return Port.L

def route_compute_yx(my_xy, dest_xy) -> Port:
    mx, my = my_xy
    dx, dy = dest_xy
    if dy != my:
        return Port.S if dy > my else Port.N
    if dx != mx:
        return Port.E if dx > mx else Port.W
    return Port.L

def _minimal_candidates(my_xy, dest_xy):
    mx, my = my_xy
    dx, dy = dest_xy
    cands = []
    if dx > mx:
        cands.append(Port.E)
    elif dx < mx:
        cands.append(Port.W)
    if dy > my:
        cands.append(Port.S)
    elif dy < my:
        cands.append(Port.N)
    return cands

def route_candidates_adaptive(my_xy, dest_xy):
    return _minimal_candidates(my_xy, dest_xy)

def route_candidates_west_first(my_xy, dest_xy):
    mx, my = my_xy
    dx, dy = dest_xy
    if dx < mx:
        return [Port.W]
    cands = []
    if dx > mx:
        cands.append(Port.E)
    if dy > my:
        cands.append(Port.S)
    elif dy < my:
        cands.append(Port.N)
    return cands

class InputVCUnit:
    ALLOC, ACTIVE = ('S_ALLOC', 'S_ACTIVE')

    def __init__(self, my_xy, port, vc_idx):
        self.my_xy = my_xy
        self.port = port
        self.vc_idx = vc_idx
        self.buf: deque[Flit] = deque()
        self.state = self.ALLOC
        self.alloc_out_vc = None
        self.decided_port = None

    @property
    def head(self):
        return self.buf[0] if self.buf else None

    def want_va(self):
        return self.head is not None and self.state == self.ALLOC

    def want_sa(self):
        return self.head is not None and self.state == self.ACTIVE

    def has_room(self):
        return len(self.buf) < BUF_DEPTH
ALL_VC_KEYS = [(p, v) for p in Port for v in range(VC_PER_VN)]
ALL_PORTS = list(Port)

class VCAllocator:

    def __init__(self):
        self.busy = {(op, v): False for op in Port for v in range(VC_PER_VN)}
        self.rr_ptr = {op: 0 for op in Port}

    def arbitrate(self, requesters):
        grants = {}
        n = len(ALL_VC_KEYS)
        for op in Port:
            free_vcs = [v for v in range(VC_PER_VN) if not self.busy[op, v]]
            if not free_vcs:
                continue
            vc_i = 0
            last_idx = None
            for k in range(n):
                if vc_i >= len(free_vcs):
                    break
                idx = (self.rr_ptr[op] + k) % n
                key = ALL_VC_KEYS[idx]
                if requesters.get(key) == op:
                    grants[key] = free_vcs[vc_i]
                    vc_i += 1
                    last_idx = idx
            if last_idx is not None:
                self.rr_ptr[op] = (last_idx + 1) % n
        return grants

    def release(self, op, vc):
        self.busy[op, vc] = False

    def mark_busy(self, op, vc):
        self.busy[op, vc] = True

class SwAllocator:

    def __init__(self):
        self.rr_i = {p: 0 for p in Port}
        self.rr_o = {p: 0 for p in Port}

    def arbitrate(self, want_sa_by_port):
        sai = {}
        for ip in Port:
            cand = want_sa_by_port.get(ip, {})
            if not cand:
                continue
            for k in range(VC_PER_VN):
                vc_idx = (self.rr_i[ip] + k) % VC_PER_VN
                if vc_idx in cand:
                    sai[ip] = (vc_idx, cand[vc_idx])
                    break
        by_out = {}
        for ip, (vc_idx, op) in sai.items():
            by_out.setdefault(op, []).append(ip)
        winners = {}
        n_ports = len(ALL_PORTS)
        for op in Port:
            ips = by_out.get(op, [])
            if not ips:
                continue
            for k in range(n_ports):
                cand_ip = ALL_PORTS[(self.rr_o[op] + k) % n_ports]
                if cand_ip in ips:
                    vc_idx, _ = sai[cand_ip]
                    winners[op] = (cand_ip, vc_idx)
                    break
        return (winners, sai)

class CreditCtrl:

    def __init__(self):
        self.cnt = {(op, v): BUF_DEPTH for op in Port for v in range(VC_PER_VN)}

    def avail(self, op, vc):
        return self.cnt[op, vc] > 0

    def level(self, op, vc):
        return self.cnt[op, vc]

    def consume(self, op, vc):
        self.cnt[op, vc] -= 1
        assert self.cnt[op, vc] >= 0, f'credit underflow at {op},{vc}'

    def credit_return(self, op, vc):
        self.cnt[op, vc] += 1
        assert self.cnt[op, vc] <= BUF_DEPTH, f'credit overflow at {op},{vc}'

class VNRouter:

    def __init__(self, xy, vn_idx=0):
        self.xy = xy
        self.vn_idx = vn_idx
        self.loc = f'router{xy} VN{vn_idx}'
        self.ivu = {(p, v): InputVCUnit(xy, p, v) for p in Port for v in range(VC_PER_VN)}
        self.va = VCAllocator()
        self.sa = SwAllocator()
        self.cc = CreditCtrl()
        self._va_grants = {}
        self._sa_winners = {}
        self._sa_candidates = {}
        self._xbar_out = {}
        self._decided_op = {}

    def _decide_route(self, dest_xy):
        if ROUTING_ALGO == 'xy':
            return route_compute(self.xy, dest_xy)
        if ROUTING_ALGO == 'yx':
            return route_compute_yx(self.xy, dest_xy)
        if ROUTING_ALGO in ('west_first', 'adaptive_unsafe'):
            cands = (route_candidates_west_first if ROUTING_ALGO == 'west_first' else route_candidates_adaptive)(self.xy, dest_xy)
            if not cands:
                return Port.L
            if len(cands) == 1:
                return cands[0]
            return max(cands, key=lambda op: self.cc.level(op, 0))
        raise ValueError(f'unknown ROUTING_ALGO {ROUTING_ALGO!r}')

    def compute(self):
        requesters = {}
        self._decided_op = {}
        for (p, v), u in self.ivu.items():
            if u.want_va():
                op = self._decide_route(u.head.dest)
                self._decided_op[p, v] = op
                requesters[p, v] = op
        self._va_grants = self.va.arbitrate(requesters)
        want_sa_by_port = {p: {} for p in Port}
        for (p, v), u in self.ivu.items():
            if u.want_sa():
                op = u.decided_port
                out_vc = u.alloc_out_vc
                if op == Port.L or self.cc.avail(op, out_vc):
                    want_sa_by_port[p][v] = op
        winners, sai_candidates = self.sa.arbitrate(want_sa_by_port)
        self._sa_winners = winners
        self._sa_candidates = sai_candidates
        for (p, v), out_vc in self._va_grants.items():
            u = self.ivu[p, v]
            f = u.head
            op = self._decided_op[p, v]
            trace(self.loc, f'pkt{f.packet_id} seq{f.seq} {f.ftype.name} at ({p.name},vc{v}): VA granted -> out({op.name},vc{out_vc})  [RC+VA]', f.packet_id)
        self._xbar_out = {}
        for op, (ip, vc_idx) in winners.items():
            u = self.ivu[ip, vc_idx]
            flit = u.head
            out_vc = u.alloc_out_vc
            rewritten = Flit(flit.ftype, flit.dest, out_vc, flit.packet_id, flit.seq, flit.inject_cycle)
            self._xbar_out[op] = (ip, vc_idx, rewritten)
            trace(self.loc, f'pkt{flit.packet_id} seq{flit.seq} {flit.ftype.name} at ({ip.name},vc{vc_idx}): SA won -> crossbar to {op.name}  [SA+ST]', flit.packet_id)

    def commit_local(self):
        for (p, v), out_vc in self._va_grants.items():
            u = self.ivu[p, v]
            op = self._decided_op[p, v]
            u.decided_port = op
            u.alloc_out_vc = out_vc
            u.state = InputVCUnit.ACTIVE
            self.va.mark_busy(op, out_vc)
        for op, (ip, vc_idx) in self._sa_winners.items():
            u = self.ivu[ip, vc_idx]
            flit = u.buf.popleft()
            out_vc = u.alloc_out_vc
            if op != Port.L:
                self.cc.consume(op, out_vc)
            if flit.ftype in (FlitType.TAIL, FlitType.HEADTAIL):
                self.va.release(op, out_vc)
                u.state = InputVCUnit.ALLOC
                u.alloc_out_vc = None
                u.decided_port = None
            self.sa.rr_o[op] = (ALL_PORTS.index(ip) + 1) % len(ALL_PORTS)
        winning_ips = {ip for ip, _ in self._sa_winners.values()}
        for ip, (vc_idx, _) in self._sa_candidates.items():
            if ip in winning_ips:
                self.sa.rr_i[ip] = (vc_idx + 1) % VC_PER_VN

class Router:

    def __init__(self, xy):
        self.xy = xy
        self.vn = [VNRouter(xy, i) for i in range(NUM_VN)]

class Mesh:

    def __init__(self):
        self.routers = {(x, y): Router((x, y)) for x in range(MESH_X) for y in range(MESH_Y)}
        self.cycle = 0
        self.delivered = []
        self.injected_count = 0
        self.link_util = {}
        self.next_packet_id = itertools.count()
        self._injection_queues = {}

    def neighbor(self, xy, port):
        dx, dy = DIR_DELTA[port]
        nx, ny = (xy[0] + dx, xy[1] + dy)
        if 0 <= nx < MESH_X and 0 <= ny < MESH_Y:
            return (nx, ny)
        return None

    def inject_packet(self, src_xy, dest_xy, vn, num_flits, cycle, vc=0):
        pid = next(self.next_packet_id)
        flits = []
        for i in range(num_flits):
            if num_flits == 1:
                ft = FlitType.HEADTAIL
            elif i == 0:
                ft = FlitType.HEAD
            elif i == num_flits - 1:
                ft = FlitType.TAIL
            else:
                ft = FlitType.BODY
            flits.append(Flit(ft, dest_xy, vc_id=vc, packet_id=pid, seq=i, inject_cycle=cycle))
        key = (src_xy, vn, vc)
        self._injection_queues.setdefault(key, deque()).append(deque(flits))
        self.injected_count += 1
        return pid

    def _try_injections(self):
        for (src_xy, vn, vc), pkt_queue in self._injection_queues.items():
            if not pkt_queue:
                continue
            vnr = self.routers[src_xy].vn[vn]
            unit = vnr.ivu[Port.L, vc]
            flitq = pkt_queue[0]
            while flitq and unit.has_room():
                f = flitq.popleft()
                unit.buf.append(f)
                trace(f'inject {src_xy} VN{vn}', f'pkt{f.packet_id} seq{f.seq} {f.ftype.name} pushed onto (L,vc{vc})  [BW]', f.packet_id)
            if not flitq:
                pkt_queue.popleft()

    def step(self):
        _CUR_CYCLE[0] = self.cycle
        for r in self.routers.values():
            for vnr in r.vn:
                vnr.compute()
        for r in self.routers.values():
            for vnr in r.vn:
                vnr.commit_local()
        for xy, r in self.routers.items():
            for vn_idx, vnr in enumerate(r.vn):
                for op, (ip, vc_idx, flit) in vnr._xbar_out.items():
                    if op == Port.L:
                        assert flit.dest == xy, 'misroute: delivered at wrong tile'
                        trace(f'router{xy} VN{vn_idx}', f'pkt{flit.packet_id} seq{flit.seq} {flit.ftype.name} DELIVERED (reached destination)', flit.packet_id)
                        self.delivered.append((flit, xy, self.cycle))
                        continue
                    nxy = self.neighbor(xy, op)
                    assert nxy is not None, f'routed off the edge of the mesh at {xy} port {op}'
                    self.link_util[xy, op] = self.link_util.get((xy, op), 0) + 1
                    dest_unit = self.routers[nxy].vn[vn_idx].ivu[OPPOSITE[op], flit.vc_id]
                    assert dest_unit.has_room(), 'sent without credit -- flow control bug'
                    trace(f'router{xy} VN{vn_idx}', f'pkt{flit.packet_id} seq{flit.seq} {flit.ftype.name} -> {op.name} -> router{nxy}  [LT]', flit.packet_id)
                    dest_unit.buf.append(flit)
        for xy, r in self.routers.items():
            for vn_idx, vnr in enumerate(r.vn):
                for op, (ip, vc_idx) in vnr._sa_winners.items():
                    if ip == Port.L:
                        continue
                    nxy = self.neighbor(xy, ip)
                    if nxy is None:
                        continue
                    self.routers[nxy].vn[vn_idx].cc.credit_return(OPPOSITE[ip], vc_idx)
        self._try_injections()
        self.cycle += 1
