
#
# player.py – turns Epika's playlist into something Kodi can play.
#
# How Epika streams
#	Films and episodes are MPEG-DASH streams encrypted with Widevine DRM (Epika also offers HLS
#	with Apple FairPlay and Smooth Streaming with PlayReady, which Kodi can't use on a Pi).
#	Kodi plays them through the inputstream.adaptive add-on, which needs the Widevine library;
#	script.module.inputstreamhelper installs that on first use. When playback starts,
#	inputstream.adaptive asks Epika's licence server for the keys, with this device's login attached.
#	Trailers are plain MP4 files without DRM and play directly.
#
# What the module does
#	resolve() picks the stream, configures inputstream.adaptive for Widevine (the configuration
#	format differs between Kodi 21 and 22), downloads the subtitles and adds the title's metadata,
#	so the player's info screen looks the same as the listing. plugin.py hands the result to Kodi
#	with setResolvedUrl.
#
# Security
#	Only https URLs are used, the login token is only ever sent to epika.lrt.lt (even if a playlist
#	named another licence server), and subtitle downloads are size-limited.
#

import json
import os
import shutil
import time
import urllib.parse
import urllib.request
import uuid

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from . import items, kodi
from .epika import USER_AGENT, is_epika_url

# Kodi's name for the Widevine DRM system.
DRM = 'com.widevine.alpha'
# Largest subtitle file accepted; real ones are a few hundred kB.
MAX_SUBTITLE = 5 * 1024 * 1024


# Protocol-relative and https links only; anything else (http, file://) is refused.
def https(link):
	if not isinstance(link, str) or not link:
		return None
	link = 'https:' + link if link.startswith('//') else link
	return link if link.startswith('https://') else None


# First usable https URL of one stream format (DASH, MP4...) in the playlist's sources.
def first_source(sources, key):
	for entry in sources.get(key) or []:
		link = https(entry.get('src')) if isinstance(entry, dict) else None
		if link:
			return link
	return None


# Downloads the playlist's subtitles (WebVTT) into folder and returns their paths.
# Kodi takes a subtitle's language from its file name, so they are saved as Epika.<lang>.vtt
# (e.g. Epika.lt.vtt) and Kodi's subtitle menu shows "Lithuanian". The language code is
# sanitised by items.lang2, so a strange code from Epika can't become a path. A subtitle that
# fails to download is logged and skipped; playback goes ahead without it.
def subtitle_files(playlist, folder):
	os.makedirs(folder, exist_ok=True)
	paths = []
	for sub in playlist.get('subtitles') or []:
		if not isinstance(sub, dict):
			continue
		link = https(sub.get('url'))
		if not link:
			continue
		lang = items.lang2(sub.get('language')) or 'und'
		path = os.path.join(folder, 'Epika.%s.vtt' % lang)
		if path in paths:
			continue
		try:
			req = urllib.request.Request(link, headers={'User-Agent': USER_AGENT})
			with urllib.request.urlopen(req, timeout=15) as resp:
				data = resp.read(MAX_SUBTITLE + 1)
			if len(data) > MAX_SUBTITLE:
				raise ValueError('subtitle too large')
			tmp = path + '.tmp'
			with open(tmp, 'wb') as fh:
				fh.write(data)
			os.replace(tmp, path)
			paths.append(path)
		except (OSError, ValueError) as err:
			kodi.log('subtitle download failed: %s' % err, xbmc.LOGWARNING)
	return paths


# True when inputstream.adaptive and Widevine are installed and enabled. If not, InputStream
# Helper offers to install them (a one-time download of a few minutes on a Pi).
def widevine_ready():
	try:
		import inputstreamhelper
	except ImportError:
		kodi.log('script.module.inputstreamhelper missing', xbmc.LOGERROR)
		return False
	return inputstreamhelper.Helper('mpd', drm=DRM).check_inputstream()


# ListItem properties that make inputstream.adaptive use Widevine with Epika's licence server.
#	Kodi 22+:   inputstream.adaptive.drm, a JSON object with the server URL and request headers.
#	Kodi 20/21: license_type + license_key "<url>|<headers>|R{SSM}|", where R{SSM} means "send the
#	            player's licence request as the raw request body", which is what Epika expects.
#	Kodi 20 also needs manifest_type; 21 detects it and warns when it is set.
# headers are URL-encoded, as inputstream.adaptive requires.
def drm_properties(license_url, headers, major):
	encoded = urllib.parse.urlencode(headers)
	if major >= 22:
		return {'inputstream.adaptive.drm': json.dumps({DRM: {'license': {'server_url': license_url, 'req_headers': encoded}}})}
	props = {
		'inputstream.adaptive.license_type': DRM,
		'inputstream.adaptive.license_key': '%s|%s|R{SSM}|' % (license_url, encoded),
	}
	if major < 21:
		props['inputstream.adaptive.manifest_type'] = 'mpd'
	return props


# Headers inputstream.adaptive sends with each licence request. Epika's licence server checks
# the login, so the device id and token are included, but only when the licence URL really is
# https://epika.lrt.lt: the URL comes from a server response, and the token must never leak to
# another host.
def license_headers(epika, license_url):
	headers = {'Content-Type': 'application/octet-stream', 'User-Agent': USER_AGENT}
	if is_epika_url(license_url):
		headers.update(epika.auth_headers())
	else:
		kodi.log('licence server is not %s, sending no login' % license_url, xbmc.LOGWARNING)
	return headers


# Builds the ListItem Kodi plays, from the playlist (epika.py: playlist) of a film, episode or
# trailer. DASH with Widevine is preferred; MP4 is used when there is no DASH (trailers).
# info_item, the title's detail record, adds metadata and artwork for the player screens.
# Returns (ListItem, subtitle paths), or (None, []) when nothing playable is found or Widevine
# is missing; the reason is in the log.
def resolve(epika, playlist, video_type, info_item=None):
	sources = playlist.get('sources') if isinstance(playlist.get('sources'), dict) else {}
	dash = first_source(sources, 'DASH')
	mp4 = first_source(sources, 'MP4')
	if dash:
		drm = playlist.get('drm') if isinstance(playlist.get('drm'), dict) else {}
		widevine = drm.get('WIDEVINE') if isinstance(drm.get('WIDEVINE'), dict) else {}
		license_url = https(widevine.get('src'))
		if not license_url:
			kodi.log('DASH source without a Widevine licence URL', xbmc.LOGERROR)
			return None, []
		if not widevine_ready():
			return None, []
		li = xbmcgui.ListItem(path=dash, offscreen=True)
		li.setMimeType('application/dash+xml')
		li.setContentLookup(False)
		li.setProperty('inputstream', 'inputstream.adaptive')
		for key, value in drm_properties(license_url, license_headers(epika, license_url), kodi.kodi_major()).items():
			li.setProperty(key, value)
	elif mp4:
		li = xbmcgui.ListItem(path=mp4, offscreen=True)
	else:
		kodi.log('no playable source in %s' % list(sources), xbmc.LOGERROR)
		return None, []
	if info_item:
		li.setArt(items.artwork(info_item))
		items.describe(li, info_item)
	subs = []
	if video_type != 'TRAILER':
		folder = xbmcvfs.translatePath('special://temp/%s/%s/' % (kodi.ADDON_ID, uuid.uuid4().hex))
		subs = subtitle_files(playlist, folder)
	if subs:
		li.setSubtitles(subs)
	return li, subs


# Switches subtitles on once playback has started (Settings → Playback → Show subtitles).
# Kodi keeps added subtitles hidden unless its own language settings say otherwise, and it can
# only switch them on after the player has started, so this waits up to timeout seconds.
# plugin.py calls it after setResolvedUrl; the plugin call simply stays alive until then.
def show_subtitles_when_started(timeout=30):
	monitor = xbmc.Monitor()
	player = xbmc.Player()
	deadline = timeout * 2
	while deadline > 0 and not monitor.abortRequested():
		if player.isPlayingVideo() and player.getAvailableSubtitleStreams():
			player.showSubtitles(True)
			return
		monitor.waitForAbort(0.5)
		deadline -= 1


# Removes subtitle folders older than max_age seconds from special://temp. Each playback gets its
# own folder (Kodi runs all plugin calls in one process, so a process id wouldn't be unique);
# plugin.py calls this when the main menu opens.
def clean_temp(max_age=86400):
	root = xbmcvfs.translatePath('special://temp/%s/' % kodi.ADDON_ID)
	try:
		names = os.listdir(root)
	except OSError:
		return
	for name in names:
		path = os.path.join(root, name)
		try:
			if time.time() - os.path.getmtime(path) > max_age:
				if os.path.isdir(path):
					shutil.rmtree(path, ignore_errors=True)
				else:
					os.remove(path)
		except OSError:
			pass


# Tells Kodi that the requested playback can't happen, so it stops waiting and shows no player.
def fail():
	xbmcplugin.setResolvedUrl(kodi.HANDLE, False, xbmcgui.ListItem(offscreen=True))
