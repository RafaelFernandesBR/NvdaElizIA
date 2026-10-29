# -*- coding: utf-8 -*-
"""
Polyfill para zoneinfo - necessario para compatibilidade com versoes antigas do Python
"""

import sys

# Tentar importar do stdlib primeiro (Python 3.9+)
# Usar __import__ para evitar circular imports
try:
	_zoneinfo_native = __import__('zoneinfo', fromlist=['ZoneInfo', 'ZoneInfoNotFoundError'])
	ZoneInfo = _zoneinfo_native.ZoneInfo
	ZoneInfoNotFoundError = _zoneinfo_native.ZoneInfoNotFoundError
except (ImportError, AttributeError):
	# Fallback para versoes antigas
	from datetime import tzinfo, timedelta

	class ZoneInfoNotFoundError(Exception):
		"""Excecao quando ZoneInfo nao eh encontrado"""
		pass

	class ZoneInfo(tzinfo):
		"""Stub minimo de ZoneInfo para compatibilidade com versoes antigas"""

		def __init__(self, key):
			self.key = key

		def utcoffset(self, dt):
			return timedelta(0)

		def tzname(self, dt):
			return self.key

		def dst(self, dt):
			return timedelta(0)

__all__ = ['ZoneInfo', 'ZoneInfoNotFoundError']
