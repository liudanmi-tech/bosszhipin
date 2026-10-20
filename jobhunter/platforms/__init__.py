from .boss import Boss
from .liepin import Liepin
from .maimai import Maimai

PLATFORMS = {p.name: p for p in (Boss, Liepin, Maimai)}
