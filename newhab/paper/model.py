# -*- coding: utf-8 -*-
"""
Back-of-envelope model behind the working paper
"Resistance Without a Weight: A Dial-Set, Direction-Dependent Friction Unit
for Rehabilitation Braces and Gym Machines" (Benny Labs Foundation, 2026).

It estimates, for a rotary friction resistance unit (one-way clutch, brake
drum, friction member, worm-set preload, dial in pounds), worn in an elbow
brace or bolted to a cable machine in place of a weight stack:
  1. brake torque from pad preload, friction coefficient and drum radius, the
     dial's turns-to-pounds relation, knob torque and worm self-locking,
  2. the resistance profile across an elbow curl for a dumbbell, an elastic
     band and the friction unit, and the work each asks per repetition,
  3. what is released when the user stops or lets go mid-repetition,
  4. the weak points: no eccentric load, heat in the drum (rehab and gym
     loads), friction-coefficient drift and wear, static versus kinetic
     friction,
  5. mass and volume against a weight stack and loose iron plates.

Every coefficient below is an assumption stated here. Nothing in this file is
a measurement, and no prototype has been built or tested. Standard library
only. Run:
    python model.py
"""
import math

# ------------------------------------------------------------- constants
G = 9.80665               # m/s2
LBF = 4.44822             # N per pound-force
C_STEEL = 490.0           # J/kgK, specific heat of steel
RHO_STEEL = 7850.0        # kg/m3

# ------------------------------------------------------------- assumptions
# Arm (adult, standing curl, upper arm vertical; angle 0 = forearm hanging)
L_HAND = 0.33             # m, elbow axis to the centre of the grip
ARM_MASS = 1.8            # kg, forearm and hand (about 2.2% of an 80 kg adult)
ARM_COM = 0.17            # m, elbow to forearm-and-hand centre of mass
ARM_I = 0.065             # kg m2, forearm and hand about the elbow
ROM_DEG = 140.0           # degrees of elbow flexion in one repetition
ELBOW_HEIGHT = 1.05       # m, elbow above the floor when standing

# Rehabilitation load range used on the public Newhab site: 0 to 5 lb
REHAB_LB = (1.0, 2.0, 5.0)

# Wearable unit ("about the size of a bottle cap")
DRUM_R = 0.012            # m, braking radius of the drum
MU_K = 0.35               # kinetic friction coefficient, dry pad on steel
MU_S_RATIO = 1.20         # static / kinetic friction
PAD_AREA = 150e-6         # m2, shoe contact area (10 mm x 15 mm)
WRAP_DEG = 270.0          # band wrap angle, for the band form of the friction member
WORM_RATIO = 40.0         # worm reduction (the application gives about 30:1 to 100:1)
CAM_RISE = 7.0e-3         # m of follower travel per worm-wheel revolution
K_STACK = 2.0e6           # N/m, pad + shoe + housing contact stiffness in series
WORM_MODULE = 0.5e-3      # m
WORM_PD = 6.0e-3          # m, worm pitch diameter, single start
WORM_MU = (0.10, 0.15, 0.25)   # worm-to-wheel friction, lubricated to dry polymer
PRESSURE_ANGLE = 20.0     # degrees, normal pressure angle of the worm thread
CLUTCH_K = 200.0          # N m/rad, torsional stiffness of clutch, shaft and arms
UNIT_HEAT_MASS = 0.030    # kg steel-equivalent in thermal contact with the drum
UNIT_AREA = 0.0032        # m2, outside of a 32 mm round housing
H_NAT = 10.0              # W/m2K, natural convection plus radiation
SKIN_START = 25.0         # C, unit temperature at the start of a session

# Rehabilitation session
REHAB_SETS, REHAB_REPS = 3, 15
REHAB_REP_S = 3.0         # s per repetition (1.5 s up, 1.5 s back)
REHAB_REST_S = 60.0       # s between sets

# Gym unit, cable machine
SPOOL_R = 0.05            # m, cable spool on the unit's input
GYM_DRUM_R = 0.08         # m
STROKE = 0.60             # m of cable pulled per repetition
GYM_SETS, GYM_REPS = 5, 10
GYM_REP_S = 3.0
GYM_REST_S = 90.0
GYM_LB = (20.0, 50.0, 100.0)
H_GYM = 12.0              # W/m2K, drum in a ventilated housing
GYM_DRUMS = {             # name: (steel mass kg, exposed surface m2)
    'wearable-size unit': (UNIT_HEAT_MASS, UNIT_AREA),
    '0.5 kg drum':        (0.5, 0.020),
    '1 kg drum':          (1.0, 0.040),
    '2 kg drum':          (2.0, 0.063),
}
PAD_LIMIT = 150.0         # C, a conservative ceiling for an organic friction pad
COMMERCIAL_DUTY = 120.0   # s between back-to-back sets on a busy machine

# Weight stack and the gym unit, mass and envelope
STACK_LB = 200.0
STACK_ENVELOPE = (0.35, 0.25, 1.80)   # m, plates, guide rods, travel and shroud
STACK_HARDWARE = 12.0                 # kg, rods, top plate, selector, cable run
GYM_UNIT_MASS = 4.0                   # kg, drum, shaft, clutch, housing, worm, dial
GYM_UNIT_ENVELOPE = (0.22, 0.10)      # m, diameter and depth
REHAB_UNIT_MASS = 0.060               # kg per wearable unit
BRACE_REST_MASS = 0.250               # kg, sleeve, arms and straps
REWIND_N = 15.0                       # N, light rewind spring on the cable

# Elastic band (Thera-Band tension at 100% elongation, Uchida et al. 2016)
BAND_F100 = {'yellow': 11.5, 'red': 15.7, 'green': 17.8, 'blue': 24.6,
             'black': 32.2, 'silver': 38.2, 'gold': 55.5}   # N
BAND_MASS = 0.020         # kg, a 0.7 m band

# Friction drift
MU_RANGE = (0.28, 0.30, 0.35, 0.40, 0.42)


# ----------------------------------------------------------------- helpers
def lb_torque(lb, arm=L_HAND):
    """Torque at the elbow equal to a dumbbell of `lb` held at the grip, forearm level."""
    return lb * LBF * arm


def shoe_normal(torque, r=DRUM_R, mu=MU_K):
    """Eq. (1): T = mu N r for a shoe (no self-energising)."""
    return torque / (mu * r)


def band_slack_tension(torque, r=DRUM_R, mu=MU_K, wrap_deg=WRAP_DEG):
    """Band brake: T = (T1 - T2) r, T1 = T2 exp(mu theta); returns T2."""
    e = math.exp(mu * math.radians(wrap_deg))
    return torque / (r * (e - 1.0))


def band_mu_gain(mu=MU_K, wrap_deg=WRAP_DEG):
    """Relative torque change per relative change in mu for a band brake."""
    x = mu * math.radians(wrap_deg)
    return x * math.exp(x) / (math.exp(x) - 1.0)


def knob_turns_for(normal):
    """Eq. (2): follower travel = normal / K; knob turns = travel / rise * ratio."""
    travel = normal / K_STACK
    return travel / CAM_RISE * WORM_RATIO, travel


def worm_lead_angle():
    return math.atan(WORM_MODULE / WORM_PD)          # single start: lead = pi m


def worm_efficiency(mu, lam):
    phi = math.radians(PRESSURE_ANGLE)
    return (math.cos(phi) - mu * math.tan(lam)) / (math.cos(phi) + mu / math.tan(lam))


def self_locks(mu, lam):
    return math.tan(lam) < mu / math.cos(math.radians(PRESSURE_ANGLE))


# ------------------------------------------------------- resistance profile
def dumbbell_torque(theta, lb):
    return lb * LBF * L_HAND * math.sin(theta)


def band_geometry(theta):
    """Band from the hand to an anchor under the foot, directly below the elbow.
    Returns strain and the band's moment arm about the elbow."""
    hx, hy = L_HAND * math.sin(theta), -L_HAND * math.cos(theta)
    ax, ay = 0.0, -ELBOW_HEIGHT
    dx, dy = ax - hx, ay - hy
    length = math.hypot(dx, dy)
    rest = ELBOW_HEIGHT - L_HAND                  # just taut with the arm hanging
    strain = max(0.0, (length - rest) / rest)
    arm = abs(hx * dy / length - hy * dx / length)
    return strain, arm


def band_torque(theta, f100):
    s, arm = band_geometry(theta)
    return f100 * s * arm


def strength(theta):
    """Illustrative elbow-flexor strength shape: peak at 90 deg, 55% at full extension."""
    return 0.55 + 0.45 * math.sin(theta)


# -------------------------------------------------------------------- heat
def run_session(power_w, rep_s, reps, sets, rest_s, c_jk, ha, t0=SKIN_START, amb=SKIN_START,
                dt=0.5, between=None):
    """Lumped drum temperature. Heat goes in at a steady rate during each set."""
    t, temp, peak = 0.0, t0, t0
    rest = rest_s if between is None else between - reps * rep_s
    for s in range(sets):
        for _ in range(int(reps * rep_s / dt)):
            temp += (power_w - ha * (temp - amb)) * dt / c_jk
        peak = max(peak, temp)
        if s < sets - 1:
            for _ in range(int(rest / dt)):
                temp -= ha * (temp - amb) * dt / c_jk
    return peak


def main():
    line = '-' * 78
    rom = math.radians(ROM_DEG)
    print('ASSUMPTIONS')
    print(f'  grip at {L_HAND} m | drum radius {DRUM_R*1000:.0f} mm | mu_k {MU_K} | mu_s/mu_k {MU_S_RATIO}'
          f' | worm {WORM_RATIO:.0f}:1 | cam rise {CAM_RISE*1000:.1f} mm/rev | stack stiffness '
          f'{K_STACK/1e6:.1f} N/um')
    print(f'  curl range 0-{ROM_DEG:.0f} deg | rehab session {REHAB_SETS}x{REHAB_REPS} | gym session '
          f'{GYM_SETS}x{GYM_REPS}, stroke {STROKE} m')

    # ---------------------------------------------------------------- 1
    print('\n1. FROM PRELOAD TO POUNDS (wearable unit, elbow)')
    print(line)
    print('  dial lb | elbow torque N m | shoe normal force N | pad pressure MPa | '
          'band slack tension N | knob turns from zero')
    for lb in REHAB_LB:
        T = lb_torque(lb)
        n = shoe_normal(T)
        turns, travel = knob_turns_for(n)
        print(f'  {lb:>6.1f} | {T:>16.2f} | {n:>19.0f} | {n/PAD_AREA/1e6:>16.1f} | '
              f'{band_slack_tension(T):>20.0f} | {turns:>6.1f} (follower {travel*1e3:.3f} mm)')
    T5 = lb_torque(5.0)
    n5 = shoe_normal(T5)
    turns5, _ = knob_turns_for(n5)
    print(f'  pounds per knob turn: {5.0/turns5:.2f} lb; per 10 deg of knob: {5.0/turns5/36*16:.2f} oz')
    for r in (0.012, 0.015, 0.018):
        print(f'  drum radius {r*1000:.0f} mm: shoe force for 5 lb {shoe_normal(T5, r):.0f} N '
              f'({shoe_normal(T5, r)/LBF:.0f} lbf)')
    print(f'  band form, {WRAP_DEG:.0f} deg wrap: exp(mu theta) = '
          f'{math.exp(MU_K*math.radians(WRAP_DEG)):.2f}; actuating force is '
          f'{n5/band_slack_tension(T5):.1f}x lower than the shoe; torque moves '
          f'{band_mu_gain():.2f}x as much as mu')
    lam = worm_lead_angle()
    wheel_torque = n5 * CAM_RISE / (2 * math.pi)
    print(f'  worm lead angle {math.degrees(lam):.2f} deg; cam load on the wheel at 5 lb '
          f'{wheel_torque:.2f} N m')
    for mu in WORM_MU:
        eff = worm_efficiency(mu, lam)
        knob = wheel_torque / WORM_RATIO / eff
        phi = math.degrees(math.atan(mu / math.cos(math.radians(PRESSURE_ANGLE))))
        print(f'    worm mu {mu:.2f}: friction angle {phi:>5.2f} deg, self-locking '
              f'{"yes" if self_locks(mu, lam) else "NO "}, efficiency {eff*100:>4.1f}%, '
              f'knob torque at 5 lb {knob:.3f} N m')
    for lb in GYM_LB + (200.0,):
        T = lb * LBF * SPOOL_R
        print(f'  gym {lb:>5.0f} lb at the handle: torque {T:>5.1f} N m, shoe force on an '
              f'{GYM_DRUM_R*1000:.0f} mm drum {shoe_normal(T, GYM_DRUM_R):>5.0f} N')

    # ---------------------------------------------------------------- 2
    print('\n2. RESISTANCE ACROSS THE CURL (lb-equivalent at the grip = torque / grip radius)')
    print(line)
    lb = 5.0
    peak_band = max(band_torque(math.radians(d), 1.0) for d in range(0, int(ROM_DEG) + 1))
    f100 = lb_torque(lb) / peak_band
    print(f'  all three matched to a {lb:.0f} lb peak; the band needs {f100:.0f} N at 100% '
          f'elongation (Thera-Band silver {BAND_F100["silver"]}, gold {BAND_F100["gold"]} N)')
    print('  angle | dumbbell | band (strain) | tension unit | strength shape')
    for d in range(0, int(ROM_DEG) + 1, 10):
        th = math.radians(d)
        s, _ = band_geometry(th)
        print(f'  {d:>5} | {dumbbell_torque(th, lb)/L_HAND/LBF:>8.2f} | '
              f'{band_torque(th, f100)/L_HAND/LBF:>5.2f} ({s*100:>3.0f}%) | {lb:>12.2f} | '
              f'{strength(th):>6.2f}')
    steps = 1400
    w_db = w_band = 0.0
    for i in range(steps):
        th = rom * (i + 0.5) / steps
        w_db += dumbbell_torque(th, lb) * rom / steps
        w_band += band_torque(th, f100) * rom / steps
    w_tu = lb_torque(lb) * rom
    print(f'  work per lift: dumbbell {w_db:.1f} J | band {w_band:.1f} J | tension unit {w_tu:.1f} J '
          f'({w_tu/w_db:.2f}x the dumbbell)')
    print(f'  mean resistance over the lift: dumbbell {w_db/rom/L_HAND/LBF:.2f} lb | band '
          f'{w_band/rom/L_HAND/LBF:.2f} lb | unit {lb:.2f} lb')
    # matched on the hardest point instead of the peak
    eff_db = max(math.sin(math.radians(d)) / strength(math.radians(d)) for d in range(0, 141))
    eff_tu = max(1.0 / strength(math.radians(d)) for d in range(0, 141))
    scale_db, scale_tu = 1.0 / eff_db, 1.0 / eff_tu
    w_db_n = w_db / lb_torque(lb) * scale_db
    w_tu_n = rom * scale_tu
    print(f'  matched on the hardest point (relative effort 1.0 somewhere in the range): unit set '
          f'to {scale_tu:.2f} of peak strength; work: dumbbell {w_db_n:.2f}, unit {w_tu_n:.2f} '
          f'(x peak strength x rad) -> dumbbell does {w_db_n/w_tu_n:.2f}x the unit')
    for start in (20, 30):
        eff_tu2 = max(1.0 / strength(math.radians(d)) for d in range(start, 141))
        print(f'    curl from {start} deg: unit can be set to {1/eff_tu2:.2f} of peak strength, '
              f'work {(math.radians(ROM_DEG-start))/eff_tu2:.2f} vs dumbbell '
              f'{(math.cos(math.radians(start))-math.cos(rom))/eff_db:.2f}')
    print('  speed: inertial torque a 5 lb dumbbell adds at the start of a lift (sinusoidal motion)')
    m_db = lb * LBF / G
    for tr in (0.5, 1.0, 2.0):
        a_max = rom / 2 * (math.pi / tr) ** 2
        tq = m_db * L_HAND ** 2 * a_max
        print(f'    lift in {tr:.1f} s: +{tq/L_HAND/LBF:.2f} lb ({tq/lb_torque(lb)*100:.0f}% of the peak); '
              f'tension unit +0')

    # ---------------------------------------------------------------- 3
    print('\n3. STOPPING OR LETTING GO MID-REP (elbow at 90 deg, 5 lb)')
    print(line)
    th = math.radians(90)
    e_swing = m_db * G * L_HAND * (1 - math.cos(th)) + ARM_MASS * G * ARM_COM * (1 - math.cos(th))
    i_tot = ARM_I + m_db * L_HAND ** 2
    w = math.sqrt(2 * e_swing / i_tot)
    print(f'  dumbbell, muscle gives way: {e_swing:.1f} J released, arm reaches {w:.1f} rad/s, '
          f'hand {w*L_HAND:.1f} m/s at the bottom')
    h = ELBOW_HEIGHT
    print(f'  dumbbell dropped from the hand: falls {h:.2f} m, {m_db*G*h:.1f} J, '
          f'{math.sqrt(2*G*h):.1f} m/s at the floor')
    s90, _ = band_geometry(th)
    rest = ELBOW_HEIGHT - L_HAND
    e_band = 0.5 * f100 * s90 * (s90 * rest)
    v_band = math.sqrt(2 * e_band / (BAND_MASS / 3))
    print(f'  band released: {e_band:.1f} J stored at {s90*100:.0f}% strain, tension '
          f'{f100*s90:.0f} N; free end could reach {v_band:.0f} m/s (effective mass 1/3 of '
          f'{BAND_MASS*1000:.0f} g)')
    e_wind = T5 ** 2 / (2 * CLUTCH_K)
    print(f'  tension unit: no lifted mass, no stretched band; elastic windup in clutch and arms '
          f'{e_wind:.2f} J, a spring-back of {math.degrees(T5/CLUTCH_K):.1f} deg')
    tq_arm = ARM_MASS * G * ARM_COM
    print(f'  the forearm itself: {tq_arm:.2f} N m at 90 deg = {tq_arm/L_HAND/LBF:.2f} lb at the grip.')
    print(f'    flexion-only unit: when the user relaxes, the forearm falls freely under its own weight')
    print(f'    with the out-motion braked at >= {tq_arm/L_HAND/LBF:.1f} lb (x {MU_S_RATIO} static margin '
          f'-> {tq_arm/L_HAND/LBF/MU_S_RATIO:.1f} lb set) it holds')
    e_stack = 100 * LBF * STROKE
    print(f'  cable machine, 100 lb at the top of a {STROKE} m stroke: stack releases {e_stack:.0f} J; '
          f'unit with rewind spring {REWIND_N*STROKE:.0f} J; unit with rigid lever 0 J')

    # ---------------------------------------------------------------- 4
    print('\n4. WEAK POINTS')
    print(line)
    print('  (a) eccentric load. Friction always opposes motion, so it can only take energy out.')
    print('      Who works, by direction, for an elbow brace:')
    print('        device                     lift (flexion)          lower (extension)')
    print('        dumbbell                   flexors, concentric     flexors, eccentric')
    print('        unit, flexion braked       flexors, concentric     no load (limb weight only)')
    print('        unit, both braked          flexors, concentric     EXTENSORS, concentric')
    print(f'      flexors lowering a 5 lb dumbbell absorb {w_db:.1f} J per rep; any friction setting: 0 J')
    print('  (b) heat. All work done against the unit becomes heat in the drum.')
    c_r = UNIT_HEAT_MASS * C_STEEL
    ha_r = UNIT_AREA * H_NAT
    print(f'      wearable unit: {UNIT_HEAT_MASS*1000:.0f} g steel-equivalent = {c_r:.1f} J/K, '
          f'cooling {ha_r*1000:.0f} mW/K (time constant {c_r/ha_r/60:.1f} min)')
    print('      dial lb | J per rep | W during set | J per session | rise one set (no cooling) | '
          'peak C, session')
    for lbx in REHAB_LB:
        wrep = lb_torque(lbx) * rom
        pw = wrep / REHAB_REP_S
        peak = run_session(pw, REHAB_REP_S, REHAB_REPS, REHAB_SETS, REHAB_REST_S, c_r, ha_r)
        print(f'      {lbx:>7.1f} | {wrep:>9.1f} | {pw:>12.1f} | {wrep*REHAB_REPS*REHAB_SETS:>13.0f} | '
              f'{wrep*REHAB_REPS/c_r:>21.1f} K | {peak:>6.1f}')
    wrep = lb_torque(5.0) * rom
    print(f'      5 lb, both directions braked in one dual-brake unit: peak '
          f'{run_session(2*wrep/REHAB_REP_S, REHAB_REP_S, REHAB_REPS, REHAB_SETS, REHAB_REST_S, c_r, ha_r):.1f} C')
    for mass in (0.060, 0.100):
        print(f'      5 lb, heat mass {mass*1000:.0f} g: peak '
              f'{run_session(wrep/REHAB_REP_S, REHAB_REP_S, REHAB_REPS, REHAB_SETS, REHAB_REST_S, mass*C_STEEL, ha_r):.1f} C')
    print(f'      gym, cable, {GYM_SETS} sets x {GYM_REPS} reps, {GYM_REST_S:.0f} s rest '
          f'(peak drum C; then steady state, back-to-back sets every {COMMERCIAL_DUTY:.0f} s)')
    print('      load | J per rep | W in set | ' + ' | '.join(f'{k:>18}' for k in GYM_DRUMS))
    for lbx in GYM_LB:
        wrep = lbx * LBF * STROKE
        pw = wrep / GYM_REP_S
        cells = []
        for k, (m, a) in GYM_DRUMS.items():
            pk = run_session(pw, GYM_REP_S, GYM_REPS, GYM_SETS, GYM_REST_S, m * C_STEEL, a * H_GYM)
            pavg = wrep * GYM_REPS / COMMERCIAL_DUTY
            ss = SKIN_START + pavg / (a * H_GYM)
            cells.append(f'{min(pk, 9999):>7.0f} / {min(ss, 9999):>6.0f} C')
        print(f'      {lbx:>4.0f} | {wrep:>9.0f} | {pw:>9.0f} | ' + ' | '.join(f'{c:>18}' for c in cells))
    for lbx in (100.0, 200.0):
        pavg = lbx * LBF * STROKE * GYM_REPS / COMMERCIAL_DUTY
        area = pavg / (H_GYM * (PAD_LIMIT - 60.0 - SKIN_START))
        print(f'      {lbx:.0f} lb back-to-back: {pavg:.0f} W average; to stay 60 K under the '
              f'{PAD_LIMIT:.0f} C pad ceiling needs {area:.3f} m2 of drum surface '
              f'(a {GYM_DRUM_R*2000:.0f} mm drum {area/(2*math.pi*GYM_DRUM_R*2)*1000:.0f} mm wide, '
              f'both faces)')
    print('  (c) friction drift. The dial sets preload, not torque; torque follows mu.')
    print('      actual mu | reading 5 lb gives | error | band-brake form gives')
    for mu in MU_RANGE:
        shoe = 5.0 * mu / MU_K
        e1 = math.exp(MU_K * math.radians(WRAP_DEG)) - 1
        e2 = math.exp(mu * math.radians(WRAP_DEG)) - 1
        print(f'      {mu:>9.2f} | {shoe:>18.2f} lb | {(shoe-5)*16:>+5.1f} oz | {5.0*e2/e1:>6.2f} lb')
    for lbx in (1.0, 2.0, 5.0):
        print(f'      at {lbx:.0f} lb, mu +/-15% means +/-{lbx*0.15*16:.1f} oz')
    for wear in (0.005, 0.010, 0.050, 0.100):
        print(f'      pad wear {wear*1000:>3.0f} um: preload falls {K_STACK*wear*1e-3:>4.0f} N = '
              f'{K_STACK*wear*1e-3/n5*100:>4.1f}% of the 5 lb setting')
    print('  (d) static vs kinetic friction')
    for lbx in (2.0, 5.0):
        brk = lbx * MU_S_RATIO
        dtq = lb_torque(lbx) * (MU_S_RATIO - 1)
        print(f'      dial {lbx:.0f} lb: breakaway {brk:.1f} lb, then a {dtq:.2f} N m drop -> forearm '
              f'jumps at {dtq/ARM_I:.0f} rad/s2 until the muscle adjusts '
              f'({math.degrees(0.5*dtq/ARM_I*0.05**2):.1f} deg in 50 ms)')

    # ---------------------------------------------------------------- 5
    print('\n5. MASS AND VOLUME')
    print(line)
    stack_kg = STACK_LB * LBF / G
    stack_l = STACK_ENVELOPE[0] * STACK_ENVELOPE[1] * STACK_ENVELOPE[2] * 1000
    unit_l = math.pi * (GYM_UNIT_ENVELOPE[0] / 2) ** 2 * GYM_UNIT_ENVELOPE[1] * 1000
    print(f'  {STACK_LB:.0f} lb weight stack: {stack_kg:.0f} kg of plates + {STACK_HARDWARE:.0f} kg hardware '
          f'= {stack_kg+STACK_HARDWARE:.0f} kg; envelope {stack_l:.0f} L')
    print(f'  gym tension unit: {GYM_UNIT_MASS:.0f} kg; envelope {unit_l:.1f} L -> '
          f'{(stack_kg+STACK_HARDWARE)/GYM_UNIT_MASS:.0f}x lighter, {stack_l/unit_l:.0f}x smaller')
    for lbx in (20.0, 50.0, 100.0, 200.0):
        kg = lbx * LBF / G
        print(f'  {lbx:>4.0f} lb as plates: {kg:>5.1f} kg, {kg/RHO_STEEL*1000:>5.1f} L of solid iron')
    print(f'  rehab: 5 lb dumbbell {5*LBF/G:.2f} kg vs brace with two units '
          f'{2*REHAB_UNIT_MASS+BRACE_REST_MASS:.2f} kg (the brace covers 0-5 lb both directions; '
          f'a dumbbell set covering 1-5 lb in 1 lb steps is {15*LBF/G:.1f} kg)')
    print('  orientation: a weight resists only upward motion; the unit resists its set direction '
          'in any posture, including lying down or with no gravity at all')


if __name__ == '__main__':
    main()
