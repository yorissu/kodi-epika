
#
# kodi.py – small helpers around the Kodi Python API, shared by the other modules.
#
# It reads the facts every plugin call needs once, at import: the add-on's id and name, its
# profile folder (special://profile/addon_data/plugin.video.lrt.epika/, where session.json, the
# cache and the search history live), the listing handle Kodi passed in sys.argv and whether
# debug logging is on. The functions wrap Kodi calls that are used everywhere: strings from
# strings.po, settings from settings.xml, logging to kodi.log, building plugin:// URLs and
# showing notifications.
#
# Only the modules that talk to Kodi import this file. epika.py, session.py and cache.py don't,
# which keeps them testable without Kodi.
#

import xbmcaddon
import xbmcgui
import xbmcvfs
import xbmc

import urllib.parse
import sys
import os


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
NAME = ADDON.getAddonInfo('name')
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
BASE_URL = 'plugin://%s/' % ADDON_ID
# Handle of the listing Kodi asked for. -1 when the call isn't a listing (RunPlugin from a
# settings button or context menu): such calls must not call endOfDirectory/setResolvedUrl.
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip('-').isdigit() else -1
# Settings → Advanced → Debug logging: log() then writes its debug messages at INFO level, so they
# show up in kodi.log without switching on Kodi's own (very verbose) debug logging.
DEBUG = ADDON.getSettingBool('debug')


# Text from strings.po in Kodi's interface language (30000-series ids), English as fallback.
def translate(string_id):
	return ADDON.getLocalizedString(string_id)


def setting(key):
	return ADDON.getSetting(key)


def setting_bool(key):
	return ADDON.getSettingBool(key)


def setting_int(key):
	return ADDON.getSettingInt(key)


# Writes to kodi.log with the add-on id in front. Never log the login token.
def log(msg, level=xbmc.LOGDEBUG):
	if level == xbmc.LOGDEBUG and DEBUG:
		level = xbmc.LOGINFO
	xbmc.log('[%s] %s' % (ADDON_ID, msg), level)


# plugin:// address that calls this add-on again, e.g. url(action='play', id=101).
# Parameters set to None are left out. plugin.py validates everything it receives back.
def url(**params):
	return BASE_URL + '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def notify(msg, icon=xbmcgui.NOTIFICATION_INFO, ms=4000):
	xbmcgui.Dialog().notification(NAME, msg, icon, ms)


# Kodi's major version (20, 21, 22...). player.py needs it because inputstream.adaptive changed
# how DRM is configured in Kodi 22. Assumes 21 if the version can't be read.
def kodi_major():
	try:
		return int(xbmc.getInfoLabel('System.BuildVersion').split('.')[0])
	except ValueError:
		return 21


def profile_path(*parts):
	return os.path.join(PROFILE, *parts)
