# -*- coding: utf-8 -*-
"""
Back-of-envelope model behind the working paper
"The Nail That Carries No Battery: Passive Fingernail Displays Powered and
Driven Through Jewelry" (Benny Labs Foundation, 2026).

It estimates, for passive fingernail displays that are powered and driven over
a fine conductor from a body-worn hub (ring or bracelet):
  1. display area and pixel count on each fingernail at several pixel pitches,
  2. the power a nail display draws (emissive and e-paper),
  3. hours of use from a ring battery and a bracelet battery, per scenario,
  4. the same budget for a self-contained nail with its own battery and radio,
  5. resistive drop, loss and signal timing along the conductor,
  6. data rates: what the radio, the conductor and a daisy chain can carry,
  7. how many characters a marquee across four nails can show,
  8. warming of the nail surface, and the thickness and mass of the nail stack,
  9. a sensitivity table for the two least certain coefficients.

Every coefficient below is an assumption stated here. Nothing in this file is
a measurement, and no prototype exists. Standard library only. Run:
    python model.py
"""
import math

# ------------------------------------------------------------- assumptions
# Natural nail plate, visible part, adult hand: width x length (mm)
NAILS = {
    'thumb':  (15.0, 14.0),
    'index':  (12.0, 12.5),
    'middle': (12.5, 13.5),
    'ring':   (11.5, 12.5),
    'little': (9.5, 10.5),
}
SIDE_MARGIN = 0.6         # mm, inactive border on each side and at the tip
CONTACT_STRIP = 2.0       # mm, cuticle strip for contacts and driver
ROUNDING = 0.90           # share of the active rectangle left after rounded corners
EXTENSION = 4.0           # mm, extra length when the nail overhangs the tip

# Emissive display (micro-LED or OLED)
EFFICACY = 15.0           # lm/W, panel-level luminous efficacy, white
PANEL_STATIC = 0.4        # mW per raster nail panel: scan and driver overhead
# E-paper (electrophoretic)
EPD_UPDATE = 4.0          # mJ per cm2 per image update, driver included
EPD_UPDATE_TIME = 0.3     # s, fastest partial update
# Addressable pixels with an integrated control element (daisy chain)
LED_PITCH = 1.0           # mm, smallest common addressable-LED packages
LED_IDLE = 1.0            # mW per pixel quiescent (about 0.3 mA at 3.3 V)
CHAIN_BITRATE = 800e3     # bit/s, single-wire daisy-chain protocol
CHAIN_BITS = 24           # bits per pixel (8-bit red, green, blue)

# Hub electronics
HUB_STANDBY = 0.30        # mW: radio connected, motion sensor on wake, regulator
HUB_ACTIVE = 1.5          # mW extra while displays are on: controller rendering
RADIO_RATE = 1.0e6        # bit/s usable over Bluetooth Low Energy
SERIAL_RATE = 20e6        # bit/s, serial display interface over the conductor

# Batteries (Wh) and how much of the energy reaches the load
RING_WH = 0.15            # ring with a raised housing (about 40 mAh at 3.7 V)
BRACELET_WH = 1.5         # wrist band (about 400 mAh at 3.7 V)
NAIL_CELL_AREA = 1.0      # cm2, a thin cell that fits under a fingernail
NAIL_CELL_THICK = 0.35    # mm
NAIL_CELL_WHL = 150.0     # Wh/L, thin pouch cell, packaging included
USABLE = 0.85             # share of rated energy used before recharge
CONVERSION = 0.85         # regulator efficiency
SELF_RADIO_ACTIVE = 0.6   # mW extra for an on-nail controller while displaying
SELF_RADIO_STANDBY = 0.10 # mW on-nail radio kept connected

# Conductor
WIRE_D = 0.08             # mm, copper inner wire (about 40 AWG)
CU_RHO = 1.72e-8          # ohm m
RETURN_OHM_PER_M = 1.0    # chain body as return, link contacts included
RUN_THUMB = 0.04          # m, thumb ring to thumbnail
RUN_WRIST = 0.20          # m, wrist band to the farthest fingertip
SUPPLY_V = 3.3            # V at the hub end
SIGNAL_V = 0.6 * 3e8      # m/s in a fine insulated wire

# Use
DAY_HOURS = 16.0          # hours worn per day
SCENARIOS = {             # name: (luminance cd/m2, average picture level, share of time on)
    'glance':   (300.0, 0.35, 0.05),
    'ambient':  (150.0, 0.25, 1.00),
    'showcase': (600.0, 0.50, 1.00),
}
EPD_UPDATES_PER_H = 30    # image changes per nail per hour

# Marquee
VIEW_DIST = 350.0         # mm, wearer looking at own curled fingers
NAIL_GAP = 2.5            # mm, skin between adjacent nails
TEXT_H_RASTER = 6.0       # mm, letter height on a raster display
SCROLL_CPS = 8.0          # characters per second
COPLANAR_DEG = 15.0       # tolerance named in the specification

# Heat
H_OUT = 15.0              # W/m2K, still air convection plus radiation
R_STACK = 0.0035          # m2K/W, nail unit plus natural nail plate (0.7 mm, k 0.2)
H_TISSUE = 100.0          # W/m2K, perfused fingertip tissue below the plate

# Stacks (mm) and densities (g/cm3)
STACKS = {
    'gel overlay':            [('gel', 0.50)],
    'press-on nail':          [('nail body', 0.60)],
    'passive emissive nail':  [('adhesive', 0.10), ('nail body', 0.25),
                               ('flexible OLED and polarizer', 0.20), ('overcoat', 0.10)],
    'passive e-paper nail':   [('adhesive', 0.10), ('nail body', 0.25),
                               ('e-paper film and backplane', 0.25), ('overcoat', 0.10)],
    'self-contained nail':    [('adhesive', 0.10), ('nail body', 0.25),
                               ('flexible OLED and polarizer', 0.20), ('overcoat', 0.10),
                               ('battery', NAIL_CELL_THICK), ('radio, controller, antenna', 0.25)],
}
DENSITY = {'battery': 2.5, 'radio, controller, antenna': 1.8}
DEFAULT_DENSITY = 1.4


# ----------------------------------------------------------------- geometry
def active_area_mm2(w, l, extended=False):
    aw = w - 2 * SIDE_MARGIN
    al = l + (EXTENSION if extended else 0.0) - CONTACT_STRIP - SIDE_MARGIN
    return aw, al, aw * al * ROUNDING


def pixels(aw, al, pitch_mm):
    return int(aw / pitch_mm) * int(al / pitch_mm)


def pitch_from_ppi(ppi):
    return 25.4 / ppi


# -------------------------------------------------------------------- power
def emissive_mw(area_mm2, lum, apl):
    """Eq. (2): electrical power for a Lambertian emitter of mean luminance."""
    flux_lm = math.pi * lum * apl * area_mm2 * 1e-6
    return flux_lm / EFFICACY * 1000.0


def nail_mw(area_mm2, lum, apl):
    return emissive_mw(area_mm2, lum, apl) + PANEL_STATIC


def epd_mw(area_mm2, updates_per_h=EPD_UPDATES_PER_H):
    return EPD_UPDATE * area_mm2 / 100.0 * updates_per_h / 3600.0


def hub_avg_mw(nail_powers, duty):
    return HUB_STANDBY + duty * (HUB_ACTIVE + sum(nail_powers))


def hours(wh, mw):
    return wh * USABLE * CONVERSION * 1000.0 / mw


def nail_cell_wh():
    return NAIL_CELL_AREA * NAIL_CELL_THICK / 10.0 * NAIL_CELL_WHL / 1000.0


# ---------------------------------------------------------------- conductor
def wire_ohm_per_m(d_mm=WIRE_D):
    return CU_RHO / (math.pi * (d_mm * 1e-3) ** 2 / 4)


def loop_ohm(run_m):
    return run_m * (wire_ohm_per_m() + RETURN_OHM_PER_M)


def critical_length(rise_s):
    """Rule of thumb: a line is electrically short below 1/6 of the rise distance."""
    return rise_s * SIGNAL_V / 6.0


# -------------------------------------------------------------------- heat
def surface_rise(mw, area_mm2):
    q = mw * 1e-3 / (area_mm2 * 1e-6)
    h_in = 1.0 / (R_STACK + 1.0 / H_TISSUE)
    return q / (H_OUT + h_in), q


def main():
    global EFFICACY, LED_IDLE
    line = '-' * 78
    areas = {}
    print('ASSUMPTIONS')
    print(f'  efficacy {EFFICACY:.0f} lm/W | panel overhead {PANEL_STATIC} mW | e-paper {EPD_UPDATE} mJ/cm2'
          f' | hub standby {HUB_STANDBY} mW, active +{HUB_ACTIVE} mW')
    print(f'  ring {RING_WH} Wh | bracelet {BRACELET_WH} Wh | usable x conversion '
          f'{USABLE*CONVERSION:.2f} | day {DAY_HOURS:.0f} h worn')

    print('\n1. DISPLAY AREA AND PIXELS PER NAIL')
    print(line)
    print('  nail    plate mm   active mm    area mm2 | 1 mm pixels | 150 ppi | 300 ppi | 300 ppi extended')
    tot = {'led': 0, 150: 0, 300: 0}
    for name, (w, l) in NAILS.items():
        aw, al, a = active_area_mm2(w, l)
        areas[name] = a
        ewa, eal, ea = active_area_mm2(w, l, True)
        led = pixels(aw, al, LED_PITCH)
        p150 = pixels(aw, al, pitch_from_ppi(150))
        p300 = pixels(aw, al, pitch_from_ppi(300))
        p300e = pixels(ewa, eal, pitch_from_ppi(300))
        tot['led'] += led; tot[150] += p150; tot[300] += p300
        print(f'  {name:<7} {w:>4.1f}x{l:<4.1f} {aw:>5.1f}x{al:<5.1f} {a:>8.0f} | {led:>11} | '
              f'{p150:>7,} | {p300:>7,} | {p300e:>7,}')
    hand_area = sum(areas.values())
    print(f'  one hand: active area {hand_area:.0f} mm2 ({hand_area/100:.2f} cm2); pixels '
          f'{tot["led"]} | {tot[150]:,} | {tot[300]:,}')
    print(f'  thumb active area is {areas["thumb"]/areas["little"]:.1f}x the little finger')

    print('\n2. POWER PER NAIL DISPLAY (mW)')
    print(line)
    for name in ('thumb', 'index', 'little'):
        a = areas[name]
        row = ' | '.join(f'{s} {nail_mw(a, L, apl):.2f}' for s, (L, apl, _) in SCENARIOS.items())
        print(f'  {name:<7} emissive while on: {row} | e-paper {epd_mw(a)*1000:.1f} uW at '
              f'{EPD_UPDATES_PER_H}/h')
    print(f'  thumb, full white 1000 cd/m2: {nail_mw(areas["thumb"], 1000, 1.0):.1f} mW')
    print(f'  one e-paper update of the thumb: {EPD_UPDATE*areas["thumb"]/100:.1f} mJ')
    ratios = [nail_mw(a, *SCENARIOS['ambient'][:2]) / epd_mw(a) for a in areas.values()]
    print(f'  emissive ambient / e-paper at {EPD_UPDATES_PER_H}/h: {min(ratios):.0f}x to {max(ratios):.0f}x')

    print('\n3. HOURS OF USE FROM THE HUB BATTERY (one hand)')
    print(line)
    thumb = [areas['thumb']]
    hand = list(areas.values())
    configs = [('thumb ring, 1 nail', RING_WH, thumb),
               ('ring hub, 5 nails', RING_WH, hand),
               ('bracelet, 5 nails', BRACELET_WH, hand)]
    print('  config               |  glance (5% on)  |  ambient (always on) | showcase (always on) | e-paper 30/h')
    results = {}
    for cname, wh, alist in configs:
        cells = []
        for s, (L, apl, duty) in SCENARIOS.items():
            p = hub_avg_mw([nail_mw(a, L, apl) for a in alist], duty)
            h = hours(wh, p)
            results[(cname, s)] = (p, h)
            cells.append(f'{p:>5.2f} mW {h:>6.0f} h')
        pe = HUB_STANDBY + sum(epd_mw(a) for a in alist) + HUB_ACTIVE * (
            len(alist) * EPD_UPDATES_PER_H * 2.0 / 3600.0)
        he = hours(wh, pe)
        results[(cname, 'epd')] = (pe, he)
        cells.append(f'{pe:>5.2f} mW {he/24:>4.0f} d')
        print(f'  {cname:<20} | ' + ' | '.join(cells))
    print(f'  (e-paper: controller active 2 s per update; days are 24 h days)')
    for cname, wh, alist in configs:
        p, h = results[(cname, 'ambient')]
        print(f'  {cname:<20} ambient: {h/DAY_HOURS:.1f} worn days per charge; showcase '
              f'{results[(cname, "showcase")][1]:.1f} h; glance {results[(cname, "glance")][1]/DAY_HOURS:.1f} worn days')
    led_px = sum(pixels(active_area_mm2(w, l)[0], active_area_mm2(w, l)[1], LED_PITCH)
                 for w, l in NAILS.values())
    L, apl, _ = SCENARIOS['ambient']
    p_led = HUB_STANDBY + HUB_ACTIVE + led_px * LED_IDLE + sum(emissive_mw(a, L, apl) for a in hand)
    light = sum(emissive_mw(a, L, apl) for a in hand)
    print(f'  daisy chain: idle draw is {led_px*LED_IDLE/light:.0f}x the {light:.1f} mW spent on light')
    print(f'  daisy chain of {led_px} addressable pixels, ambient: idle {led_px*LED_IDLE:.0f} mW, '
          f'total {p_led:.0f} mW -> bracelet {hours(BRACELET_WH, p_led):.1f} h, ring '
          f'{hours(RING_WH, p_led):.1f} h')

    print('\n4. SELF-CONTAINED NAIL (battery and radio on the nail), thumb')
    print(line)
    cell = nail_cell_wh()
    print(f'  cell {NAIL_CELL_AREA} cm2 x {NAIL_CELL_THICK} mm at {NAIL_CELL_WHL:.0f} Wh/L = '
          f'{cell*1000:.2f} mWh; ring holds {RING_WH/cell:.0f}x, bracelet {BRACELET_WH/cell:.0f}x')
    a = areas['thumb']
    for s, (L, apl, duty) in SCENARIOS.items():
        p = SELF_RADIO_STANDBY + duty * (SELF_RADIO_ACTIVE + nail_mw(a, L, apl))
        print(f'  {s:<9} {p:>5.2f} mW -> {hours(cell, p):>5.1f} h')
    pe = SELF_RADIO_STANDBY + epd_mw(a) + SELF_RADIO_ACTIVE * EPD_UPDATES_PER_H * 2.0 / 3600.0
    print(f'  e-paper   {pe:>5.3f} mW -> {hours(cell, pe):>5.0f} h ({hours(cell, pe)/24:.1f} days)')
    print('  ten such nails mean ten cells to charge; the passive nail has none')

    print('\n5. CONDUCTOR: DROP, LOSS AND TIMING')
    print(line)
    print(f'  inner wire {WIRE_D} mm copper: {wire_ohm_per_m():.2f} ohm/m; return {RETURN_OHM_PER_M} ohm/m')
    cases = [('thumb, glance', RUN_THUMB, nail_mw(areas['thumb'], 300, 0.35)),
             ('thumb, full white 1000 cd/m2', RUN_THUMB, nail_mw(areas['thumb'], 1000, 1.0)),
             ('wrist to fingertip, showcase', RUN_WRIST, nail_mw(areas['middle'], 600, 0.5)),
             ('wrist, five nails on one pair, full white', RUN_WRIST,
              sum(nail_mw(x, 1000, 1.0) for x in hand))]
    for name, run, mw in cases:
        i = mw / 1000.0 / SUPPLY_V
        r = loop_ohm(run)
        dv = i * r
        loss = i * i * r * 1000.0
        print(f'  {name:<42} {mw:>6.1f} mW {i*1000:>5.1f} mA | loop {r:.3f} ohm | drop '
              f'{dv*1000:>5.1f} mV | loss {loss/mw*100:.2f}%')
    for tr in (2e-9, 5e-9, 20e-9):
        print(f'  edge {tr*1e9:>4.0f} ns: electrically short below {critical_length(tr)*100:>5.1f} cm')

    print('\n6. DATA RATES')
    print(line)
    for ppi in (150, 300):
        pt = pixels(*active_area_mm2(*NAILS['thumb'])[:2], pitch_from_ppi(ppi))
        ph = sum(pixels(*active_area_mm2(w, l)[:2], pitch_from_ppi(ppi)) for w, l in NAILS.values())
        for fps in (30,):
            rt = pt * 16 * fps
            rh = ph * 16 * fps
            print(f'  {ppi} ppi, 16-bit, {fps} fps: thumb {rt/1e6:.1f} Mbit/s '
                  f'({rt/SERIAL_RATE*100:.0f}% of its own {SERIAL_RATE/1e6:.0f} Mbit/s conductor link), '
                  f'hand {rh/1e6:.1f} Mbit/s = {rh/RADIO_RATE:.0f}x the radio')
    max_px = CHAIN_BITRATE / CHAIN_BITS / 30
    print(f'  daisy chain at {CHAIN_BITRATE/1e3:.0f} kbit/s, {CHAIN_BITS} bit: {max_px:.0f} pixels at 30 fps; '
          f'hand of 1 mm pixels {led_px} -> {CHAIN_BITRATE/CHAIN_BITS/led_px:.0f} fps')
    still = sum(pixels(*active_area_mm2(w, l)[:2], pitch_from_ppi(300)) for w, l in NAILS.values()) * 16
    print(f'  one 300 ppi still image for the hand: {still/8/1024:.0f} KiB, {still/RADIO_RATE:.1f} s over the radio')

    print('\n7. MARQUEE ACROSS FOUR NAILS (index to little)')
    print(line)
    fingers = ['index', 'middle', 'ring', 'little']
    widths = [NAILS[f][0] - 2 * SIDE_MARGIN for f in fingers]
    lens = [active_area_mm2(*NAILS[f])[1] for f in fingers]
    lit = sum(widths)
    blank = 3 * (NAIL_GAP + 2 * SIDE_MARGIN)
    span = lit + blank
    print(f'  lit width {lit:.1f} mm, blank {blank:.1f} mm, span {span:.1f} mm '
          f'({blank/span*100:.0f}% of the span is dark); shortest active length {min(lens):.1f} mm')
    for label, h, adv in (('1 mm addressable pixels, 5x7 font', 7 * LED_PITCH, 6 * LED_PITCH),
                          ('raster, 6 mm letters', TEXT_H_RASTER, TEXT_H_RASTER * 6 / 7)):
        chars = span / adv
        ang = math.degrees(2 * math.atan(h / 2 / VIEW_DIST))
        print(f'  {label:<34} letter {h:.1f} mm ({ang:.2f} deg at {VIEW_DIST/10:.0f} cm), '
              f'{chars:.1f} characters across, {chars/SCROLL_CPS:.1f} s on view each at {SCROLL_CPS:.0f}/s')
    print(f'  e-paper at {EPD_UPDATE_TIME} s per update: {1/EPD_UPDATE_TIME:.1f} steps/s; at '
          f'{SCROLL_CPS:.0f} char/s each step jumps {SCROLL_CPS*EPD_UPDATE_TIME:.1f} characters')
    print(f'  nails tilted {COPLANAR_DEG:.0f} deg from the common plane look '
          f'{(1-math.cos(math.radians(COPLANAR_DEG)))*100:.1f}% narrower')

    print('\n8. HEAT AT THE NAIL AND THE STACK')
    print(line)
    a = areas['thumb']
    for name, mw in (('glance 300 cd/m2', nail_mw(a, 300, 0.35)),
                     ('showcase 600 cd/m2', nail_mw(a, 600, 0.5)),
                     ('full white 1000 cd/m2', nail_mw(a, 1000, 1.0))):
        dt, q = surface_rise(mw, a)
        print(f'  thumb {name:<22} {mw:>5.1f} mW, {q:>5.0f} W/m2 -> {dt:.2f} C warmer')
    full = NAILS['thumb'][0] * NAILS['thumb'][1] * ROUNDING / 100.0   # cm2
    for sname, layers in STACKS.items():
        t = sum(x for _, x in layers)
        m = 0.0
        for lname, x in layers:
            area = NAIL_CELL_AREA if lname == 'battery' else full
            m += area * x / 10.0 * DENSITY.get(lname, DEFAULT_DENSITY)
        print(f'  {sname:<23} {t:.2f} mm, thumb {m:.2f} g')

    print('\n9. SENSITIVITY: bracelet, five nails always on (hours)')
    print(line)
    print('  efficacy  ' + ''.join(f'{L:>8.0f} cd/m2' for L in (150, 300, 600)))
    keep = EFFICACY
    for eff in (8.0, 15.0, 30.0):
        EFFICACY = eff
        row = ''
        for L in (150, 300, 600):
            p = hub_avg_mw([nail_mw(x, L, 0.35) for x in hand], 1.0)
            row += f'{hours(BRACELET_WH, p):>14.0f}'
        print(f'  {eff:>4.0f} lm/W {row}')
    EFFICACY = keep
    print('  (average picture level 0.35)')
    print('\n  daisy chain of 1 mm pixels, bracelet, ambient: idle draw per pixel (hours)')
    keep_led = LED_IDLE
    for idle in (0.1, 0.3, 1.0, 3.0):
        LED_IDLE = idle
        p = HUB_STANDBY + HUB_ACTIVE + led_px * LED_IDLE + sum(emissive_mw(x, 150, 0.25) for x in hand)
        print(f'    {idle:>3} mW per pixel: {p:>6.0f} mW -> {hours(BRACELET_WH, p):>5.1f} h')
    LED_IDLE = keep_led
    print('\n  ring hub, five nails, share of time on (glance duty) at 300 cd/m2, APL 0.35 (hours)')
    for duty in (0.02, 0.05, 0.10, 0.25, 0.50, 1.0):
        p = hub_avg_mw([nail_mw(x, 300, 0.35) for x in hand], duty)
        print(f'    {duty*100:>3.0f}% on: {p:>5.2f} mW -> {hours(RING_WH, p):>5.0f} h')


if __name__ == '__main__':
    main()
