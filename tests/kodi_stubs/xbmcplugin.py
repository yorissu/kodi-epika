
#
# kodi_stubs/xbmcplugin.py – test stand-in for Kodi's xbmcplugin module.
#
# Collects what one plugin call hands to Kodi in STATE: listed items, the content type, every
# endOfDirectory result and every setResolvedUrl. reset() empties it between tests.
#

SORT_METHOD_UNSORTED, SORT_METHOD_LABEL, SORT_METHOD_VIDEO_YEAR, SORT_METHOD_DURATION, SORT_METHOD_EPISODE = range(5)
STATE = {}


def reset():
	STATE.clear()
	STATE.update(items=[], content=None, ends=[], resolved=[])


reset()


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
	assert isinstance(url, str)
	STATE['items'].append((url, listitem, isFolder))
	return True


def addDirectoryItems(handle, items, totalItems=0):
	for url, listitem, folder in items:
		addDirectoryItem(handle, url, listitem, folder)
	return True


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
	STATE['ends'].append(succeeded)


def setContent(handle, content):
	STATE['content'] = content


def addSortMethod(handle, sortMethod, labelMask='', label2Mask=''):
	pass


def setResolvedUrl(handle, succeeded, listitem):
	STATE['resolved'].append((succeeded, listitem))
