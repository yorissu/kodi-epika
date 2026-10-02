
#
# epika.py – the client for LRT Epika's web API: everything the add-on asks Epika goes through here.
#
# About the API
#	epika.lrt.lt runs on Redge Media's video platform. The website is a JavaScript app that loads
#	all its content from https://epika.lrt.lt/api/... as JSON; this module makes the same calls.
#	LRT doesn't document or publish this API. It was worked out from what the website does and
#	can change without notice: if listings or playback break after a website update, look here.
#	Every request carries:
#		tenantUid=Lh8t    Epika's id on the Redge platform
#		lang=LIT|ENG      language of labels and descriptions
#		platform=BROWSER  which layout to get; BROWSER gives the same pages and rows as the website.
#		                  The code login uses ANDROID_TV, the smart-TV flow it belongs to.
#		API-DeviceUid     this installation's id (session.py), and after login
#		API-Authentication  the login token
#
# What the module does
#	Builds the URLs, sends the requests, turns Epika's errors into EpikaError/AuthRequired,
#	caches responses (cache.py) and offers one method per thing the add-on needs: menu, pages and
#	rows, catalogue, search, film/series/episode details, playlists, My List, login and logout.
#	Results are always the expected type (dict or list, possibly empty), so callers don't have to
#	guard against odd responses.
#
# It uses only the Python standard library and doesn't import Kodi, so the tests and a plain
# Python on a PC can use it directly.
#

import gzip
import hashlib
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

HOST = 'epika.lrt.lt'
BASE = 'https://%s/api' % HOST
TENANT = 'Lh8t'
# The website layout (sections, rows) is configured per platform; BROWSER matches epika.lrt.lt.
PLATFORM = 'BROWSER'
# Device-code pairing is the smart-TV login flow.
PAIR_PLATFORM = 'ANDROID_TV'
USER_AGENT = 'Mozilla/5.0 (X11; Linux aarch64) Kodi plugin.video.lrt.epika'
# Seconds to wait for Epika before giving up with a network error.
TIMEOUT = 20
# Larger responses are refused; real ones are well below 1 MB.
MAX_RESPONSE = 32 * 1024 * 1024
# How long the My List membership (which titles get "Remove from My List") is cached.
FAVOURITES_TTL = 300


# Any failure talking to Epika. status is the HTTP status (0 for network problems), code is
# Epika's error code ("DEVICE_CODE_EXPIRED_OR_NOT_EXISTS") or our own ("NETWORK", "BAD_RESPONSE").
class EpikaError(Exception):
	def __init__(self, status, code, message=''):
		Exception.__init__(self, '%s %s %s' % (status, code, message))
		self.status = status
		self.code = code


# Epika wants a login for this request: either nobody is logged in, or the token was revoked
# (device removed on the website). plugin.py decides which and reacts accordingly.
class AuthRequired(EpikaError):
	pass


# True for https URLs on the Epika host: the only place the login token may be sent.
def is_epika_url(link):
	try:
		parts = urllib.parse.urlsplit(link)
	except (TypeError, ValueError):
		return False
	return parts.scheme == 'https' and parts.hostname == HOST


# One client per plugin call. session gives the device id and token, cache (optional) stores
# responses, lang is LIT or ENG, log receives debug messages (kodi.log in the add-on).
class Epika:
	def __init__(self, session, cache=None, lang='LIT', log=None):
		self.session = session
		self.cache = cache
		self.lang = lang if lang in ('LIT', 'ENG') else 'LIT'
		self.log = log or (lambda msg: None)

	# --- transport ---

	def url(self, path, params=(), platform=PLATFORM):
		query = [('tenantUid', TENANT), ('lang', self.lang), ('platform', platform)] + list(params)
		return '%s/%s?%s' % (BASE, path.lstrip('/'), urllib.parse.urlencode(query))

	# Headers that identify this device and, when logged in, carry the token. player.py also
	# uses them for the Widevine licence request, but only towards Epika itself.
	def auth_headers(self):
		headers = {'API-DeviceUid': self.session.device_uid}
		if self.session.token:
			headers['API-Authentication'] = self.session.token
		return headers

	# Sends one request and returns the decoded JSON (None for an empty body). body is sent as
	# JSON; auth=False leaves the token out (pairing). Every way it can fail ends in an exception
	# from this module: AuthRequired for a missing/revoked login, EpikaError for everything else
	# (HTTP errors with Epika's code, network trouble, timeouts, broken or oversized responses).
	def request(self, method, path, params=(), body=None, auth=True, platform=PLATFORM):
		headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
		headers.update(self.auth_headers() if auth else {'API-DeviceUid': self.session.device_uid})
		data = None
		if body is not None:
			data = json.dumps(body).encode('utf-8')
			headers['Content-Type'] = 'application/json'
		url = self.url(path, params, platform)
		self.log('%s %s' % (method, url))
		req = urllib.request.Request(url, data=data, headers=headers, method=method)
		try:
			with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
				payload = _read(resp)
		except urllib.error.HTTPError as err:
			code = ''
			try:
				parsed = json.loads(_read(err).decode('utf-8'))
				if isinstance(parsed, dict):
					code = str(parsed.get('code') or '')
			except (ValueError, OSError, EOFError, zlib.error, http.client.HTTPException):
				pass
			self.log('HTTP %s %s for %s' % (err.code, code, path))
			if err.code in (401, 403) and code == 'AUTHENTICATION_REQUIRED':
				raise AuthRequired(err.code, code)
			raise EpikaError(err.code, code or 'HTTP_%s' % err.code)
		except (urllib.error.URLError, OSError, http.client.HTTPException, EOFError, zlib.error) as err:
			raise EpikaError(0, 'NETWORK', str(getattr(err, 'reason', err)))
		if not payload:
			return None
		try:
			return json.loads(payload.decode('utf-8'))
		except ValueError:
			raise EpikaError(200, 'BAD_RESPONSE', path)

	# Cache key for a request. It includes a hash of the token: responses can differ for a
	# logged-in user and must never be served to another account (or to nobody after Log out).
	def _cache_key(self, path, params):
		who = hashlib.sha1(self.session.token.encode('utf-8')).hexdigest()[:12] if self.session.token else 'anon'
		return '%s|%s|%s|%s' % (path, urllib.parse.urlencode(list(params)), self.lang, who)

	# GET with caching: a cached response younger than ttl seconds is returned without asking
	# Epika. ttl=0 always asks (used for anything personal or time-sensitive).
	def get(self, path, params=(), ttl=0, auth=True):
		key = None
		if ttl and self.cache:
			key = self._cache_key(path, params)
			hit = self.cache.get(key, ttl)
			if hit is not None:
				return hit
		data = self.request('GET', path, params, auth=auth)
		if key and data is not None:
			self.cache.set(key, data)
		return data

	# --- account ---

	# Step 1 of the code login: Epika returns {"code": "123456", "expiresAt": "...+02:00"}.
	def create_pair_code(self):
		data = self.request('POST', 'subscribers/devices/codes', body={}, auth=False, platform=PAIR_PLATFORM)
		if not isinstance(data, dict) or not str(data.get('code') or '').isdigit():
			raise EpikaError(200, 'BAD_RESPONSE', 'pair code')
		return data

	# Step 2, polled until the user has entered the code on the website: until then Epika answers
	# DEVICE_CODE_EXPIRED_OR_NOT_EXISTS, afterwards the account with its token.
	def login_with_code(self, code):
		return self.request('POST', 'subscribers/login', body={'code': code}, auth=False, platform=PAIR_PLATFORM)

	# Revokes the token on Epika's side (the device disappears from the account).
	def logout(self):
		return self.request('POST', 'subscribers/logout')

	# --- navigation ---

	# The website's menu as configured by LRT; entry names are translation keys (translations()).
	def menu(self):
		return _dict(self.get('documents/menu/content', ttl=86400, auth=False))

	def translations(self):
		return _dict(self.get('translations/%s' % self.lang, ttl=86400, auth=False))

	# Genres/categories; with main_category only those inside it (e.g. genres of feature films).
	def categories(self, main_category=None):
		params = [('mainCategoryId', main_category)] if main_category else []
		return _list(self.get('items/categories', params, ttl=86400, auth=False))

	# Rows ("sections") of a website page: main, filmai, serialai, vaikams... Each row comes with
	# its first 12 titles, enough to decide how to show it; section() loads a whole row.
	def view(self, label):
		return _list(self.get('products/sections/%s' % label, [('elementsLimit', 12), ('maxResults', 100)], ttl=900))

	def section(self, section_id):
		return _dict(self.get('products/sections/%s' % section_id, [('elementsLimit', 500)], ttl=900))

	# One page of all titles, optionally limited to a main category and/or a genre. Returns
	# {"meta": {"totalCount": n}, "items": [...]}; first/count select the page.
	def catalog(self, main_category=None, category=None, first=0, count=50, sort='createdAt', order='desc'):
		params = [('firstResult', first), ('maxResults', count), ('sort', sort), ('order', order)]
		if main_category:
			params.append(('mainCategoryId[]', main_category))
		if category:
			params.append(('categoryId[]', category))
		return _dict(self.get('products/vods', params, ttl=900))

	# Search one kind of title: VOD (films), SERIAL (series) or EPISODE.
	def search(self, query, kind, count=100):
		return _dict(self.get('products/vods/search/%s' % kind, [('keyword', query), ('maxResults', count)], ttl=600))

	# --- products ---

	# Full record of a film or an episode (plot, cast, genres, artwork...). Lists only carry part of it.
	def vod(self, item_id, ttl):
		return _dict(self.get('products/vods/%s' % item_id, ttl=ttl))

	def serial(self, item_id, ttl):
		return _dict(self.get('products/vods/serials/%s' % item_id, ttl=ttl))

	def seasons(self, serial_id):
		return _list(self.get('products/vods/serials/%s/seasons' % serial_id, ttl=900))

	def episodes(self, serial_id, season_id):
		return _list(self.get('products/vods/serials/%s/seasons/%s/episodes' % (serial_id, season_id), ttl=900))

	# Full records for many titles at once, for the metadata of a whole listing. items is a list of
	# (kind, id); the result has one record per entry in the same order, None where it failed.
	# Up to 8 requests run in parallel, so a 26-title row takes about a second; cached records
	# cost nothing.
	def details(self, items, ttl, workers=8):
		def one(entry):
			kind, item_id = entry
			try:
				return self.serial(item_id, ttl) if kind == 'SERIAL' else self.vod(item_id, ttl)
			except EpikaError as err:
				self.log('detail %s %s failed: %s' % (kind, item_id, err))
				return None
		if not items:
			return []
		with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
			return list(pool.map(one, items))

	# What to play: stream URLs per format (DASH, HLS, MP4...), the Widevine licence URL and
	# subtitle files. video_type is MOVIE (the film/episode itself) or TRAILER. Never cached.
	def playlist(self, item_id, video_type='MOVIE'):
		return _dict(self.request('GET', 'products/%s/videos/playlist' % item_id, [('videoType', video_type)]))

	# --- bookmarks (My List, Continue watching) ---

	# Titles on one of the account's lists: FAVOURITE (My List) or WATCHED (Continue watching).
	def bookmarks(self, kind):
		data = _dict(self.request('GET', 'subscribers/bookmarks', [('type', kind), ('maxResults', 200)]))
		return [b['item'] for b in _list(data.get('items')) if isinstance(b, dict) and isinstance(b.get('item'), dict)]

	# IDs on My List, cached for a few minutes so listings don't each cost a request.
	def favourite_ids(self):
		params = [('type', 'FAVOURITE'), ('maxResults', 200)]
		key = self._cache_key('subscribers/bookmarks', params)
		ids = self.cache.get(key, FAVOURITES_TTL) if self.cache else None
		if ids is None:
			ids = [str(item.get('id')) for item in self.bookmarks('FAVOURITE')]
			if self.cache:
				self.cache.set(key, ids)
		return set(ids)

	# Drops the cached My List ids after a change, so the next listing shows the right menu entry.
	def _forget_favourites(self):
		if self.cache:
			self.cache.delete(self._cache_key('subscribers/bookmarks', [('type', 'FAVOURITE'), ('maxResults', 200)]))

	def bookmark_add(self, kind, item_id):
		self.request('POST', 'subscribers/bookmarks', [('type', kind)], body={'itemId': int(item_id)})
		self._forget_favourites()

	def bookmark_remove(self, kind, item_id):
		self.request('DELETE', 'subscribers/bookmarks', [('type', kind), ('itemId[]', item_id)])
		self._forget_favourites()


# Body of a response (or error response), size-limited and un-gzipped.
def _read(resp):
	payload = resp.read(MAX_RESPONSE + 1)
	if len(payload) > MAX_RESPONSE:
		raise EpikaError(0, 'TOO_LARGE')
	if resp.headers.get('Content-Encoding') == 'gzip':
		payload = gzip.decompress(payload)
	return payload


# _dict/_list: the value if it has the expected type, otherwise an empty one.
def _dict(value):
	return value if isinstance(value, dict) else {}


def _list(value):
	return value if isinstance(value, list) else []
