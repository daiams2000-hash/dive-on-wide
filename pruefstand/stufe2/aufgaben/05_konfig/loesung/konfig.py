import copy
from standard import STANDARD


def _mischen(basis, neu):
    for k, v in neu.items():
        if isinstance(v, dict) and isinstance(basis.get(k), dict):
            _mischen(basis[k], v)
        else:
            basis[k] = copy.deepcopy(v)
    return basis


def lade(nutzer):
    return _mischen(copy.deepcopy(STANDARD), nutzer)
