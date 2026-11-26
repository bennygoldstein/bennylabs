# -*- coding: utf-8 -*-
"""
Back-of-envelope model behind the working paper
"The Sorting Hub That Drives: Routing Packages Through a Coupled Chain of
Cargo Vehicles" (Benny Labs Foundation, 2026).

It estimates, for a chain of releasably coupled cargo vehicles that pass
destination-addressed packages to one another while travelling together:
  1. the transit time removed when hub dwell is replaced by in-chain sorting,
  2. the number of inter-vehicle transfers (hops) a load needs in a chain of N,
  3. how re-ordering the chain shortens hops and unloads the busiest link,
  4. the sorting rate one coupling aperture can carry, and what limits it,
  5. the length of shared road needed to finish a sort, and what a short
     shared segment can and cannot do,
  6. the cost of catching up to a partner vehicle that is not already alongside,
  7. multi-deck parallel transfer, and the time to load a roadside locker.

Every coefficient below is an assumption stated here. Nothing in this file is
a measurement, and no prototype exists. Standard library only. Run:
    python model.py
"""
import itertools
import math

# ------------------------------------------------------------- assumptions
ROAD_SPEED = 90.0         # km/h, cruising speed of a coupled chain
CONVEYOR_SPEED = 0.5      # m/s, deck conveyor through the coupling aperture
UNIT_PITCH = 0.9          # m, parcel length 0.6 m plus 0.3 m gap
PICK_RATE = 900.0         # parcels/h, one pick head pulling parcels from storage
PLACE_RATE = 900.0        # parcels/h, one put head storing arriving parcels
T_COUPLE = 10.0 / 60      # h, approach, align, engage, attest, open the door
T_DECOUPLE = 3.0 / 60     # h, close the door, release, open the gap

# corridor example (the long-haul example named in the specification)
CORRIDOR_KM = 2200.0      # km, approximate Chicago to Miami road distance
HUB_DWELL = 6.0           # h per hub visit: queue, unload, sort, wait, load
HUBS_ON_PATH = 2
CIRCUITY_HUB = 1.08       # extra distance from routing through fixed hubs
CIRCUITY_CHAIN = 1.02     # vehicles that couple stay near their own routes
COUPLING_EVENTS = 4       # relays along the corridor
EVENT_SLACK = 0.25        # h per relay, rendezvous timing slack


def aperture_rate(speed=CONVEYOR_SPEED, pitch=UNIT_PITCH):
    """Parcels per hour one deck-level aperture can pass, Eq. (4)."""
    return 3600.0 * speed / pitch


def link_rate(pick_heads=1, place_heads=1, decks=1, speed=CONVEYOR_SPEED):
    """Effective rate of one link: the slowest of pick, aperture and place."""
    return decks * min(aperture_rate(speed), pick_heads * PICK_RATE,
                       place_heads * PLACE_RATE)


# --------------------------------------------------------------- 1. dwell
def transit_hours(km, circuity, speed, fixed_hours):
    return km * circuity / speed + fixed_hours


def corridor(hub_dwell=HUB_DWELL, hubs=HUBS_ON_PATH):
    conv = transit_hours(CORRIDOR_KM, CIRCUITY_HUB, ROAD_SPEED, hubs * hub_dwell)
    chain = transit_hours(CORRIDOR_KM, CIRCUITY_CHAIN, ROAD_SPEED,
                          COUPLING_EVENTS * EVENT_SLACK)
    return conv, chain


# ----------------------------------------------------------------- 2. hops
def hops_for_order(order, demand):
    """Total transfers and per-link loads for a chain order and a demand dict
    {(origin, destination): parcels}. Links are numbered 0..N-2."""
    pos = {v: i for i, v in enumerate(order)}
    n = len(order)
    load_r = [0] * (n - 1)   # traffic moving toward the higher position
    load_l = [0] * (n - 1)
    total = 0
    for (o, d), m in demand.items():
        a, b = pos[o], pos[d]
        total += m * abs(a - b)
        lo, hi = min(a, b), max(a, b)
        for k in range(lo, hi):
            if b > a:
                load_r[k] += m
            else:
                load_l[k] += m
    return total, load_r, load_l


def feeder_demand(n, parcels):
    """One feeder vehicle 'F' carries `parcels` split evenly over n-1 others."""
    others = [f'V{i}' for i in range(1, n)]
    per = parcels / (n - 1)
    return ['F'] + others, {('F', v): per for v in others}


def mean_hops_feeder(n, feeder_pos):
    return sum(abs(i - feeder_pos) for i in range(n) if i != feeder_pos) / (n - 1)


def mean_hops_any_to_any(n):
    """Uniform exchange among all pairs: mean |i-j| = (N+1)/3."""
    s = sum(abs(i - j) for i in range(n) for j in range(n) if i != j)
    return s / (n * (n - 1))


# -------------------------------------------------------- 5. shared segment
def sort_hours(max_link_parcels, rate):
    return max_link_parcels / rate


def shared_km(sort_h, speed=ROAD_SPEED):
    return speed * (T_COUPLE + sort_h + T_DECOUPLE)


def parcels_in_segment(km, rate, speed=ROAD_SPEED):
    usable = km / speed - T_COUPLE - T_DECOUPLE
    return max(0.0, usable) * rate


# ------------------------------------------------------------- 6. catch-up
def catch_up(gap_min, dv_kmh, speed=ROAD_SPEED):
    """Follower at speed+dv closing a time gap. Returns hours and km driven."""
    gap_km = speed * gap_min / 60.0
    hours = gap_km / dv_kmh
    return hours, hours * (speed + dv_kmh)


# ---------------------------------------------------------------- 7. locker
def locker_minutes(n, t_align_s=60.0, t_place_s=25.0):
    return (t_align_s + n * t_place_s) / 60.0


# a fixed, illustrative demand matrix among six vehicles (parcels)
DEMAND6 = {
    ('A', 'D'): 240, ('A', 'E'): 60, ('B', 'F'): 180, ('B', 'C'): 40,
    ('C', 'A'): 30, ('D', 'B'): 120, ('E', 'F'): 90, ('F', 'A'): 50,
    ('A', 'F'): 150, ('E', 'B'): 70,
}


def main():
    line = '-' * 78
    ar = aperture_rate()
    print('ASSUMPTIONS')
    print(f'  road {ROAD_SPEED:.0f} km/h | conveyor {CONVEYOR_SPEED} m/s | pitch {UNIT_PITCH} m'
          f' | pick/place head {PICK_RATE:.0f}/h')
    print(f'  couple {T_COUPLE*60:.0f} min, decouple {T_DECOUPLE*60:.0f} min')

    print('\n1. CORRIDOR TRANSIT TIME, Chicago-Miami example (driverless both ways)')
    print(line)
    conv, chain = corridor()
    print(f'  conventional: {CORRIDOR_KM*CIRCUITY_HUB/ROAD_SPEED:.1f} h driving + '
          f'{HUBS_ON_PATH}x{HUB_DWELL:.0f} h dwell = {conv:.1f} h')
    print(f'  coupled chain: {CORRIDOR_KM*CIRCUITY_CHAIN/ROAD_SPEED:.1f} h driving + '
          f'{COUPLING_EVENTS}x{EVENT_SLACK*60:.0f} min slack = {chain:.1f} h')
    print(f'  saved {conv-chain:.1f} h ({(conv-chain)/conv*100:.0f}%)')
    print('  sensitivity, hours saved:   dwell/hub ->  ' +
          ''.join(f'{d:>6.0f} h' for d in (1, 2, 4, 6, 8, 12)))
    for hubs in (1, 2, 3):
        row = ''
        for d in (1, 2, 4, 6, 8, 12):
            c, w = corridor(d, hubs)
            row += f'{c-w:>8.1f}'
        print(f'    {hubs} hub(s) on path             {row}')

    print('\n2. MEAN TRANSFERS PER PARCEL (hops) IN A CHAIN OF N')
    print(line)
    print('   N | feeder at end | feeder in middle | any-to-any (N+1)/3 | busiest link, feeder end vs middle')
    for n in range(3, 9):
        e = mean_hops_feeder(n, 0)
        mid = (n - 1) // 2
        m = mean_hops_feeder(n, mid)
        a = mean_hops_any_to_any(n)
        busiest_mid = max(mid, n - 1 - mid) / (n - 1)
        print(f'  {n:>2} | {e:>13.2f} | {m:>16.2f} | {a:>18.2f} | 100% vs {busiest_mid*100:.0f}%')

    print('\n3. RE-ORDERING: one feeder with 1,200 parcels for four others (N = 5)')
    print(line)
    order, dem = feeder_demand(5, 1200)
    for name, ordr in (('feeder at end', order),
                       ('feeder in middle', order[1:3] + ['F'] + order[3:])):
        tot, r, l = hops_for_order(ordr, dem)
        busiest = max(max(r), max(l))
        print(f'  {name:<17} {" ".join(ordr):<16} transfers {tot:>6.0f} | '
              f'busiest link {busiest:>5.0f} parcels')
    print('\n   general demand among six vehicles (fixed example, 1,030 parcels):')
    vehicles = 'ABCDEF'
    results = []
    for p in itertools.permutations(vehicles):
        if p[0] > p[-1]:
            continue  # a chain read backwards is the same chain
        tot, r, l = hops_for_order(p, DEMAND6)
        results.append((tot, max(max(r), max(l)), ''.join(p)))
    results.sort()
    alpha = hops_for_order(tuple(vehicles), DEMAND6)
    print(f'   orders checked {len(results)}; parcels {sum(DEMAND6.values())}')
    print(f'   best order  {results[0][2]}: transfers {results[0][0]}, busiest link {results[0][1]}')
    print(f'   as-arrived  ABCDEF: transfers {alpha[0]}, busiest link '
          f'{max(max(alpha[1]), max(alpha[2]))}')
    print(f'   worst order {results[-1][2]}: transfers {results[-1][0]}, busiest link {results[-1][1]}')
    print(f'   mean hops: best {results[0][0]/1030:.2f}, as-arrived {alpha[0]/1030:.2f}, '
          f'worst {results[-1][0]/1030:.2f}')

    print('\n4. LINK RATE (parcels/h) = decks x min(aperture, picks, places)')
    print(line)
    print(f'  aperture alone at {CONVEYOR_SPEED} m/s, {UNIT_PITCH} m pitch: {ar:.0f}/h')
    for v in (0.3, 0.5, 1.0):
        print(f'    conveyor {v} m/s -> aperture {aperture_rate(v):>5.0f}/h')
    for heads in (1, 2, 3):
        print(f'  {heads} pick head(s), 1 deck: {link_rate(heads, heads):>5.0f}/h | '
              f'2 decks: {link_rate(heads, heads, 2):>5.0f}/h | '
              f'3 decks: {link_rate(heads, heads, 3):>5.0f}/h')

    print('\n5. SHARED ROAD NEEDED TO FINISH A FEEDER SORT (N = 5, includes coupling)')
    print(line)
    print('  parcels | feeder end, 1 head | end, 2 heads | middle, 2 heads (1/side) | middle, 4 heads | middle, 4 heads, 2 decks')
    for parcels in (300, 600, 1200, 2400):
        cases = [
            (parcels, link_rate(1, 1)),
            (parcels, link_rate(2, 2)),
            (parcels / 2, link_rate(1, 1)),
            (parcels / 2, link_rate(2, 2)),
            (parcels / 2, link_rate(2, 2, 2)),
        ]
        row = ''
        for load, rate in cases:
            h = sort_hours(load, rate)
            row += f' {shared_km(h):>6.0f} km ({(T_COUPLE+h+T_DECOUPLE)*60:>3.0f} min) |'
        print(f'  {parcels:>6}  |{row}')
    print('\n  what a short shared segment can sort on the busiest link (parcels):')
    for km in (15, 20, 30, 50, 100, 200):
        print(f'    {km:>3} km: 1 head {parcels_in_segment(km, link_rate(1,1)):>6.0f} | '
              f'aperture-bound {parcels_in_segment(km, link_rate(3,3)):>6.0f} | '
              f'2 decks {parcels_in_segment(km, link_rate(3,3,2)):>6.0f}')
    print(f'  overhead alone (couple + decouple) uses '
          f'{ROAD_SPEED*(T_COUPLE+T_DECOUPLE):.1f} km of shared road')

    print('\n   any-to-any exchange, each vehicle sends 60 parcels to every other vehicle:')
    for n in (4, 5, 6, 8):
        m = 60
        busiest_dir = m * (n // 2) * (n - n // 2)
        both = 2 * busiest_dir
        h = sort_hours(both, ar)
        print(f'   N={n}: {m*n*(n-1):>5} parcels, centre link {busiest_dir} each way, '
              f'one shared aperture {h*60:>4.0f} min, shared road {shared_km(h):>4.0f} km')

    print('\n6. CATCHING UP TO A PARTNER (follower speeds up by dv)')
    print(line)
    print('  gap   ' + ''.join(f'  dv={dv:>2} km/h        ' for dv in (5, 10, 20)))
    for gap in (1, 5, 10, 20):
        row = ''
        for dv in (5, 10, 20):
            h, km = catch_up(gap, dv)
            row += f'  {h*60:>5.0f} min {km:>5.0f} km  '
        print(f'  {gap:>2} min{row}')

    print('\n7. MULTI-DECK AND LAST-MILE')
    print(line)
    for decks in (1, 2, 3):
        h = sort_hours(1200, link_rate(2, 2, decks))
        print(f'  {decks} deck(s), 1,200 parcels over one link, 2 heads per deck: '
              f'{h*60:.0f} min sorting, {shared_km(h):.0f} km shared road')
    for n in (1, 5, 10, 20):
        print(f'  roadside locker, {n:>2} parcels: {locker_minutes(n):.1f} min beside the locker')

    print('\n8. EXPOSURE: each aperture crossing is one more transition')
    print(line)
    for p in (1e-4, 1e-3):
        print(f'  p = {p:g} per crossing: ' + ', '.join(
            f'{h} hop(s) {(1-(1-p)**h)*100:.2f}%' for h in (1, 2, 3, 5)))


if __name__ == '__main__':
    main()
