"""order2homebox server package."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("order2homebox-server")
except PackageNotFoundError:  # not installed (e.g. running from a source checkout)
    __version__ = "dev"
