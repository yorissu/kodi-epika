
#
# plugin.py – the add-on's brain: decides what each plugin call does and builds Kodi's listings.
#
# How a Kodi plugin works
#	Kodi calls default.py with a plugin:// URL, e.g. plugin://plugin.video.lrt.epika/?action=section&id=433059.
#	run() reads the "action" and its parameters, checks them and calls the matching Plugin method.
#	A listing method adds items (each with its own plugin:// URL for when it is selected) and ends
#	with endOfDirectory; playback ends with setResolvedUrl; actions such as "Add to My List" just
#	do their work and refresh the screen. Every item URL is built with kodi.url(action=...).
#
# The menu structure, mirroring epika.lrt.lt
#	root          the website's menu (Pradinis, Filmai, Serialai, Vaikams, Kategorijos...), plus
#	              My List, Continue watching, Genres, Search and Log in / Log out
#	view          a website page: its rows (sections); a single-title promo banner shows the title
#	section       all titles of one row
#	catalog       the "Katalogas" row: All + genres → catalog_list, paged, order from settings
#	serial        a series: its seasons, or straight to the episodes when there is only one
#	search        the search menu with recent searches; search_input asks for a new one
#	play          resolves a film, episode or trailer for Kodi's player (player.py)
#
# Safety
#	Any add-on, skin or remote control app can call plugin:// URLs, so nothing in them is trusted:
#	the routes table validates every parameter (ids must be numeric, kinds from fixed lists...)
#	before Epika is asked anything, and rejected calls are only logged. Errors never surface as a
#	Kodi script error: run() catches them, tells the user in a notification and always gives Kodi
#	the answer it is waiting for (a failed listing or a failed playback).
#

import re
import sys
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui
import xbmcplugin

from . import items, kodi, pairing, player
from .epika import Epika, EpikaError, AuthRequired
from .cache import Cache, read_json, write_json
from .session import Session

# Settings → Content → Catalogue order: setting value -> Epika's sort field and direction.
SORTS = {
	'newest': ('createdAt', 'desc'),
	'year_desc': ('year', 'desc'),
	'year_asc': ('year', 'asc'),
	'title': ('title', 'asc'),
}
# What one search looks for, in the order the results are listed.
SEARCH_KINDS = ('SERIAL', 'VOD', 'EPISODE')
# Website menu entries not shown as pages: My List has its own entry in the root menu.
SKIP_VIEWS = {'mano-sarasas'}
# Recent searches kept, and the longest search text sent to Epika.
HISTORY_SIZE = 15
MAX_QUERY = 100


# A plugin URL with parameters we did not produce (another add-on, a stale favourite).
class BadRequest(Exception):
	pass


# Everything one plugin call needs: the login (session), the cache and the Epika client, plus
# one method per action. A new Plugin is created for every call (see run()).
class Plugin:
	def __init__(self):
		self.session = Session(kodi.profile_path('session.json'))
		self.cache = Cache(kodi.profile_path('cache'))
		self.epika = Epika(self.session, self.cache, kodi.setting('language') or 'LIT', log=kodi.log)
		self.detail_ttl = min(max(kodi.setting_int('cache_hours') or 12, 1), 168) * 3600
		self._favourites = None

	# --- helpers ---

	# Adds a folder entry (a menu, a row, a genre...) to the current listing.
	def folder(self, label, link, art=None, plot=None, menu=None):
		li = xbmcgui.ListItem(label, offscreen=True)
		if art:
			li.setArt({k: v for k, v in art.items() if v})
		if plot:
			li.getVideoInfoTag().setPlot(plot)
		if menu:
			li.addContextMenuItems(menu)
		xbmcplugin.addDirectoryItem(kodi.HANDLE, link, li, True)

	# Finishes the listing. content ('movies', 'tvshows', 'episodes'...) lets skins pick fitting
	# views; sort adds Kodi's sort options; cache=False for listings that change (root, My List).
	def end(self, content=None, sort=False, cache=True):
		if content:
			xbmcplugin.setContent(kodi.HANDLE, content)
		if sort:
			for method in (xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_LABEL,
					xbmcplugin.SORT_METHOD_VIDEO_YEAR, xbmcplugin.SORT_METHOD_DURATION):
				xbmcplugin.addSortMethod(kodi.HANDLE, method)
		xbmcplugin.endOfDirectory(kodi.HANDLE, cacheToDisc=cache)

	# For menu entries that run an action instead of opening a folder (Log in, New search): Kodi
	# opened them as a folder and waits for a listing, so it is answered with "no listing", which
	# keeps Kodi on the current screen. Calls from settings buttons have no listing (HANDLE -1).
	@staticmethod
	def no_listing():
		if kodi.HANDLE >= 0:
			xbmcplugin.endOfDirectory(kodi.HANDLE, succeeded=False, cacheToDisc=False)

	# Ids on My List, fetched once per call, to choose between "Add to" and "Remove from My List"
	# in the context menu. A failure only costs the right menu entry, unless the login is gone.
	def favourites(self):
		if self._favourites is None:
			self._favourites = set()
			if self.session.logged_in:
				try:
					self._favourites = self.epika.favourite_ids()
				except AuthRequired:
					raise
				except EpikaError as err:
					kodi.log('favourites failed: %s' % err)
		return self._favourites

	# Adds the full detail record (plot, cast, genres, artwork...) to every film, series and
	# episode of a listing, because list records from Epika are partial. Each title is fetched
	# once even if it appears twice. Settings → Content → "Load full details" switches this off
	# for faster but sparser listings.
	def enrich(self, records):
		if not kodi.setting_bool('full_details'):
			return records
		wanted = []
		for record in records:
			pid = items.item_id(record)
			if pid and items.kind(record) in (items.MOVIE, items.SERIAL, items.EPISODE) and (items.kind(record), pid) not in wanted:
				wanted.append((items.kind(record), pid))
		details = dict(zip(wanted, self.epika.details(wanted, self.detail_ttl)))
		return [items.merge(r, details.get((items.kind(r), items.item_id(r)))) for r in records]

	# Adds titles to the listing with full metadata (items.build); returns the enriched records so
	# the caller can pick the content type. ctx is passed to items.build (series title, season).
	def add_items(self, records, ctx=None):
		records = self.enrich([r for r in records if isinstance(r, dict)])
		favourites = self.favourites() if self.session.logged_in else None
		entries = []
		for record in records:
			built = items.build(record, ctx, self.session.logged_in, favourites)
			if built:
				entries.append(built)
		xbmcplugin.addDirectoryItems(kodi.HANDLE, entries, len(entries))
		return records

	# Kodi content type for a listing: only series -> tvshows, only episodes -> episodes,
	# anything else (films, or a mix as in website rows) -> movies.
	@staticmethod
	def content_for(records):
		kinds = {items.kind(r) for r in records}
		if kinds == {items.SERIAL}:
			return 'tvshows'
		if kinds == {items.EPISODE}:
			return 'episodes'
		return 'movies'

	# Label for one of the website's own translation keys (menu entries like "COMMON_MOVIES"),
	# in the website language from settings. Not to be confused with kodi.translate, which reads
	# the add-on's strings.po. Falls back to fallback, or to the key itself.
	def translate(self, key, fallback=''):
		try:
			value = self.epika.translations().get(key)
		except EpikaError:
			value = None
		return value if isinstance(value, str) and value else (fallback or key)

	# The website's menu entries; an empty list if Epika can't be reached (root still shows
	# Home, Genres and Search, which don't depend on the menu).
	def menu_entries(self):
		try:
			menu = self.epika.menu().get('web')
		except EpikaError as err:
			kodi.log('menu failed: %s' % err, xbmc.LOGWARNING)
			return []
		return [e for e in menu if isinstance(e, dict)] if isinstance(menu, list) else []

	# --- root ---

	# The add-on's main menu: the website's menu as LRT configures it (so new pages appear by
	# themselves), then the add-on's own entries. Entries without a name (logo, separator, search
	# box) and account pages are skipped; entries with children become a submenu.
	def root(self):
		self.folder(self.translate('COMMON_HOME', kodi.translate(30000)), kodi.url(action='view', label='main'))
		for index, entry in enumerate(self.menu_entries()):
			if not isinstance(entry.get('name'), str) or not entry['name']:
				continue  # logo, separator, search box, account dropdown
			label = self.translate(entry['name'])
			if entry.get('elements'):
				self.folder(label, kodi.url(action='submenu', index=index))
				continue
			slug = view_slug(entry.get('url'))
			if slug and slug not in SKIP_VIEWS and not slug.startswith('subscriber'):
				self.folder(label, kodi.url(action='view', label=slug))
		if self.session.logged_in:
			self.folder(self.translate('COMMON_MY_LIST', kodi.translate(30002)), kodi.url(action='bookmarks', kind='FAVOURITE'))
			self.folder(kodi.translate(30003), kodi.url(action='bookmarks', kind='WATCHED'))
		self.folder(kodi.translate(30004), kodi.url(action='genres'))
		self.folder(kodi.translate(30001), kodi.url(action='search'))
		if self.session.logged_in:
			self.folder(kodi.translate(30006) % (self.session.account or ''), kodi.url(action='logout', menu=1))
		else:
			self.folder(kodi.translate(30005), kodi.url(action='login', menu=1))
		self.end(cache=False)

	# Children of a website menu entry (e.g. Kategorijos → Vaidybiniai serialai...). index is the
	# entry's position in the menu, as root() numbered it.
	def submenu(self, index):
		entries = self.menu_entries()
		children = entries[index].get('elements') if index < len(entries) else None
		for child in children if isinstance(children, list) else []:
			slug = view_slug(child.get('url')) if isinstance(child, dict) else None
			if slug and isinstance(child.get('name'), str):
				self.folder(self.translate(child['name']), kodi.url(action='view', label=slug))
		self.end()

	# --- website pages and rows ---

	# One website page (label: main, filmai, vaikams...) as a list of its rows. Epika's row types:
	#	STATIC / ALGORITHMIC     titles chosen by LRT or by rules → folder with the row's titles;
	#	                         a BANNER row with a single title shows that title directly
	#	CATALOG                  "all titles of this kind" → catalog() with its genre filter
	#	SUBSCRIBER_FAVOURITE     My List, SUBSCRIBER_WATCHED Continue watching (only when logged in)
	#	RECOMMENDATION           filled by an external recommender the add-on doesn't use → skipped
	# Each row folder shows its first titles in the plot and the first title's art as background.
	def view(self, label):
		singles = []
		for row in self.epika.view(label):
			if not isinstance(row, dict):
				continue
			kind = row.get('contentType')
			title = row.get('title') if isinstance(row.get('title'), str) else ''
			elements = [e['item'] for e in row.get('elements') or [] if isinstance(e, dict) and isinstance(e.get('item'), dict)]
			if kind == 'CATALOG':
				main = items.item_id(row.get('mainCategory') or {})
				self.folder(title or kodi.translate(30009), kodi.url(action='catalog', main=main))
			elif kind in ('SUBSCRIBER_FAVOURITE', 'SUBSCRIBER_WATCHED'):
				if self.session.logged_in:
					self.folder(title, kodi.url(action='bookmarks', kind=kind.split('_')[1]))
			elif not elements or not items.item_id(row):
				continue  # recommender rows are filled by an external service; empty rows are skipped
			elif len(elements) == 1 and row.get('layout') == 'BANNER':
				singles.append(elements[0])  # promo banner for one title: show the title itself
			else:
				art = items.artwork(elements[0])
				names = ', '.join(e['title'] for e in elements[:8] if isinstance(e.get('title'), str))
				self.folder(title, kodi.url(action='section', id=items.item_id(row)),
					art={'fanart': art.get('fanart'), 'thumb': art.get('landscape'), 'icon': art.get('landscape')}, plot=names)
		if singles:
			self.add_items(singles)
		self.end('videos')

	# All titles of one row, in the website's order (Kodi's sort options are offered as well).
	def section(self, section_id):
		data = self.epika.section(section_id)
		records = [e['item'] for e in data.get('elements') or [] if isinstance(e, dict) and isinstance(e.get('item'), dict)]
		records = self.add_items(records)
		self.end(self.content_for(records), sort=True)

	# --- catalogue (all titles of a type, by genre) ---

	# Genre filter of a catalogue row: "All" plus the genres within the row's main category
	# (main, e.g. 268 = feature films). Adult categories are never listed.
	def catalog(self, main):
		self.folder(kodi.translate(30008), kodi.url(action='catalog_list', main=main))
		for category in self.epika.categories(main):
			cat = items.item_id(category) if isinstance(category, dict) else None
			if cat and cat != main and not category.get('adult') and isinstance(category.get('name'), str):
				self.folder(category['name'], kodi.url(action='catalog_list', main=main, cat=cat))
		self.end()

	# The root menu's Genres: every non-adult category, each opening all its titles.
	def genres(self):
		for category in self.epika.categories():
			cat = items.item_id(category) if isinstance(category, dict) else None
			if cat and category.get('type') == 'VOD' and not category.get('adult') and isinstance(category.get('name'), str):
				self.folder(category['name'], kodi.url(action='catalog_list', cat=cat))
		self.end()

	# One page of titles from the catalogue, filtered by main category and/or genre, sorted as set
	# in Settings → Content. A "Next page (2/5)" entry at the end opens the following page.
	def catalog_list(self, main=None, cat=None, page=0):
		count = min(max(kodi.setting_int('page_size') or 50, 10), 100)
		sort, order = SORTS.get(kodi.setting('sort'), SORTS['newest'])
		data = self.epika.catalog(main, cat, page * count, count, sort, order)
		records = self.add_items(data.get('items') or [])
		total = items.number((data.get('meta') or {}).get('totalCount')) or 0
		if (page + 1) * count < total:
			self.folder('%s (%d/%d)' % (kodi.translate(30007), page + 2, (total + count - 1) // count),
				kodi.url(action='catalog_list', main=main, cat=cat, page=page + 1))
		self.end(self.content_for(records))

	# --- series ---

	# A series: its seasons as folders, each described with the series' metadata and the season
	# number. A series with only one season skips that step and lists its episodes directly.
	def serial(self, serial_id):
		show = self.epika.serial(serial_id, self.detail_ttl)
		seasons = [s for s in self.epika.seasons(serial_id) if isinstance(s, dict) and items.item_id(s)]
		if len(seasons) == 1:
			return self.season(serial_id, items.item_id(seasons[0]))
		show_art = items.artwork(show)
		for season in seasons:
			sid = items.item_id(season)
			li = xbmcgui.ListItem(season.get('title') if isinstance(season.get('title'), str) else '', offscreen=True)
			li.setArt(items.artwork(season, show_art))
			record = items.merge(dict(show, type_=items.SEASON, type=items.SEASON, trailer=False),
				{'title': season.get('title'), 'number': season.get('number'), 'id': sid})
			items.describe(li, record, {'show': show.get('title')})
			xbmcplugin.addDirectoryItem(kodi.HANDLE, kodi.url(action='season', serial=serial_id, id=sid), li, True)
		self.end('seasons')

	# Episodes of one season. Episode records don't name their series or season, so both are
	# passed along, and episodes without own artwork get the series' poster, fanart and logo.
	def season(self, serial_id, season_id):
		show = self.epika.serial(serial_id, self.detail_ttl)
		number = next((s.get('number') for s in self.epika.seasons(serial_id) if isinstance(s, dict) and items.item_id(s) == season_id), None)
		records = [dict(e, type_=items.EPISODE) for e in self.epika.episodes(serial_id, season_id) if isinstance(e, dict)]
		show_art = {k: v for k, v in items.artwork(show).items() if k in ('poster', 'fanart', 'clearlogo')}
		self.add_items(records, {'show': show.get('title'), 'season': number, 'art': show_art})
		xbmcplugin.addSortMethod(kodi.HANDLE, xbmcplugin.SORT_METHOD_EPISODE)
		self.end('episodes')

	# --- account lists ---

	# My List (kind FAVOURITE) or Continue watching (WATCHED) of the logged-in account.
	def bookmarks(self, kind):
		if not pairing.ensure_login(self.epika, self.session):
			return self.end()
		records = self.epika.bookmarks(kind)
		if not records:
			kodi.notify(kodi.translate(30046))
		records = self.add_items(records)
		self.end(self.content_for(records), cache=False)

	# Context menu "Add to / Remove from My List"; refreshes the screen so the menu entry flips.
	def mylist(self, item_id, add):
		if not pairing.ensure_login(self.epika, self.session):
			return
		if add:
			self.epika.bookmark_add('FAVOURITE', item_id)
		else:
			self.epika.bookmark_remove('FAVOURITE', item_id)
		kodi.notify(kodi.translate(30016 if add else 30017))
		xbmc.executebuiltin('Container.Refresh')

	# --- search ---

	@staticmethod
	def history_path():
		return kodi.profile_path('search_history.json')

	# Recent searches, newest first, from profile/search_history.json (kept so the remote rarely
	# has to type). save_history() writes them back, at most HISTORY_SIZE.
	def history(self):
		entries = read_json(self.history_path(), [])
		return [q for q in entries if isinstance(q, str) and q][:HISTORY_SIZE] if isinstance(entries, list) else []

	def save_history(self, entries):
		try:
			write_json(self.history_path(), entries[:HISTORY_SIZE])
		except OSError as err:
			kodi.log('search history not saved: %s' % err, xbmc.LOGWARNING)

	# The Search menu: "New search" and the recent searches, each with a context menu to remove
	# it or clear the whole history.
	def search_menu(self):
		self.folder('[B]%s[/B]' % kodi.translate(30010), kodi.url(action='search_input'))
		for query in self.history():
			self.folder(query, kodi.url(action='search', q=query), menu=[
				(kodi.translate(30011), 'RunPlugin(%s)' % kodi.url(action='search_forget', q=query)),
				(kodi.translate(30012), 'RunPlugin(%s)' % kodi.url(action='search_forget')),
			])
		self.end(cache=False)

	# "New search": shows Kodi's keyboard, stores the text in the history and opens the results.
	# The results are opened with Container.Update instead of being listed here: otherwise this
	# call would stay in Kodi's back history and Back from a title would pop up the keyboard again.
	def search_input(self):
		self.no_listing()
		query = clean_query(xbmcgui.Dialog().input(kodi.translate(30044), type=xbmcgui.INPUT_ALPHANUM))
		if query:
			self.save_history([query] + [q for q in self.history() if q.lower() != query.lower()])
			xbmc.executebuiltin('Container.Update(%s)' % kodi.url(action='search', q=query))

	# Removes one search from the history, or all of them when query is None.
	def search_forget(self, query=None):
		self.save_history([q for q in self.history() if query and q != query])
		xbmc.executebuiltin('Container.Refresh')

	# Search results: series, films and episodes, searched in parallel and listed in that order.
	# One kind failing doesn't hide the others; only a lost login aborts the whole search.
	def search(self, query):
		def one(kind):
			try:
				return self.epika.search(query, kind).get('items') or []
			except AuthRequired:
				raise
			except EpikaError as err:
				kodi.log('search %s failed: %s' % (kind, err), xbmc.LOGWARNING)
				return []
		with ThreadPoolExecutor(max_workers=len(SEARCH_KINDS)) as pool:
			results = list(pool.map(one, SEARCH_KINDS))
		records = [r for group in results for r in group]
		if not records:
			kodi.notify(kodi.translate(30043))
		records = self.add_items(records)
		self.end(self.content_for(records))

	# --- playback ---

	# Plays a film or episode (video=MOVIE, needs a login) or a trailer (video=TRAILER, doesn't).
	# Kodi waits for setResolvedUrl: with the stream on success, with player.fail() otherwise.
	# The title's details are fetched for the player's info screen; if that fails it plays anyway.
	# Afterwards the call stays alive briefly to switch subtitles on (player.py).
	def play(self, item_id, video='MOVIE'):
		if video != 'TRAILER' and not pairing.ensure_login(self.epika, self.session):
			return player.fail()
		playlist = self.epika.playlist(item_id, video)
		info = None
		try:
			info = self.epika.vod(item_id, self.detail_ttl) or None
		except EpikaError:
			pass
		li, subs = player.resolve(self.epika, playlist, video, info)
		if not li:
			return player.fail()
		xbmcplugin.setResolvedUrl(kodi.HANDLE, True, li)
		if subs and kodi.setting_bool('subtitles'):
			try:
				player.show_subtitles_when_started()
			except Exception as err:  # playback already started; never report it as failed
				kodi.log('could not switch subtitles on: %s' % err, xbmc.LOGWARNING)

	# --- account ---

	# Log in from the menu (from_menu: Kodi opened it as a folder) or from the settings button.
	# After a new login the cache is cleared, so listings are fetched again for the account.
	def login(self, from_menu=False):
		if from_menu:
			self.no_listing()
		if self.session.logged_in:
			kodi.notify(kodi.translate(30034) % (self.session.account or ''))
		elif pairing.pair(self.epika, self.session):
			self.cache.clear()
		xbmc.executebuiltin('Container.Refresh')

	# Log out after confirmation: revokes the token on Epika (best effort, e.g. when offline) and
	# always forgets it locally.
	def logout(self, from_menu=False):
		if from_menu:
			self.no_listing()
		if not self.session.logged_in or not xbmcgui.Dialog().yesno(kodi.NAME, kodi.translate(30035)):
			return
		try:
			self.epika.logout()
		except EpikaError as err:
			kodi.log('logout request failed: %s' % err, xbmc.LOGWARNING)
		self.forget_login()
		kodi.notify(kodi.translate(30036))
		xbmc.executebuiltin('Container.Refresh')

	# Forgets the login and everything cached for it.
	def forget_login(self):
		self.session.clear()
		self.cache.clear()

	def clear_cache(self):
		self.cache.clear()
		kodi.notify(kodi.translate(30045))


# --- parameter validation ---
#
# Plugin URLs can come from anywhere (other add-ons, skins, Kodi favourites made with an older
# version), so routes() runs every parameter through one of these checks. A failing check raises
# BadRequest, which run() logs and answers with a failed listing; Epika is never contacted.

# Page name from a website menu link: '${FO_URL}/filmai' -> 'filmai'. Only lower-case letters,
# digits, '-' and '/' are allowed and '..' never, because the name becomes part of an API path.
def view_slug(link):
	if not isinstance(link, str):
		return None
	slug = link.replace('${FO_URL}', '').strip('/')
	return slug if re.match(r'^[a-z0-9][a-z0-9/-]{0,80}$', slug) and '..' not in slug else None


# Search text with whitespace collapsed, at most MAX_QUERY characters.
def clean_query(text):
	text = re.sub(r'\s+', ' ', text or '').strip()
	return text[:MAX_QUERY]


# An Epika id: digits only (at most 12), since it is put into API paths.
def _id(params, key, required=True):
	value = params.get(key)
	if value is None and not required:
		return None
	if not isinstance(value, str) or not value.isdigit() or len(value) > 12:
		raise BadRequest('%s=%r' % (key, value))
	return value


# One of a fixed set of values (list kinds, video types).
def _choice(params, key, allowed, default=None):
	value = params.get(key, default)
	if value not in allowed:
		raise BadRequest('%s=%r' % (key, value))
	return value


def _slug(params, key):
	value = view_slug(params.get(key))
	if not value:
		raise BadRequest('%s=%r' % (key, params.get(key)))
	return value


# A whole number between low and high (page numbers, menu positions).
def _int(params, key, default, low, high):
	value = params.get(key, str(default))
	if not value.isdigit() or not low <= int(value) <= high:
		raise BadRequest('%s=%r' % (key, value))
	return int(value)


def _query(params):
	query = clean_query(params.get('q'))
	return query or None


# Action name -> what to run, with the parameters already validated. The validation happens
# inside each lambda, i.e. only when that action is actually called. To add an action: add a
# Plugin method, a line here, and its name to FOLDERS if it produces a listing.
def routes(plugin, params):
	return {
		'root': plugin.root,
		'submenu': lambda: plugin.submenu(_int(params, 'index', 0, 0, 100)),
		'view': lambda: plugin.view(_slug(params, 'label')),
		'section': lambda: plugin.section(_id(params, 'id')),
		'catalog': lambda: plugin.catalog(_id(params, 'main', required=False)),
		'genres': plugin.genres,
		'catalog_list': lambda: plugin.catalog_list(_id(params, 'main', required=False), _id(params, 'cat', required=False), _int(params, 'page', 0, 0, 10000)),
		'serial': lambda: plugin.serial(_id(params, 'id')),
		'season': lambda: plugin.season(_id(params, 'serial'), _id(params, 'id')),
		'bookmarks': lambda: plugin.bookmarks(_choice(params, 'kind', ('FAVOURITE', 'WATCHED'))),
		'mylist_add': lambda: plugin.mylist(_id(params, 'id'), True),
		'mylist_remove': lambda: plugin.mylist(_id(params, 'id'), False),
		'search': lambda: plugin.search(_query(params)) if _query(params) else plugin.search_menu(),
		'search_input': plugin.search_input,
		'search_forget': lambda: plugin.search_forget(_query(params)),
		'play': lambda: plugin.play(_id(params, 'id'), _choice(params, 'video', ('MOVIE', 'TRAILER'), 'MOVIE')),
		'login': lambda: plugin.login(params.get('menu') == '1'),
		'logout': lambda: plugin.logout(params.get('menu') == '1'),
		'clear_cache': plugin.clear_cache,
	}


# Actions that are folder listings; on failure Kodi must still get an endOfDirectory.
FOLDERS = {'root', 'submenu', 'view', 'section', 'catalog', 'genres', 'catalog_list', 'serial', 'season', 'bookmarks', 'search'}


# Entry point, called by default.py for every plugin URL. Runs the requested action and handles
# whatever goes wrong:
#	BadRequest     a forged or outdated URL: logged, nothing else happens
#	AuthRequired   logged in → the token was revoked: it is forgotten and the user told to log in;
#	               logged out → the user is told this needs a login
#	EpikaError     a notification ("Network error" or "Epika error: CODE"), details in the log
#	anything else  a bug: the traceback goes to the log, the user sees "Something went wrong"
# In every case Kodi still gets its answer (_fail), so it never hangs waiting for a listing.
# Opening the main menu also tidies up: week-old cache entries and old subtitle folders go.
def run(argv=None):
	argv = argv or sys.argv
	params = dict(urllib.parse.parse_qsl(argv[2][1:] if len(argv) > 2 else ''))
	action = params.pop('action', 'root')
	plugin = None
	try:
		plugin = Plugin()
		table = routes(plugin, params)
		if action not in table:
			raise BadRequest('action=%r' % action)
		table[action]()
	except BadRequest as err:
		kodi.log('rejected plugin call: %s' % err, xbmc.LOGWARNING)
		_fail(action)
	except AuthRequired:
		if plugin and plugin.session.logged_in:
			# Token revoked (e.g. device removed on the website): forget it and say so.
			plugin.forget_login()
			xbmcgui.Dialog().ok(kodi.NAME, kodi.translate(30038))
		else:
			kodi.notify(kodi.translate(30048), xbmcgui.NOTIFICATION_WARNING)
		_fail(action)
	except EpikaError as err:
		kodi.log('action %s failed: %s' % (action, err), xbmc.LOGERROR)
		kodi.notify(kodi.translate(30041) if err.code == 'NETWORK' else kodi.translate(30040) % err.code, xbmcgui.NOTIFICATION_ERROR)
		_fail(action)
	except Exception:  # last line of defence: log it, tell the user, keep Kodi's UI consistent
		kodi.log('action %s crashed:\n%s' % (action, traceback.format_exc()), xbmc.LOGERROR)
		kodi.notify(kodi.translate(30047), xbmcgui.NOTIFICATION_ERROR)
		_fail(action)
	if action == 'root' and plugin:
		plugin.cache.prune(7 * 86400)
		player.clean_temp()


# Gives Kodi a "failed" answer of the kind it is waiting for: a failed playback for play, a
# failed listing for folder actions, nothing for actions run with RunPlugin.
def _fail(action):
	if action == 'play':
		player.fail()
	elif action in FOLDERS and kodi.HANDLE >= 0:
		xbmcplugin.endOfDirectory(kodi.HANDLE, succeeded=False)
