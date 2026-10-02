
#
# helpers.py – shared plumbing for the test suite: fake Kodi, fake Epika and a plugin runner.
#
# The tests run on any PC with plain Python, without Kodi and without network access:
#	- the folder kodi_stubs/ is put on sys.path, so "import xbmc" etc. load small stand-ins
#	  that record what the add-on does (listed items, notifications, resolved playback)
#	- FakeEpika replaces urllib.request.urlopen and answers the add-on's API calls from the
#	  synthetic data below; tests can change routes or inject errors per request
#	- KodiTestCase gives every test a fresh temporary profile folder and clean stub state, and
#	  invoke() runs the add-on exactly like Kodi: fresh modules, sys.argv = [url, handle, query]
#
# All data here is made up and only mirrors the shape of real Epika responses (no LRT content).
# When Epika changes a response format, update the matching structure here first, then the code.
#

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, 'plugin.video.lrt.epika')
# Import paths: the Kodi stubs, the add-on (for "resources.lib...") and the repo root (build scripts).
for path in (os.path.join(HERE, 'kodi_stubs'), ADDON, ROOT):
	if path not in sys.path:
		sys.path.insert(0, path)

import xbmc  # noqa: E402  (stub)
import xbmcaddon  # noqa: E402
import xbmcgui  # noqa: E402
import xbmcplugin  # noqa: E402
import xbmcvfs  # noqa: E402

# --- synthetic Epika data ---
# A film (101) with a full detail record, a promo-banner film (102), a one-season series (201) with
# two episodes (301, 302), a two-season series (202), the website menu with an unsafe link, and a
# home page whose rows cover every row type plugin.view() handles. The playlist also contains a
# file:// subtitle that the add-on must refuse.

IMG = {
	'3x4': [{'templateUrl': '//img.example/poster.jpg?dstw={width:560}&dsth={height:746}'}],
	'16x9': [{'url': '//img.example/land.jpg'}],
}
ART = {'16x9': [{'templateUrl': '//img.example/fan.jpg?dstw={width:1920}&dsth={height:1080}'}]}

FILM = {
	'type_': 'VOD', 'type': 'VOD', 'id': 101, 'title': 'Test film', 'lead': 'Short lead', 'rating': 14,
	'since': '2026-10-01T21:01:00Z', 'till': '2026-11-30T21:59:59Z', 'images': IMG, 'trailer': True,
	'year': 2024, 'duration': 5400, 'mainCategory': {'id': 268, 'name': 'Vaidybiniai filmai'},
}
FILM_DETAIL = dict(FILM, description='<p>Full <b>plot</b> &amp; more</p><p>Second</p>', artworks=ART,
	originalTitle='Original title', genres=[{'name': 'Drama'}], tags=[{'name': 'N-14'}],
	persons={'DIRECTOR': [{'name': 'Dee Rector'}], 'ACTOR': [{'name': 'Ann Actor'}, {'name': 'Bob Actor'}],
		'SCRIPTWRITER': [{'name': 'Wri Ter'}], 'PRESENTER': []},
	audioTracks=[{'language': 'LIT', 'type': 'DUBBING'}, {'language': 'UND', 'type': 'ORIGINAL'}],
	subtitles=[{'language': 'LIT'}])
BANNER_FILM = dict(FILM, id=102, title='Banner film')
SERIES = {'type_': 'SERIAL', 'type': 'SERIAL', 'id': 201, 'title': 'Test series', 'images': IMG, 'year': 2025, 'trailer': False}
SERIES_DETAIL = dict(SERIES, id='201', description='Series plot', artworks=ART, genres=[{'name': 'Drama'}])
SERIES2 = dict(SERIES, id=202, title='Long series')
EPISODES = [
	{'type': 'EPISODE', 'id': 301, 'title': 'Test series 1 s.', 'number': 1, 'duration': 2500, 'images': IMG},
	{'type': 'EPISODE', 'id': 302, 'title': 'Test series 2 s.', 'number': 2, 'duration': 2600, 'images': IMG},
]
EPISODE_DETAIL = dict(EPISODES[0], type_='EPISODE', description='Episode plot',
	season={'type': 'SEASON', 'id': 2011, 'number': 1, 'serial': {'id': 201, 'title': 'Test series'}})

MENU = {'web': [
	{'type': 'LOGO'},
	{'name': 'COMMON_MOVIES', 'url': '${FO_URL}/filmai'},
	{'name': 'COMMON_MY_LIST', 'url': '${FO_URL}/mano-sarasas'},
	{'name': 'COMMON_LRT_BY_TYPE', 'url': '${FO_URL}/filmai', 'elements': [
		{'name': 'SEO_VIEW_DOCS', 'url': '${FO_URL}/dokumentiniai-filmai'},
		{'name': 'BAD', 'url': '${FO_URL}/../../etc'},
	]},
	{'type': 'SEARCH'},
	{'name': 'COMMON_LOG_IN', 'url': '${FO_URL}/subscriber/login'},
	{'type': 'SUBSCRIBER_DROPDOWN', 'url': '${FO_URL}/subscriber/account/settings'},
]}
TRANSLATIONS = {'COMMON_HOME': 'Pradinis', 'COMMON_MOVIES': 'Filmai', 'COMMON_MY_LIST': 'Mano sąrašas',
	'COMMON_LRT_BY_TYPE': 'Kategorijos', 'SEO_VIEW_DOCS': 'Dokumentiniai filmai'}
MAIN_ROWS = [
	{'id': 11, 'title': 'Row one', 'contentType': 'STATIC', 'layout': 'ONE_LINE', 'elements': [{'item': FILM}, {'item': SERIES}]},
	{'id': 12, 'title': 'Catalogue', 'contentType': 'CATALOG', 'layout': 'GRID', 'mainCategory': {'id': 268}, 'elements': []},
	{'id': 14, 'title': 'Continue', 'contentType': 'SUBSCRIBER_WATCHED', 'elements': []},
	{'id': 15, 'title': 'For you', 'contentType': 'RECOMMENDATION', 'elements': []},
	{'id': 13, 'title': 'Promo', 'contentType': 'STATIC', 'layout': 'BANNER', 'elements': [{'item': BANNER_FILM}]},
	'garbage',
]
PLAYLIST = {
	'sources': {'DASH': [{'src': '//cdn.example/dash/Manifest.ism'}], 'HLS': [{'src': '//cdn.example/hls.m3u8'}]},
	'drm': {'WIDEVINE': {'src': 'https://epika.lrt.lt/api/products/101/drm/widevine?platform=BROWSER&type=MOVIE&tenantUid=Lh8t'}},
	'subtitles': [{'url': '//subs.example/s.vtt', 'language': 'LIT'}, {'url': 'file:///etc/passwd', 'language': 'ENG'}],
}
TRAILER = {'sources': {'MP4': [{'src': '//cdn.example/trailer.mp4'}]}}
TOKEN = 'f' * 32


# Fake Epika server. Installed as urllib.request.urlopen, so the add-on's real HTTP code runs.
#	routes    (method, API path) -> response data, or a function of the query parameters
#	errors    (method, API path) -> (HTTP status, JSON body) to make a request fail
#	files     full URL -> bytes, for downloads from other hosts (subtitles)
#	requests  every request made: (method, URL, headers), for assertions
# Unknown paths answer 404, unknown hosts fail like a network error.
class FakeEpika:

	def __init__(self):
		self.requests = []
		self.routes = {
			('GET', 'documents/menu/content'): MENU,
			('GET', 'translations/LIT'): TRANSLATIONS,
			('GET', 'products/sections/main'): MAIN_ROWS,
			('GET', 'products/sections/filmai'): MAIN_ROWS[:2],
			('GET', 'products/sections/11'): {'id': 11, 'elements': [{'item': FILM}, {'item': SERIES}, {'item': {'type': 'ARTICLE', 'id': 9}}]},
			('GET', 'items/categories'): [{'id': 268, 'name': 'Vaidybiniai filmai', 'type': 'VOD'}, {'id': 300, 'name': 'Komedija', 'type': 'VOD'}, {'id': 1, 'name': 'Adult', 'type': 'VOD', 'adult': True}],
			('GET', 'products/vods'): {'meta': {'totalCount': 120}, 'items': [FILM]},
			('GET', 'products/vods/101'): FILM_DETAIL,
			('GET', 'products/vods/102'): BANNER_FILM,
			('GET', 'products/vods/301'): EPISODE_DETAIL,
			('GET', 'products/vods/302'): EPISODES[1],
			('GET', 'products/vods/serials/201'): SERIES_DETAIL,
			('GET', 'products/vods/serials/201/seasons'): [{'id': 2011, 'title': '1 sezonas', 'number': 1}],
			('GET', 'products/vods/serials/201/seasons/2011/episodes'): EPISODES,
			('GET', 'products/vods/serials/202'): SERIES2,
			('GET', 'products/vods/serials/202/seasons'): [{'id': 2021, 'title': '1 sezonas', 'number': 1}, {'id': 2022, 'title': '2 sezonas', 'number': 2}],
			('GET', 'products/vods/search/VOD'): {'items': [FILM]},
			('GET', 'products/vods/search/SERIAL'): {'items': [SERIES]},
			('GET', 'products/vods/search/EPISODE'): {'items': [EPISODES[0]]},
			('GET', 'products/101/videos/playlist'): lambda q: PLAYLIST if q.get('videoType') == 'MOVIE' else TRAILER,
			('GET', 'subscribers/bookmarks'): lambda q: {'items': [{'item': FILM}]} if q.get('type') == 'FAVOURITE' else {'items': []},
			('POST', 'subscribers/bookmarks'): None,
			('DELETE', 'subscribers/bookmarks'): None,
			('POST', 'subscribers/devices/codes'): {'code': '123456', 'expiresAt': '2099-01-01T00:00:00+02:00'},
			('POST', 'subscribers/login'): {'token': TOKEN, 'email': 'tester@example.com'},
			('POST', 'subscribers/logout'): None,
		}
		self.errors = {}
		self.files = {'https://subs.example/s.vtt': b'WEBVTT\n'}

	def urlopen(self, req, timeout=None):
		url = req.full_url if isinstance(req, urllib.request.Request) else req
		parts = urllib.parse.urlsplit(url)
		method = req.get_method() if isinstance(req, urllib.request.Request) else 'GET'
		headers = dict(req.header_items()) if isinstance(req, urllib.request.Request) else {}
		self.requests.append((method, url, headers))
		if parts.hostname != 'epika.lrt.lt':
			if url in self.files:
				return Response(self.files[url])
			raise urllib.error.URLError('no route to %s' % url)
		path = parts.path[len('/api/'):]
		query = dict(urllib.parse.parse_qsl(parts.query))
		key = (method, path)
		if key in self.errors:
			status, body = self.errors[key]
			raise urllib.error.HTTPError(url, status, 'error', {}, io.BytesIO(json.dumps(body).encode()))
		if key not in self.routes:
			raise urllib.error.HTTPError(url, 404, 'not found', {}, io.BytesIO(b'{}'))
		body = self.routes[key]
		body = body(query) if callable(body) else body
		return Response(b'' if body is None else json.dumps(copy.deepcopy(body)).encode())

	def paths(self, method=None):
		return [urllib.parse.urlsplit(u).path for m, u, h in self.requests if method in (None, m)]


# Minimal HTTP response with what epika.py reads: read(), headers and use as a context manager.
class Response:
	def __init__(self, data, headers=None):
		self.stream = io.BytesIO(data)
		self.headers = headers or {}
		self.status = 200

	def read(self, size=-1):
		return self.stream.read(size)

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


# Fresh profile folder, fake server and Kodi stub state for every test.
class KodiTestCase(unittest.TestCase):

	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix='epika-test-')
		xbmcvfs.ROOT[0] = self.tmp
		self.server = FakeEpika()
		self._urlopen = urllib.request.urlopen
		urllib.request.urlopen = self.server.urlopen
		xbmcplugin.reset()
		xbmcgui.NOTES.clear()
		xbmcgui.PROGRESS.clear()
		xbmcgui.ANSWERS.update(yesno=False, input='', cancel_progress=True)
		xbmc.BUILTINS.clear()
		xbmc.LOG.clear()
		xbmc.BUILD_VERSION[0] = '21.2 (21.2.0) Git:stub'
		xbmc.PLAYER.update(playing=True, subtitles=['lt'], shown=None)
		xbmcaddon.SETTINGS.clear()
		xbmcaddon.SETTINGS.update(xbmcaddon.DEFAULTS)

	def tearDown(self):
		urllib.request.urlopen = self._urlopen
		shutil.rmtree(self.tmp, ignore_errors=True)

	# Path inside the add-on's profile folder of this test (session.json, cache/, ...).
	def profile(self, *parts):
		return os.path.join(self.tmp, 'profile', 'addon_data', 'plugin.video.lrt.epika', *parts)

	# Starts the test logged in, as if pairing had happened (writes session.json with TOKEN).
	def login(self):
		os.makedirs(self.profile(), exist_ok=True)
		with open(self.profile('session.json'), 'w', encoding='utf-8') as fh:
			json.dump({'device_uid': 'dev-1', 'token': TOKEN, 'account': 'te***@example.com'}, fh)

	# Runs the add-on like Kodi does: sys.argv = [base URL, handle, ?query] and freshly imported
	# modules (kodi.py reads sys.argv and settings at import). Returns xbmcplugin.STATE.
	def invoke(self, query='', handle='1'):
		for name in [m for m in sys.modules if m == 'resources' or m.startswith('resources.')]:
			del sys.modules[name]
		sys.argv = ['plugin://plugin.video.lrt.epika/', handle, '?' + query]
		from resources.lib import plugin
		plugin.run()
		return xbmcplugin.STATE

	# Labels of the listed entries, the quickest way to compare a listing.
	def labels(self, state=None):
		return [li.label for url, li, folder in (state or xbmcplugin.STATE)['items']]

	def errors_logged(self):
		return [m for level, m in xbmc.LOG if level >= xbmc.LOGERROR]
