"""
ES_deployer.py — Deploys Earth stations (ESs) based on a population map.
Source of candidates for valid links in the computation of sidelobe EPFD.

This script is isolated and should be executed by itslef to generate the
accepted_ESs.csv file.
"""

import math
import csv
import numpy as np

scale = 20
R = 6371.0  # Raio da Terra (km)


def deploy_ESs(scale, pop_map_path, N, MIN_DISTANCE):
    """
    Deploys Earth Stations (ESs) based on population density
    and co-frequency beam spacing using a greedy algorithm.

    Inputs:
        • scale: decimation factor for the .tif map
        • pop_map_path: path to the .tif population map
        • N: number of possible earth stations for the greedy algorithm
        • MIN_DISTANCE: distance between co-frequency beams in km
                            (20 - 50 km for realistic cenarios)
    """
    # Lazy imports: rasterio/matplotlib are NOT runtime dependencies of the
    # engine — the EPFD path only uses generate_es_grid below. This keeps
    # `import src.ES_deployer` working in the app venv, where rasterio is
    # not installed; run this file standalone to regenerate the CSV.
    import rasterio
    import matplotlib.pyplot as plt
    from rasterio.transform import Affine

    with rasterio.open(pop_map_path) as src:
        data = src.read(1).astype(np.float64)
        data = data[::scale, ::scale]
        nodata = src.nodata
        if nodata is not None:
            data[data == nodata] = 0.0
        data = np.nan_to_num(data)
        data /= data.max()
        transform = src.transform * Affine.scale(scale)

    # Plota densidade populacional
    plt.figure(figsize=(12, 6))
    plt.imshow(data, cmap="viridis", origin="upper")
    plt.tight_layout()
    plt.savefig("src/visualization/population_map.png")

    print(type(data))
    print(data.shape)

    # Distribui 1.000.000 ESs baseado na densidade populacional
    weights = data.ravel()
    weights = weights.astype(np.float64)
    weights /= weights.sum()  # weights.sum() = 1 (distribuição de probab)

    N = 1_000_000

    ES_indices = np.random.choice(len(weights),
                                  size=N,
                                  replace=True,
                                  p=weights)
    rows, cols = np.unravel_index(ES_indices, data.shape)  # Arrays de len N
    lon = transform.c + (cols + 0.5) * transform.a
    lat = transform.f + (rows + 0.5) * transform.e
    ES_candidates_positions = np.column_stack((lat, lon))

    # Algoritmo guloso para selecionar ESs com espaçamento mínimo de 18.53 km
    CELL_KM = 10.0

    # Embaralhar candidatos
    perm = np.random.permutation(len(ES_candidates_positions))
    ES_candidates_positions = ES_candidates_positions[perm]

    # Distância entre células (Haversine km)
    def haversine(lat1, lon1, lat2, lon2):
        lat1, lon1, lat2, lon2 = np.radians([lat1, lon1, lat2, lon2])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = np.sin(dlat/2.0)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(dlon/2.0)**2

        return 2.0 * R * np.arcsin(np.sqrt(a))

    # Altura da célula em latitude (cte)
    lat_step = CELL_KM / 111.32

    # key = (lat_cell, lon_cell)
    # val = índices aceitos
    grid = {}

    accepted = np.empty_like(ES_candidates_positions)
    n_accepted = 0

    for lat, lon in ES_candidates_positions:

        # Tamanho da célula de latitude
        lat_cell = int(np.floor(lat / lat_step))
        # Latitude da célula avaliado no meio da faixa de latitude
        cell_lat = (lat_cell + 0.5) * lat_step

        cos_lat = np.cos(np.radians(cell_lat))
        cos_lat = max(cos_lat, 1e-6)

        lon_step = CELL_KM / (111.32 * cos_lat)
        lon_cell = int(np.floor(lon / lon_step))
        keep = True

        # Busca nas células vizinhas (3x3) verificar ESs aceitos próximos
        for di in range(-2, 3):
            for dj in range(-2, 3):
                neighbor = (lat_cell + di, lon_cell + dj)
                if neighbor not in grid:
                    continue

                for idx in grid[neighbor]:
                    lat2, lon2 = accepted[idx]
                    if haversine(lat, lon, lat2, lon2) < MIN_DISTANCE:
                        keep = False
                        break

                if not keep:
                    break

            if not keep:
                break

        if keep:
            accepted[n_accepted] = (lat, lon)
            cell = (lat_cell, lon_cell)

            if cell not in grid:
                grid[cell] = []
            grid[cell].append(n_accepted)

            n_accepted += 1

    accepted = accepted[:n_accepted]
    print(f"Accepted ESs: {len(accepted):,}")

    # Save accepted Earth Stations to CSV
    np.savetxt(
        "src/data/accepted_ESs.csv",
        accepted,
        delimiter=",",
        header="latitude,longitude",
        comments="",
        fmt="%.8f"
    )

    # plot
    plt.figure(figsize=(16, 8))

    plt.imshow(data, cmap="viridis",
               origin="upper",
               extent=[-180, 180, -60, 85])
    plt.scatter(accepted[:, 1], accepted[:, 0], s=0.15, c="red", alpha=0.5)

    plt.xlabel("Longitude")
    plt.ylabel("Latitude")
    plt.title("Deployed Earth Stations - 20 km between co-frequency beams")

    plt.tight_layout()
    plt.savefig("src/visualization/deployed_ESs.png")

    return accepted


def generate_es_grid(
    victim_lat_deg,
    victim_lon_deg,
    grid_radius_km=315,
    spacing_km=21,
    output_csv=None,
):
    """
    Generate a square grid of Earth stations centered on the victim ES.

    Parameters
    ----------
    victim_lat_deg : float
        Victim Earth station latitude [deg].

    victim_lon_deg : float
        Victim Earth station longitude [deg].

    grid_radius_km : float, optional
        Half-width of the square grid [km].
        For example:
            500 -> grid extends from -500 km to +500 km.

    spacing_km : float, optional
        Grid spacing [km].

    output_csv : str | None
        Optional CSV dump of the grid. None (default) writes nothing —
        the engine calls this per run and must not leave files in the CWD.

    Returns
    -------
    deployed_ESs : list of tuple
        List of (latitude, longitude).
    """

    victim_lat_rad = math.radians(victim_lat_deg)

    deployed_ESs = []

    n = int(grid_radius_km / spacing_km)

    for iy in range(-n, n + 1):
        north_km = iy * spacing_km

        for ix in range(-n, n + 1):
            east_km = ix * spacing_km

            # Skip the victim location
            if ix == 0 and iy == 0:
                continue

            dlat = north_km / R
            dlon = east_km / (R * math.cos(victim_lat_rad))

            lat = victim_lat_deg + math.degrees(dlat)
            lon = victim_lon_deg + math.degrees(dlon)

            # Wrap longitude into [-180,180]
            lon = ((lon + 180) % 360) - 180

            deployed_ESs.append((lat, lon))

    if output_csv:
        with open(output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["latitude", "longitude"])

            for lat, lon in deployed_ESs:
                writer.writerow([f"{lat:.8f}", f"{lon:.8f}"])

    return np.array(deployed_ESs)
