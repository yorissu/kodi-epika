
#
# default.py – the add-on's entry point.
#
# Kodi runs this file every time it opens one of the add-on's plugin:// addresses: the main menu,
# a row of titles, a search, playback, a settings button. addon.xml names it as the "library" of
# the xbmc.python.pluginsource extension. Kodi passes the request in sys.argv:
#	sys.argv[0]   the add-on's base URL        plugin://plugin.video.lrt.epika/
#	sys.argv[1]   the handle of the listing    used to add items and to finish the listing
#	sys.argv[2]   the query string             ?action=section&id=433059
# Every call is a fresh start, so nothing is kept in memory between two calls; state lives in
# the profile folder (see session.py and cache.py). All the work happens in resources/lib/plugin.py.
#

from resources.lib import plugin


plugin.run()
