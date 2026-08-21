import numpy as np
import math
from scipy.special import jn, jn_zeros

NUM_OF_BESSEL_ROOTS = 3


def calculate_gain_1_2(off_axis,
                       Gmax,
                       near_lobe_level,
                       HPBW,
                       z=1,
                       far_lobe_level=0):
    """
    Calculate the antenna gain for given off-axis angles
    using the alternative S.1528 model. (from SHARC)

    Parameters
    ----------
    off_axis
        - off_axis: off-axis angles (degrees)

    Returns
    -------
    np.array
        Calculated gain values for the given angles.
    """

    # Antenna parameters:
    back_lobe_level = np.maximum(
            0, 15 + near_lobe_level + 0.25 *
            Gmax + 5 * np.log10(z),
        )

    psi_b = HPBW/2

    if near_lobe_level == -15:
        a = 2.58 * math.sqrt(1 - 1.4 * np.log10(z))
    elif near_lobe_level == -20:
        a = 2.58 * math.sqrt(1 - 1.0 * np.log10(z))
    elif near_lobe_level == -25:
        a = 2.58 * math.sqrt(1 - 0.6 * np.log10(z))
    elif near_lobe_level == -30:
        a = 2.58 * math.sqrt(1 - 0.4 * np.log10(z))
    else:
        # ValueError, not sys.exit(1): this runs inside multiprocessing Pool
        # workers, where a process kill would silently hang the whole run.
        raise ValueError(
            f"Invalid near lobe parameter: {near_lobe_level} "
            "(S.1528 rec 1.2 defines LN = -15/-20/-25/-30 dB only)"
        )

    b = 6.32

    alpha = 1.5

    x = Gmax + near_lobe_level + \
        25 * np.log10(b * psi_b)
    y = b * psi_b * \
        math.pow(10, 0.04 * (Gmax + near_lobe_level - far_lobe_level))

    # Gain:
    psi = np.absolute(off_axis)

    gain = np.zeros(len(psi))

    idx_0 = np.where(psi < a * psi_b)[0]
    gain[idx_0] = Gmax - 3 * \
        np.power(psi[idx_0] / psi_b, alpha)

    idx_1 = np.where((a * psi_b < psi) &
                     (psi <= 0.5 * b * psi_b))[0]
    gain[idx_1] = Gmax + near_lobe_level + 20 * np.log10(z)

    idx_2 = np.where((0.5 * b * psi_b < psi) &
                     (psi <= b * psi_b))[0]
    gain[idx_2] = Gmax + near_lobe_level

    idx_3 = np.where((b * psi_b < psi) & (psi <= y))[0]
    gain[idx_3] = x - 25 * np.log10(psi[idx_3])

    idx_4 = np.where((y < psi) & (psi <= 90))[0]
    gain[idx_4] = far_lobe_level

    idx_5 = np.where((90 < psi) & (psi <= 180))[0]
    gain[idx_5] = back_lobe_level

    return gain


def calculate_gain_1_4(off_axis,
                       theta,
                       Gmax,
                       f_GHz,
                       n_sidelobes,
                       slr,
                       l_r,
                       l_t):
    """
    Calculate the antenna gain for given off-axis
    and theta angles using the S.1528 model.

    Parameters
    ----------
    *args : tuple
        Positional arguments (unused).
    **kwargs : dict
        Keyword arguments containing:
            - off_axis: off-axis angles (degrees)
            - theta: theta angles (degrees)

    Returns
    -------
    np.array
        Calculated gain values for the given angles.
    """
    # The reference angles for the simulator and the antenna realisation are
    # switched: local theta is the simulator's off_axis angle, local phi is
    # the simulator's theta. NOTE the original assignment order shadowed the
    # `theta` parameter before phi was derived, so phi effectively came from
    # off_axis (double-radians). With a circular aperture (l_r == l_t, the
    # only case exercised) phi cancels out of `u`, so results are unchanged;
    # fixed here so an elliptical aperture would be computed correctly.
    phi = np.abs(np.radians(np.asarray(theta, dtype=np.float64)))
    theta = np.abs(np.radians(np.asarray(off_axis, dtype=np.float64)))

    lamb = 299_792_458 / (f_GHz*1e9)

    mu = jn_zeros(1, NUM_OF_BESSEL_ROOTS) / np.pi

    u = (np.pi / lamb) * np.sqrt((l_r * np.sin(theta) * \
         np.cos(phi)) ** 2 + (l_t * np.sin(theta) * np.sin(phi)) ** 2)

    A = (1 / np.pi) * np.arccosh(10 ** (slr / 20))
    j1_roots = jn_zeros(1, n_sidelobes) / np.pi
    sigma = j1_roots[-1] / \
            np.sqrt(A ** 2 + (n_sidelobes - 1 / 2) ** 2)

    v = np.ones(u.shape + (NUM_OF_BESSEL_ROOTS,))

    for i, ui in enumerate(mu):
        v[..., i] = (1 - u ** 2 / (np.pi ** 2 * sigma ** 2 * (A **
                     2 + (i + 1 - 0.5) ** 2))) / (1 - (u / (np.pi * ui)) ** 2)

    # Take care of divide-by-zero
    with np.errstate(divide='ignore', invalid='ignore'):
        gain = Gmax + 20 * \
            np.log10(np.abs((2 * jn(1, u) / u) * np.prod(v, axis=-1)))

    # Replace undefined values with -inf (or other desired value)
    gain = np.nan_to_num(gain, nan=-np.inf)
    # Replace values that were substituded specifically
    # because u == 0 with Lim (gain)_(u -> 0) = peak_gain
    gain[u == 0] = Gmax

    return gain
