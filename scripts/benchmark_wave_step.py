"""파랑 엔진 스텝 벤치마크: NumPy 참조 vs JAX 커널 (운영 격자).

사용: .venv/bin/python scripts/benchmark_wave_step.py
"""

import time

import numpy as np
import xarray as xr

from poseidon.engines.spectral_wave.grid import SpectralGrid
from poseidon.engines.spectral_wave.jax_kernel import JaxRegionalKernel, ring_mask
from poseidon.engines.spectral_wave.regional import RegionalWaveModel

grid = SpectralGrid()
lats = np.arange(12.5, 46.0 + 1e-9, 0.25)
lons = np.arange(120.0, 148.0 + 1e-9, 0.25)
et = xr.open_dataset("data/static/etopo2022_60s_east_asia.nc")
depth = np.maximum(-et.z.interp(lat=xr.DataArray(lats, dims="y"),
                                lon=xr.DataArray(lons, dims="x")).values, 0.0)
m = RegionalWaveModel(lats, lons, depth, grid, h_min=10.0)
print(f"grid {m.ny}x{m.nx}x{grid.nf}x{grid.ntheta} "
      f"({m.ny*m.nx*grid.nf*grid.ntheta/1e6:.0f}M bins)")

x, y = np.meshgrid(np.arange(m.nx), np.arange(m.ny))
hs = 2.0 + 6.0 * np.exp(-((x - 90) ** 2 + (y - 50) ** 2) / 100.0)
e0 = m.spectra_from_integrals(hs, np.full_like(hs, 10.0), np.full_like(hs, 0.5))
m.set_boundary(e0)
u10 = np.full((m.ny, m.nx), 12.0)
udir = np.full((m.ny, m.nx), 0.7)
dt = m.cfl_dt()

# NumPy
e = e0.copy()
t0 = time.perf_counter()
for _ in range(3):
    e = m.step(e, dt, u10, udir)
t_np = (time.perf_counter() - t0) / 3

# JAX (jit 워밍업 후)
k = JaxRegionalKernel(m)
k.prepare(m, dt)
ring = ring_mask(m.ny, m.nx)
e = k.step(e0, dt, u10, udir, e0, ring)
np.asarray(e)                                # 컴파일+동기화
t0 = time.perf_counter()
n = 10
for _ in range(n):
    e = k.step(e, dt, u10, udir, e0, ring)
np.asarray(e)
t_jx = (time.perf_counter() - t0) / n

steps_24h = int(24 * 3600 / dt)
print(f"dt={dt:.0f}s, 24h={steps_24h} steps")
print(f"NumPy : {t_np*1000:7.0f} ms/step → 24h {t_np*steps_24h/60:5.1f} min")
print(f"JAX   : {t_jx*1000:7.0f} ms/step → 24h {t_jx*steps_24h/60:5.1f} min "
      f"(×{t_np/t_jx:.1f})")
