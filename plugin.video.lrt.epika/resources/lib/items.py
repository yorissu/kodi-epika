
#
# items.py – turns Epika's title records into Kodi list items with full metadata and artwork.
#
# What it is for
#	Epika describes every film, series, season and episode as a JSON "product" record. Kodi shows
#	list items (xbmcgui.ListItem) whose info tag (InfoTagVideo) and artwork feed the skin: plot,
#	year, genres, cast, posters, fanart... This module is the translation between the two, so that
#	Epika titles look like a well-scraped local library in any Kodi skin.
#
# What it fills in
#	Title, original title, plot (with audio/subtitle languages and the "available until" date
#	appended), outline, year, duration, genres, age rating (N-7, N-14...), directors, writers,
#	cast, date added, Epika id, trailer, series/season/episode numbers, audio and subtitle stream
#	languages, and artwork: poster (3:4), landscape/thumb (16:9), 1080p fanart and clear logo.
#
# Notes for maintenance
#	Epika records come in two sizes: list records (in rows, search results) are partial, detail
#	records (epika.py: vod/serial) are complete. plugin.py fetches details and merges them with
#	merge() before calling build(). Everything read from a record is type-checked, because the
#	data comes from an undocumented API: a wrong type degrades to "field left empty", never to a
#	crash. Kodi's setters are strict about types (setYear wants an int...), see describe().
#

import html
import re

import xbmc
import xbmcgui

from . import kodi

MOVIE = 'VOD'
SERIAL = 'SERIAL'
EPISODE = 'EPISODE'
SEASON = 'SEASON'

# Epika uses ISO 639-2-ish codes; Kodi wants ISO 639-1 for stream flags.
LANGS = {
	'LIT': 'lt', 'ENG': 'en', 'RUS': 'ru', 'POL': 'pl', 'GER': 'de', 'DEU': 'de', 'FRA': 'fr', 'FRE': 'fr',
	'SPA': 'es', 'ITA': 'it', 'LAV': 'lv', 'EST': 'et', 'UKR': 'uk', 'SWE': 'sv', 'NOR': 'no', 'DAN': 'da',
	'FIN': 'fi', 'CZE': 'cs', 'CES': 'cs', 'JPN': 'ja', 'KOR': 'ko', 'CHI': 'zh', 'ZHO': 'zh', 'POR': 'pt',
	'HUN': 'hu', 'DUT': 'nl', 'NLD': 'nl', 'PER': 'fa', 'FAS': 'fa', 'TUR': 'tr', 'HEB': 'he', 'ARA': 'ar',
}


# Record type: VOD (film), SERIAL, SEASON or EPISODE. Newer responses use "type_", older "type".
def kind(item):
	return item.get('type_') or item.get('type')


# Epika ids are numeric; anything else is not something we can link to.
def item_id(item):
	value = str(item.get('id') or '')
	return value if value.isdigit() else None


# ISO 639-1 code, or '' when unknown. Always [a-z]{2,3}, so it is safe in file names.
def lang2(code):
	if not isinstance(code, str) or not code or code.upper() == 'UND':
		return ''
	value = LANGS.get(code.upper(), code[:2].lower())
	return value if re.match(r'^[a-z]{2,3}$', value) else ''


# value as int, or None if it isn't a number (Epika sends ids and years as int or string).
def number(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


# URL of one picture from Epika's image map, e.g. images['3x4'] for a poster.
# Epika gives a "templateUrl" with {width:315}/{height:177} placeholders that its image server
# scales on request, so any size can be asked for; without a template the fixed "url" is used.
# Links are protocol-relative (//r.dcs.redcdn.pl/...); only https results are returned.
def image(images, aspect, width, height):
	entries = (images or {}).get(aspect) if isinstance(images, dict) else None
	if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
		return None
	entry = entries[0]
	link = entry.get('templateUrl')
	if isinstance(link, str) and link:
		link = re.sub(r'\{width:\d+\}', str(width), link)
		link = re.sub(r'\{height:\d+\}', str(height), link)
	else:
		link = entry.get('url')
	if not isinstance(link, str) or not link:
		return None
	link = 'https:' + link if link.startswith('//') else link
	return link if link.startswith('https://') else None


# Kodi artwork dict for a record: poster, landscape, fanart, clearlogo, thumb and icon.
# "images" holds the covers, "artworks" the large backgrounds; each falls back to the other.
# Episodes use the 16:9 picture as thumb (skins show episode stills), everything else the poster.
# parent is the series' artwork: seasons and episodes take whatever they lack from it.
def artwork(item, parent=None):
	images = item.get('images')
	works = item.get('artworks')
	art = {
		'poster': image(images, '3x4', 600, 800) or image(works, '3x4', 600, 800),
		'landscape': image(images, '16x9', 1280, 720) or image(works, '16x9', 1280, 720),
		'fanart': image(works, '16x9', 1920, 1080) or image(images, '16x9', 1920, 1080),
	}
	logos = item.get('titleTreatmentImages')
	if isinstance(logos, dict):
		for aspect in logos:
			art['clearlogo'] = image(logos, aspect, 800, 310)
			break
	if kind(item) == EPISODE:
		art['thumb'] = art['landscape']
	else:
		art['thumb'] = art['poster'] or art['landscape']
	art['icon'] = art['thumb']
	art = {k: v for k, v in art.items() if v}
	# Seasons and episodes inherit missing art from the series.
	for key, value in (parent or {}).items():
		art.setdefault(key, value)
	if kind(item) == EPISODE and parent and parent.get('poster'):
		art['tvshow.poster'] = parent['poster']
	return art


# Epika's HTML descriptions ("<p>...</p>") as plain text with line breaks, for Kodi's plot.
def plain(text):
	if not isinstance(text, str) or not text:
		return ''
	text = re.sub(r'(?i)<br\s*/?>|</p>\s*', '\n', text)
	text = re.sub(r'<[^>]+>', '', text)
	text = html.unescape(text)
	return re.sub(r'\n{3,}', '\n\n', text).strip()


# Names of the people with one role in Epika's persons map (DIRECTOR, ACTOR, SCRIPTWRITER...).
def _names(persons, role):
	entries = persons.get(role) if isinstance(persons, dict) else None
	return [p['name'] for p in entries or [] if isinstance(p, dict) and isinstance(p.get('name'), str) and p['name']]


# The "name" of each {..., "name": ...} entry, e.g. genre or category names.
def _named(entries):
	return [e['name'] for e in entries or [] if isinstance(e, dict) and isinstance(e.get('name'), str) and e['name']]


# Lithuanian age rating for Kodi's MPAA field. Epika tags titles "N-14" etc.; without a tag the
# numeric rating is used: 0 -> "V" (visiems, for everyone), 7 -> "N-7".
def mpaa(item):
	for tag in item.get('tags') or []:
		name = tag.get('name') if isinstance(tag, dict) else None
		if isinstance(name, str) and re.match(r'^N-\d+$', name):
			return name
	rating = number(item.get('rating'))
	if rating is None:
		return ''
	return 'N-%d' % rating if rating else 'V'


# Epika timestamp to Kodi's date format: 2026-10-01T21:01:00Z -> 2026-10-01 21:01:00 ('' if odd).
def _date(stamp):
	if not isinstance(stamp, str) or not re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d', stamp):
		return ''
	return stamp[:10] + ' ' + stamp[11:19]


def _language_name(code):
	return (xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or code) if code else ''


# "Lithuanian (dubbed), original" from audioTracks, for the plot. UND (undetermined) tracks of
# type ORIGINAL are the film's own language, which Epika doesn't name.
def _audio_line(item):
	parts = []
	for track in item.get('audioTracks') or []:
		if not isinstance(track, dict):
			continue
		code = lang2(track.get('language'))
		name = _language_name(code)
		if track.get('type') == 'DUBBING' and name:
			parts.append('%s (%s)' % (name, kodi.translate(30027)))
		elif track.get('type') == 'ORIGINAL' and not code:
			parts.append(kodi.translate(30028))
		elif name:
			parts.append(name)
	return ', '.join(parts)


# Subtitle languages as a readable list for the plot, e.g. "Lithuanian".
def _subs_line(item):
	codes = [lang2(s.get('language')) for s in item.get('subtitles') or [] if isinstance(s, dict)]
	return ', '.join(_language_name(c) for c in codes if c)


# Series title and season number for an episode. Inside a series listing plugin.py passes them
# in ctx; episodes found elsewhere (search, My List) carry them in their detail record under
# item['season'] = {"number": 1, "serial": {"title": ...}}. Values in ctx win.
def episode_context(item, ctx):
	ctx = dict(ctx or {})
	season = item.get('season')
	if isinstance(season, dict):
		ctx.setdefault('season', number(season.get('number')))
		serial = season.get('serial')
		if isinstance(serial, dict) and isinstance(serial.get('title'), str):
			ctx.setdefault('show', serial['title'])
	return ctx


# Fills the list item's InfoTagVideo with every field Epika provides (see the top of the file).
# ctx carries what the record itself doesn't know: 'show' (series title) and 'season' (number).
# Kodi raises TypeError on a wrong type, so every value is converted or checked before setting,
# and fields Epika leaves empty are simply not set.
def describe(li, item, ctx=None):
	k = kind(item)
	ctx = episode_context(item, ctx) if k == EPISODE else (ctx or {})
	tag = li.getVideoInfoTag()
	title = item.get('title') if isinstance(item.get('title'), str) else ''
	tag.setTitle(title)
	tag.setMediaType({MOVIE: 'movie', SERIAL: 'tvshow', EPISODE: 'episode', SEASON: 'season'}.get(k, 'video'))
	original = item.get('originalTitle')
	if isinstance(original, str) and original and original != title:
		tag.setOriginalTitle(original)

	lead = plain(item.get('lead'))
	plot = plain(item.get('description')) or lead
	extra = []
	audio = _audio_line(item)
	if audio:
		extra.append('%s: %s' % (kodi.translate(30021), audio))
	subs = _subs_line(item)
	if subs:
		extra.append('%s: %s' % (kodi.translate(30022), subs))
	till = _date(item.get('till'))
	if till:
		extra.append(kodi.translate(30020) % till[:10])
	if extra:
		plot = (plot + '\n\n' if plot else '') + '\n'.join(extra)
	tag.setPlot(plot)
	if lead:
		tag.setPlotOutline(lead)

	year = number(item.get('year'))
	if year:
		tag.setYear(year)
	duration = number(item.get('duration'))
	if duration:
		tag.setDuration(duration)
	genres = _named(item.get('genres')) or _named(item.get('categories'))
	if not genres and isinstance(item.get('mainCategory'), dict):
		genres = _named([item['mainCategory']])
	if genres:
		tag.setGenres(genres)
	rating = mpaa(item)
	if rating:
		tag.setMpaa(rating)
	countries = [c if isinstance(c, str) else (c.get('name') if isinstance(c, dict) else None) for c in item.get('countries') or []]
	countries = [c for c in countries if isinstance(c, str) and c]
	if countries:
		tag.setCountries(countries)

	persons = item.get('persons')
	directors = _names(persons, 'DIRECTOR')
	if directors:
		tag.setDirectors(directors)
	writers = _names(persons, 'SCRIPTWRITER')
	if writers:
		tag.setWriters(writers)
	cast = [xbmc.Actor(name, '', order) for order, name in enumerate(_names(persons, 'ACTOR') + _names(persons, 'PRESENTER'))]
	if cast:
		tag.setCast(cast)

	added = _date(item.get('since'))
	if added:
		tag.setDateAdded(added)
	pid = item_id(item)
	if pid:
		tag.setUniqueIDs({'epika': pid}, 'epika')
		if item.get('trailer') and k in (MOVIE, SERIAL):
			tag.setTrailer(kodi.url(action='play', id=pid, video='TRAILER'))

	if k in (EPISODE, SEASON) and isinstance(ctx.get('show'), str):
		tag.setTvShowTitle(ctx['show'])
	season = number(item.get('number')) if k == SEASON else number(ctx.get('season'))
	if k in (EPISODE, SEASON) and season:
		tag.setSeason(season)
	episode = number(item.get('number'))
	if k == EPISODE and episode:
		tag.setEpisode(episode)

	for track in item.get('audioTracks') or []:
		if isinstance(track, dict):
			tag.addAudioStream(xbmc.AudioStreamDetail(2, 'aac', lang2(track.get('language'))))
	for sub in item.get('subtitles') or []:
		code = lang2(sub.get('language')) if isinstance(sub, dict) else ''
		if code:
			tag.addSubtitleStream(xbmc.SubtitleStreamDetail(code))
	# Resolution varies per title (the 'hd' flag is unreliable), so only what is certain.
	video = {'codec': 'h264', 'duration': duration or 0}
	if item.get('uhd') is True:
		video.update(width=3840, height=2160)
	tag.addVideoStream(xbmc.VideoStreamDetail(**video))


# Complete directory entry for one title: (plugin URL, ListItem, is_folder), ready for
# xbmcplugin.addDirectoryItems. Films and episodes are playable items (action=play), series
# are folders (action=serial). Other record types (articles, live channels) and records without
# a numeric id return None and are left out of the listing.
# The context menu gets "Watch trailer" when there is one and, when logged in, "Add to" or
# "Remove from My List" depending on whether the id is in favourites.
def build(item, ctx=None, logged_in=False, favourites=None):
	if not isinstance(item, dict):
		return None
	k = kind(item)
	pid = item_id(item)
	if k not in (MOVIE, SERIAL, EPISODE) or not pid:
		return None
	ctx = ctx or {}
	li = xbmcgui.ListItem(item.get('title') if isinstance(item.get('title'), str) else '', offscreen=True)
	li.setArt(artwork(item, ctx.get('art')))
	describe(li, item, ctx)

	menu = []
	if item.get('trailer') and k in (MOVIE, SERIAL):
		menu.append((kodi.translate(30013), 'PlayMedia(%s)' % kodi.url(action='play', id=pid, video='TRAILER')))
	if logged_in and k in (MOVIE, SERIAL):
		if favourites is not None and pid in favourites:
			menu.append((kodi.translate(30015), 'RunPlugin(%s)' % kodi.url(action='mylist_remove', id=pid)))
		else:
			menu.append((kodi.translate(30014), 'RunPlugin(%s)' % kodi.url(action='mylist_add', id=pid)))
	if menu:
		li.addContextMenuItems(menu)

	if k == SERIAL:
		return kodi.url(action='serial', id=pid), li, True
	li.setProperty('IsPlayable', 'true')
	return kodi.url(action='play', id=pid), li, False


# Combines a list record with its detail record. The detail is complete but may lack fields that
# only lists carry, so it is laid over the list record and its empty values don't erase anything.
def merge(item, detail):
	if not isinstance(detail, dict) or not detail:
		return item
	merged = dict(item)
	for key, value in detail.items():
		if value not in (None, '', [], {}):
			merged[key] = value
	return merged
