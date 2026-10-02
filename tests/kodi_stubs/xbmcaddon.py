
#
# kodi_stubs/xbmcaddon.py – test stand-in for Kodi's xbmcaddon module.
#
# Settings come from SETTINGS (reset to DEFAULTS, the same defaults as settings.xml, before each
# test). Strings come from the add-on's real en_gb strings.po, and asking for an id that isn't in
# it fails the test, which catches strings used in code but missing from the language file.
#

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PO = os.path.join(HERE, '..', '..', 'plugin.video.lrt.epika', 'resources', 'language', 'resource.language.en_gb', 'strings.po')
DEFAULTS = {'full_details': True, 'page_size': 50, 'sort': 'newest', 'language': 'LIT', 'subtitles': True, 'cache_hours': 12, 'debug': False}
SETTINGS = dict(DEFAULTS)

with open(PO, encoding='utf-8') as fh:
	STRINGS = {int(i): text for i, text in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', fh.read())}


class Addon:
	def __init__(self, id=None):
		pass

	def getAddonInfo(self, key):
		return {'id': 'plugin.video.lrt.epika', 'name': 'LRT Epika', 'profile': 'special://profile/addon_data/plugin.video.lrt.epika/'}[key]

	def getSetting(self, key):
		return str(SETTINGS.get(key, ''))

	def getSettingBool(self, key):
		return bool(SETTINGS.get(key))

	def getSettingInt(self, key):
		return int(SETTINGS.get(key) or 0)

	def getLocalizedString(self, string_id):
		assert string_id in STRINGS, 'string %d missing from strings.po' % string_id
		return STRINGS[string_id]
