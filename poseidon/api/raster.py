"""표시용 파고 확대. 해상 가중치를 정규화해 육지의 0 혼입을 막는다."""
import numpy as np
from scipy.ndimage import zoom


def upsample_sea(hs, sea, factor):
    weight = zoom(sea.astype(float), factor, order=1)
    total = zoom(np.where(sea, hs, 0.0), factor, order=1)
    values = np.divide(total, weight, out=np.zeros_like(total), where=weight > 1e-12)
    return values, np.clip((weight - .35) / .3, 0, 1)
