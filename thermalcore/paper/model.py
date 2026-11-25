# -*- coding: utf-8 -*-
"""
Lumped-parameter model behind the working paper
"The Heating Element That Computes" (Benny Labs Foundation, 2026).

It estimates, for an immersion computing module in a storage water heater:
  1. the share of a household's daily water-heating energy a module can supply,
  2. the temperature steps along the heat path  die -> coupling -> wall -> water,
  3. the tank warm-up time at a given continuous power.

Every coefficient is an assumption stated here. Nothing in this file is a
measurement. Run:  python model.py
"""
import math

G = 9.81

# ---------------------------------------------------------------- geometry
D_OUT = 0.0254      # enclosure outer diameter, m (passes a nominal 1 in. NPT port)
WALL = 0.00089      # wall thickness, m (0.035 in.)
D_IN = D_OUT - 2 * WALL
K_WALL = 15.0       # 316L stainless steel, W/m.K
IN2_PER_M2 = 1550.0


def area_out(length):
    return math.pi * D_OUT * length


def area_in(length):
    return math.pi * D_IN * length


# -------------------------------------------------------- water properties
def water(t):
    """Approximate liquid-water properties, 20-90 C."""
    return dict(
        k=0.598 + 0.0011 * (t - 20),
        nu=1.004e-6 * math.exp(-0.025 * (t - 20)),
        pr=7.0 * math.exp(-0.028 * (t - 20)),
        beta=2.07e-4 + 4.5e-6 * (t - 20),
    )


def h_natural(t_wall, t_water):
    """Churchill-Chu (1975), free convection from a horizontal cylinder."""
    p = water(0.5 * (t_wall + t_water))
    dt = max(t_wall - t_water, 0.05)
    ra = G * p['beta'] * dt * D_OUT ** 3 / p['nu'] ** 2 * p['pr']
    nu = (0.60 + 0.387 * ra ** (1 / 6) /
          (1 + (0.559 / p['pr']) ** (9 / 16)) ** (8 / 27)) ** 2
    return nu * p['k'] / D_OUT


def h_forced(t_wall, t_water, v):
    """Churchill-Bernstein (1977), cross-flow over a cylinder."""
    p = water(0.5 * (t_wall + t_water))
    re = v * D_OUT / p['nu']
    nu = 0.3 + (0.62 * re ** 0.5 * p['pr'] ** (1 / 3)) / \
        (1 + (0.4 / p['pr']) ** (2 / 3)) ** 0.25 * \
        (1 + (re / 282000) ** (5 / 8)) ** (4 / 5)
    return nu * p['k'] / D_OUT


def water_film_step(power, length, t_water, fin_factor=1.0, velocity=0.0):
    """Temperature rise of the enclosure wall above the tank water."""
    a = area_out(length) * fin_factor
    t_wall = t_water + 5.0
    for _ in range(300):
        h = h_forced(t_wall, t_water, velocity) if velocity > 0 \
            else h_natural(t_wall, t_water)
        t_wall += 0.3 * (t_water + power / (h * a) - t_wall)
    return t_wall - t_water, h


def wall_step(power, length):
    return power * math.log(D_OUT / D_IN) / (2 * math.pi * K_WALL * length)


# ------------------------------------------------- internal coupling options
# (name, description, function giving thermal resistance in K/W for a length)
def r_still_liquid(length, h=150.0, sink_area_per_m=0.13):
    """Dies -> still dielectric liquid -> inner wall (two films in series)."""
    return 1 / (h * sink_area_per_m * length) + 1 / (h * area_in(length))


def r_moved_liquid(length, h=600.0, sink_area_per_m=0.13):
    """Same path with the dielectric liquid gently circulated."""
    return 1 / (h * sink_area_per_m * length) + 1 / (h * area_in(length))


def r_conduction(length, h_contact=3000.0, contact_fraction=0.5):
    """Dies on a metal spreader pressed or potted against the wall."""
    return 1 / (h_contact * contact_fraction * area_in(length))


COUPLINGS = [
    ('Still dielectric liquid', r_still_liquid),
    ('Circulated dielectric liquid', r_moved_liquid),
    ('Conduction (spreader or potting)', r_conduction),
]

R_JC = 0.8          # junction-to-case, K/W per die
P_DIE = 15.0        # W per die


def junction(power, length, t_water, r_fn, fin_factor=1.0, velocity=0.0):
    d_water, _ = water_film_step(power, length, t_water, fin_factor, velocity)
    d_wall = wall_step(power, length)
    d_coup = power * r_fn(length)
    d_die = P_DIE * R_JC
    return dict(d_water=d_water, d_wall=d_wall, d_coup=d_coup, d_die=d_die,
                tj=t_water + d_water + d_wall + d_coup + d_die)


def max_power(length, t_water, r_fn, tj_limit, fin_factor=1.0, velocity=0.0):
    best = 0
    for p in range(10, 6001, 10):
        if junction(p, length, t_water, r_fn, fin_factor, velocity)['tj'] > tj_limit:
            break
        best = p
    return best


# ---------------------------------------------------------------- tank
def warm_up(power, litres=189.0, t_start=15.0, t_room=20.0, ua=1.1):
    """Hours to reach 50 C and 60 C with no draw; steady-state temperature."""
    c = litres * 0.985 * 4184.0
    t_ss = t_room + power / ua
    out = {}
    for target in (50.0, 60.0):
        if t_ss <= target:
            out[target] = None
        else:
            out[target] = c / ua * math.log((t_ss - t_start) / (t_ss - target)) / 3600
    return out, t_ss


def main():
    line = '-' * 78
    L = 0.30
    print('GEOMETRY')
    print(f'  enclosure {D_OUT*1000:.1f} mm OD x {L*100:.0f} cm, wetted area '
          f'{area_out(L)*IN2_PER_M2:.1f} in2 ({area_out(L)*1e4:.0f} cm2)')
    print(f'  power at 40 W/in2 ceiling: {40*area_out(L)*IN2_PER_M2:.0f} W')

    print('\n1. SHARE OF DAILY HEAT SUPPLIED BY COMPUTATION  f = min(1, 24 P / E)')
    print(line)
    demands = (6, 9, 12, 15)
    print('  P (W)   ' + ''.join(f'E={e:>2} kWh   ' for e in demands) + ' duty of a 4,500 W element')
    for p in (300, 500, 800, 1500):
        row = ''.join(f'{min(1, 24*p/1000/e)*100:>6.0f}%     ' for e in demands)
        print(f'  {p:>5}   {row}')
    print('  4,500 W element duty: ' +
          ', '.join(f'{e/(24*4.5)*100:.0f}% at {e} kWh' for e in demands))
    print('  module duty when it carries the whole load (500 W): ' +
          ', '.join(f'{min(1, e/(24*0.5))*100:.0f}% at {e} kWh' for e in demands))

    print('\n2. WATER-SIDE FILM (rigid module, natural convection)')
    print(line)
    for tw in (50, 60):
        for p in (300, 500, 800):
            d, h = water_film_step(p, L, tw)
            d2, _ = water_film_step(p, L, tw, fin_factor=2.5)
            d3, h3 = water_film_step(p, L, tw, velocity=0.3)
            print(f'  tank {tw} C, {p:>3} W: bare {d:>5.1f} K (h={h:>4.0f}) | '
                  f'fins x2.5 {d2:>5.1f} K | forced 0.3 m/s {d3:>4.1f} K (h={h3:>4.0f}) | '
                  f'flux {p/(area_out(L)*IN2_PER_M2):.1f} W/in2')

    print('\n3. TEMPERATURE STEPS AT 500 W, 60 C TANK, FINNED (x2.5)')
    print(line)
    for name, fn in COUPLINGS:
        r = junction(500, L, 60, fn, fin_factor=2.5)
        print(f'  {name:<34} water {r["d_water"]:>5.1f} | wall {r["d_wall"]:>4.1f} | '
              f'coupling {r["d_coup"]:>6.1f} | die {r["d_die"]:>4.1f} | Tj {r["tj"]:>6.1f} C')

    print('\n4. MAXIMUM CONTINUOUS POWER (W) FOR A 30 cm RIGID MODULE')
    print(line)
    print(f'  {"coupling":<34} {"exterior":<22} {"50C/Tj90":>9} {"60C/Tj90":>9} {"60C/Tj105":>10}')
    for name, fn in COUPLINGS:
        for ext, ff, v in (('bare, still water', 1.0, 0.0),
                           ('fins x2.5', 2.5, 0.0),
                           ('impeller 0.3 m/s', 1.0, 0.3)):
            a = max_power(L, 50, fn, 90, ff, v)
            b = max_power(L, 60, fn, 90, ff, v)
            c = max_power(L, 60, fn, 105, ff, v)
            print(f'  {name:<34} {ext:<22} {a:>9} {b:>9} {c:>10}')

    print('\n5. FLEXIBLE CONDUIT: POWER SCALES WITH IMMERSED LENGTH (conduction, bare, 60 C, Tj 90)')
    print(line)
    for length in (0.3, 0.6, 1.2, 2.4, 4.8):
        p = max_power(length, 60, r_conduction, 90)
        print(f'  {length:>4.1f} m ({length/0.0254:>4.0f} in.): {p:>5} W, '
              f'{p/(area_out(length)*IN2_PER_M2):.1f} W/in2')

    print('\n6. TANK WARM-UP, 189 L (50 gal), from 15 C, no draw, UA = 1.1 W/K')
    print(line)
    for p in (150, 300, 500, 800, 1500, 4500):
        t, tss = warm_up(p)
        f = lambda x: 'never' if x is None else f'{x:.1f} h'
        print(f'  {p:>5} W: 50 C in {f(t[50.0]):>7} | 60 C in {f(t[60.0]):>7} | '
              f'steady state {min(tss, 999):.0f} C')


if __name__ == '__main__':
    main()
