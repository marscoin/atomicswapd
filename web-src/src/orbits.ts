// Earth and Mars positions from JPL's "Approximate Positions of the
// Planets" (E. M. Standish), Table 1, valid 1800-2050. Good to a fraction
// of a percent in distance, which is plenty for light time.

type El = [number, number][]  // [value at J2000, rate per century] for a, e, I, L, varpi, Omega

const EARTH: El = [[1.00000261, 0.00000562], [0.01671123, -0.00004392], [-0.00001531, -0.01294668],
  [100.46457166, 35999.37244981], [102.93768193, 0.32327364], [0, 0]]
const MARS: El = [[1.52371034, 0.00001847], [0.0933941, 0.00007882], [1.84969142, -0.00813131],
  [-4.55343205, 19140.30268499], [-23.94362959, 0.44441088], [49.55953891, -0.29257343]]

const AU_LIGHT_SEC = 499.004784
const AU_KM = 149_597_870.7
const rad = (d: number) => (d * Math.PI) / 180

function position(el: El, T: number): [number, number, number] {
  const [a, e, I, L, wBar, Om] = el.map(([v, r]) => v + r * T)
  const w = wBar - Om
  const M = rad((((L - wBar + 180) % 360) + 360) % 360 - 180)
  let E = M + e * Math.sin(M)
  for (let i = 0; i < 12; i++) E -= (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E))
  const xp = a * (Math.cos(E) - e)
  const yp = a * Math.sqrt(1 - e * e) * Math.sin(E)
  const [cw, sw, cO, sO, cI, sI] = [Math.cos(rad(w)), Math.sin(rad(w)), Math.cos(rad(Om)),
    Math.sin(rad(Om)), Math.cos(rad(I)), Math.sin(rad(I))]
  return [
    (cw * cO - sw * sO * cI) * xp + (-sw * cO - cw * sO * cI) * yp,
    (cw * sO + sw * cO * cI) * xp + (-sw * sO + cw * cO * cI) * yp,
    sw * sI * xp + cw * sI * yp,
  ]
}

export interface Orbits { earth: [number, number, number]; mars: [number, number, number]; au: number; km: number; lightSec: number }

export function orbits(unixSec: number): Orbits {
  const T = (unixSec / 86400 + 2440587.5 - 2451545.0) / 36525
  const earth = position(EARTH, T)
  const mars = position(MARS, T)
  const au = Math.hypot(earth[0] - mars[0], earth[1] - mars[1], earth[2] - mars[2])
  return { earth, mars, au, km: au * AU_KM, lightSec: au * AU_LIGHT_SEC }
}
