
#
# test_plugin.py – end-to-end tests: plugin URLs are run like Kodi runs them, against FakeEpika.
#
# Each test checks what a user would see or what Kodi would receive: the listed entries and their
# metadata, the requests sent to Epika, notifications, saved files and the resolved playback item.
# Groups: Navigation (menus, pages, rows, series, catalogue), Search, Account (pairing, logout,
# revoked logins, My List, per-account cache), Playback (Widevine setup for Kodi 21 and 22,
# trailers, token safety) and Robustness (forged parameters, server errors, odd data).
#
# Run: python -m unittest discover -s tests
#

import json
import os
import unittest

from helpers import TOKEN, KodiTestCase  # puts the Kodi stubs on sys.path

import inputstreamhelper
import xbmc
import xbmcgui


class Navigation(KodiTestCase):
	def test_root_mirrors_website_menu_logged_out(self):
		state = self.invoke('')
		self.assertEqual(self.labels(state), ['Pradinis', 'Filmai', 'Kategorijos', 'Genres', 'Search', 'Log in'])
		self.assertEqual(state['ends'], [True])

	def test_root_logged_in_has_account_lists(self):
		self.login()
		self.assertEqual(self.labels(self.invoke('')), ['Pradinis', 'Filmai', 'Kategorijos', 'Mano sąrašas', 'Continue watching', 'Genres', 'Search', 'Log out (te***@example.com)'])

	def test_submenu_skips_unsafe_links(self):
		self.assertEqual(self.labels(self.invoke('action=submenu&index=3')), ['Dokumentiniai filmai'])

	def test_view_rows(self):
		state = self.invoke('action=view&label=main')
		# row, catalogue, (watched hidden when logged out), (empty recommender skipped), banner shown as its title
		self.assertEqual(self.labels(state), ['Row one', 'Catalogue', 'Banner film'])
		self.assertIn('action=section&id=11', state['items'][0][0])
		self.assertIn('action=catalog&main=268', state['items'][1][0])

	def test_section_items_have_full_metadata(self):
		state = self.invoke('action=section&id=11')
		self.assertEqual(self.labels(state), ['Test film', 'Test series'])  # the article is dropped
		film = state['items'][0][1]
		data = film.tag.data
		self.assertEqual(data['Plot'].split('\n\n')[0], 'Full plot & more\nSecond')
		self.assertIn('Available until 2026-11-30', data['Plot'])
		self.assertEqual((data['Year'], data['Duration'], data['Mpaa'], data['MediaType']), (2024, 5400, 'N-14', 'movie'))
		self.assertEqual(data['Genres'], ['Drama'])
		self.assertEqual(data['Directors'], ['Dee Rector'])
		self.assertEqual(data['Cast'], ['Ann Actor', 'Bob Actor'])
		self.assertEqual(data['OriginalTitle'], 'Original title')
		self.assertEqual(film.tag.audio, ['lt', ''])
		self.assertEqual(film.tag.subs, ['lt'])
		self.assertEqual(film.art['poster'], 'https://img.example/poster.jpg?dstw=600&dsth=800')
		self.assertEqual(film.art['fanart'], 'https://img.example/fan.jpg?dstw=1920&dsth=1080')
		self.assertEqual(film.props.get('IsPlayable'), 'true')
		self.assertEqual(state['content'], 'movies')

	def test_full_details_can_be_switched_off(self):
		import xbmcaddon
		xbmcaddon.SETTINGS['full_details'] = False
		self.invoke('action=section&id=11')
		self.assertNotIn('/api/products/vods/101', self.server.paths())

	def test_details_are_cached(self):
		self.invoke('action=section&id=11')
		first = len(self.server.requests)
		self.invoke('action=section&id=11')
		self.assertEqual(len(self.server.requests), first)

	def test_single_season_series_lists_episodes(self):
		state = self.invoke('action=serial&id=201')
		self.assertEqual(self.labels(state), ['Test series 1 s.', 'Test series 2 s.'])
		data = state['items'][0][1].tag.data
		self.assertEqual((data['TvShowTitle'], data['Season'], data['Episode'], data['MediaType']), ('Test series', 1, 1, 'episode'))
		self.assertEqual(state['content'], 'episodes')

	def test_multi_season_series_lists_seasons(self):
		state = self.invoke('action=serial&id=202')
		self.assertEqual(self.labels(state), ['1 sezonas', '2 sezonas'])
		self.assertEqual(state['items'][1][1].tag.data['Season'], 2)

	def test_catalog_and_paging(self):
		self.assertEqual(self.labels(self.invoke('action=catalog&main=268')), ['All', 'Komedija'])
		state = self.invoke('action=catalog_list&main=268&cat=300')
		self.assertEqual(self.labels(state)[-1], 'Next page (2/3)')
		self.assertIn('page=1', state['items'][-1][0])

	def test_genres_hide_adult(self):
		self.assertEqual(self.labels(self.invoke('action=genres')), ['Vaidybiniai filmai', 'Komedija'])


class Search(KodiTestCase):
	def test_search_input_saves_history_and_replaces_listing(self):
		xbmcgui.ANSWERS['input'] = '  meilė   ir  '
		state = self.invoke('action=search_input')
		self.assertEqual(state['ends'], [False])
		self.assertEqual(xbmc.BUILTINS, ['Container.Update(plugin://plugin.video.lrt.epika/?action=search&q=meil%C4%97+ir)'])
		self.assertEqual(self.labels(self.invoke('action=search')), ['[B]New search[/B]', 'meilė ir'])

	def test_search_lists_all_kinds_with_episode_context(self):
		state = self.invoke('action=search&q=test')
		self.assertEqual(self.labels(state), ['Test series', 'Test film', 'Test series 1 s.'])
		episode = state['items'][2][1].tag.data
		self.assertEqual((episode['TvShowTitle'], episode['Season']), ('Test series', 1))

	def test_one_failing_search_kind_does_not_hide_others(self):
		self.server.errors[('GET', 'products/vods/search/EPISODE')] = (500, {})
		self.assertEqual(self.labels(self.invoke('action=search&q=test')), ['Test series', 'Test film'])

	def test_forget_history(self):
		os.makedirs(self.profile(), exist_ok=True)
		with open(self.profile('search_history.json'), 'w') as fh:
			json.dump(['a', 'b'], fh)
		self.invoke('action=search_forget&q=a')
		self.assertEqual(self.labels(self.invoke('action=search')), ['[B]New search[/B]', 'b'])


class Account(KodiTestCase):
	def test_pairing_stores_token_only_in_profile(self):
		xbmcgui.ANSWERS['cancel_progress'] = False
		state = self.invoke('action=login&menu=1')
		self.assertEqual(state['ends'], [False])  # menu entry closes its listing
		with open(self.profile('session.json')) as fh:
			session = json.load(fh)
		self.assertEqual(session['token'], TOKEN)
		self.assertEqual(session['account'], 'te***@example.com')
		self.assertNotIn('email', session)
		self.assertIn('Container.Refresh', xbmc.BUILTINS)
		self.assertTrue(any('123 456' in m for m in xbmcgui.PROGRESS))

	def test_pairing_cancelled(self):
		self.invoke('action=login')
		with open(self.profile('session.json'), encoding='utf-8') as fh:
			self.assertIsNone(json.load(fh)['token'])

	def test_logout_revokes_and_forgets(self):
		self.login()
		xbmcgui.ANSWERS['yesno'] = True
		self.invoke('action=logout&menu=1')
		self.assertIn('/api/subscribers/logout', self.server.paths('POST'))
		with open(self.profile('session.json')) as fh:
			session = json.load(fh)
		self.assertIsNone(session['token'])
		self.assertNotEqual(session['device_uid'], 'dev-1')

	def test_revoked_token_is_forgotten(self):
		self.login()
		self.server.errors[('GET', 'subscribers/bookmarks')] = (403, {'code': 'AUTHENTICATION_REQUIRED'})
		state = self.invoke('action=bookmarks&kind=FAVOURITE')
		self.assertEqual(state['ends'], [False])
		with open(self.profile('session.json')) as fh:
			self.assertIsNone(json.load(fh)['token'])
		self.assertTrue(any(n.startswith('ok: Your session has ended') for n in xbmcgui.NOTES))

	def test_auth_error_while_logged_out_keeps_device(self):
		self.server.errors[('GET', 'products/sections/main')] = (403, {'code': 'AUTHENTICATION_REQUIRED'})
		self.invoke('action=view&label=main')
		self.assertIn('Log in first: choose Log in in the LRT Epika menu.', xbmcgui.NOTES)

	def test_my_list(self):
		self.login()
		state = self.invoke('action=bookmarks&kind=FAVOURITE')
		self.assertEqual(self.labels(state), ['Test film'])
		self.assertIn('Remove from My List', [m[0] for m in state['items'][0][1].menu])
		self.invoke('action=mylist_add&id=102')
		self.assertIn('Added to My List', xbmcgui.NOTES)

	def test_my_list_membership_is_cached_and_invalidated(self):
		self.login()
		self.invoke('action=section&id=11')
		self.invoke('action=section&id=11')
		self.assertEqual(self.server.paths().count('/api/subscribers/bookmarks'), 1)
		self.invoke('action=mylist_remove&id=101')
		self.invoke('action=section&id=11')
		self.assertEqual(self.server.paths('GET').count('/api/subscribers/bookmarks'), 2)

	def test_cache_is_per_account(self):
		self.invoke('action=section&id=11')
		count = self.server.paths().count('/api/products/sections/11')
		self.login()
		self.invoke('action=section&id=11')
		self.assertEqual(self.server.paths().count('/api/products/sections/11'), count + 1)


class Playback(KodiTestCase):
	def resolved(self):
		from xbmcplugin import STATE
		self.assertEqual(len(STATE['resolved']), 1)
		return STATE['resolved'][0]

	def test_movie_needs_login(self):
		self.invoke('action=play&id=101')
		ok, li = self.resolved()
		self.assertFalse(ok)
		self.assertNotIn('/api/products/101/videos/playlist', self.server.paths())

	def test_movie_kodi21(self):
		self.login()
		self.invoke('action=play&id=101')
		ok, li = self.resolved()
		self.assertTrue(ok)
		self.assertEqual(li.path, 'https://cdn.example/dash/Manifest.ism')
		self.assertEqual(li.props['inputstream'], 'inputstream.adaptive')
		self.assertEqual(li.props['inputstream.adaptive.license_type'], 'com.widevine.alpha')
		url, headers, rest = li.props['inputstream.adaptive.license_key'].split('|', 2)
		self.assertTrue(url.startswith('https://epika.lrt.lt/api/products/101/drm/widevine'))
		self.assertIn('API-Authentication=' + TOKEN, headers)
		self.assertEqual(rest, 'R{SSM}|')
		self.assertNotIn('inputstream.adaptive.manifest_type', li.props)
		self.assertEqual([os.path.basename(p) for p in li.subtitles], ['Epika.lt.vtt'])  # file:// subtitle refused
		self.assertEqual(li.tag.data['Title'], 'Test film')
		self.assertTrue(xbmc.PLAYER['shown'])

	def test_movie_kodi22_uses_drm_property(self):
		self.login()
		xbmc.BUILD_VERSION[0] = '22.0 (22.0.0) Git:stub'
		self.invoke('action=play&id=101')
		ok, li = self.resolved()
		drm = json.loads(li.props['inputstream.adaptive.drm'])['com.widevine.alpha']['license']
		self.assertIn('API-Authentication=' + TOKEN, drm['req_headers'])
		self.assertNotIn('inputstream.adaptive.license_key', li.props)

	def test_token_never_sent_to_foreign_licence_server(self):
		self.login()
		from helpers import PLAYLIST
		PLAYLIST['drm']['WIDEVINE']['src'] = 'https://evil.example/licence'
		try:
			self.invoke('action=play&id=101')
		finally:
			PLAYLIST['drm']['WIDEVINE']['src'] = 'https://epika.lrt.lt/api/products/101/drm/widevine?platform=BROWSER&type=MOVIE&tenantUid=Lh8t'
		ok, li = self.resolved()
		self.assertNotIn(TOKEN, li.props['inputstream.adaptive.license_key'])

	def test_trailer_needs_no_login_and_no_drm(self):
		self.invoke('action=play&id=101&video=TRAILER')
		ok, li = self.resolved()
		self.assertTrue(ok)
		self.assertEqual(li.path, 'https://cdn.example/trailer.mp4')
		self.assertNotIn('inputstream', li.props)

	def test_widevine_missing(self):
		self.login()
		inputstreamhelper.READY[0] = False
		try:
			self.invoke('action=play&id=101')
		finally:
			inputstreamhelper.READY[0] = True
		self.assertFalse(self.resolved()[0])


class Robustness(KodiTestCase):
	# Each URL tries to smuggle something past routes(): a path instead of an id, an unknown list
	# kind or video type, an unknown action, a negative page. None may reach Epika, and Kodi must
	# get the answer it waits for: a failed listing for folders, a failed playback for play.
	# Every URL starts from a clean state: the previous one is torn down first (unittest tears
	# down the last one).
	def test_rejects_forged_parameters(self):
		cases = (
			('action=section&id=../subscribers/logout', 'listing'),
			('action=view&label=../x', 'listing'),
			('action=bookmarks&kind=EVERYTHING', 'listing'),
			('action=catalog_list&page=-1', 'listing'),
			('action=play&id=1%2F..', 'playback'),
			('action=play&id=101&video=FULL', 'playback'),
			('action=nope', 'nothing'),
		)
		for query, answer in cases:
			with self.subTest(query=query):
				self.tearDown()
				self.setUp()
				state = self.invoke(query)
				self.assertEqual(self.server.requests, [])
				self.assertEqual(state['ends'], [False] if answer == 'listing' else [])
				self.assertEqual([ok for ok, li in state['resolved']], [False] if answer == 'playback' else [])
				self.assertTrue(any('rejected plugin call' in m for level, m in xbmc.LOG))

	def test_network_error_is_reported_not_raised(self):
		self.server.errors[('GET', 'products/sections/11')] = (502, {})
		state = self.invoke('action=section&id=11')
		self.assertEqual(state['ends'], [False])
		self.assertTrue(any('Epika error: HTTP_502' in n for n in xbmcgui.NOTES))

	def test_unexpected_crash_is_contained(self):
		self.server.routes[('GET', 'products/sections/11')] = {'elements': [{'item': {'type': 'VOD', 'id': 101, 'title': ['not', 'a', 'string']}}]}
		state = self.invoke('action=section&id=11')
		self.assertEqual(state['ends'], [True])  # bad title degrades to an empty label, nothing crashes

	def test_garbage_json(self):
		self.server.routes[('GET', 'products/sections/main')] = 'not a list'
		self.assertEqual(self.labels(self.invoke('action=view&label=main')), [])

	def test_corrupt_session_file_is_not_overwritten_blindly(self):
		os.makedirs(self.profile(), exist_ok=True)
		with open(self.profile('session.json'), 'w') as fh:
			fh.write('{"token": ')
		self.invoke('')
		with open(self.profile('session.json')) as fh:
			self.assertEqual(fh.read(), '{"token": ')


if __name__ == '__main__':
	unittest.main()
