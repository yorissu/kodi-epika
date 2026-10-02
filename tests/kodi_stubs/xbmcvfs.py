
#
# kodi_stubs/xbmcvfs.py – test stand-in for Kodi's xbmcvfs module.
#
# translatePath maps special:// paths into the test's temporary folder (ROOT[0], set by
# KodiTestCase), so the add-on's profile, cache and subtitle files land there.
#

import os

ROOT = [None]


def translatePath(path):
	assert ROOT[0], 'tests must set xbmcvfs.ROOT[0]'
	return os.path.join(ROOT[0], path.replace('special://', '').replace('/', os.sep))
