try:
    from .jina_reader import read_url
    from .xml_search import search_web
except ImportError:  # pragma: no cover
    from jina_reader import read_url
    from xml_search import search_web

__all__ = ["read_url", "search_web"]
