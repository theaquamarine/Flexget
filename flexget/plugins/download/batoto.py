import HTMLParser
import re
import logging
from os.path import basename, join
from urlparse import urlparse
from datetime import datetime, timedelta
from copy import copy
import requests
from BeautifulSoup import BeautifulSoup
from flexget.plugin import register_plugin, priority, PluginError, PluginWarning
from flexget.utils.titles import ID_TYPES

log = logging.getLogger('batoto')

class Batoto(object):

	schema = {'title': 'language', 'type': 'string'}

	#This applies to all unexpected behaviour. Remember while troubleshooting.
	updatewarning = 'If this is unexpected, site may have changed. Plugin may require updating.'

	@priority(150)	#Needs to run before series@125
	def on_task_metainfo(self, task, config):
		#Add the sequence regexp needed to properly handle batoto series if they don't have any *_regexps
		seqregexp = {'sequence_regexp': 'Ch[\.\s](\d+)'}
		newconfig = []
		if task.config.get('series'):
			for series in task.config.get('series'):
				if not isinstance(series, dict): series = {series: None}
				for seriesitem, properties in series.items():
					if not isinstance(properties, dict): properties = {}
					if not any(properties.get(id_type + '_regexp') for id_type in ID_TYPES):
						properties.update(seqregexp)
						log.debug('Adding sequence regex to series \'%s\'' % seriesitem)
					series[seriesitem] = properties
				newconfig.append(series)
			task.config['series'] = newconfig

		self.language = config.split(' ')
		self.language = [language.title() for language in self.language]
		if 'Any' in self.language or 'None' in self.language: self.language = None
		log.debug('Language set to %s', self.language)

		for entry in task.entries:
			if entry.get('title'): entry['title'] = entry.get('title').replace('Read Online','').strip()
			entry['description'] = entry.get('title')

	@priority(150)	#Needs to go before download@128
	def on_task_download(self, task, config):

		haveworked = False

		for entry in task.accepted:
			url = entry.get('url')
			if not urlparse(url)[1].endswith('batoto.net'):
				log.warning('%s URL is not a batoto URL, ignoring.' % entry.get('title'))
				continue

			try:
				r = requests.get(url)
				if r.status_code != 200: raise PluginError(str(r.status_code) + ' error getting ' + str(r.url))
				#r.url or url? r.url can be redirect target.
			except Exception as e:
				entry.fail(unicode(e))
				continue

			#Are we on a series page? If so, try to get chapter page.
			if urlparse(r.url)[2].startswith('/comic/_/comics/'):
				try: r = self.get_chapter(entry, r)
				except PluginWarning: continue

			#Are we on a chapter page?
			if not urlparse(r.url)[2].startswith('/read/'):
				entry.fail(unicode('URL is not a chapter page.'))
				continue

			#Get chapter pages & info
			h = HTMLParser.HTMLParser()
			try:
				soup = BeautifulSoup(r.text)
				language = basename(soup.find('select', {'name':'group_select'}).
					find('option', {'selected':'selected'})['value'])
				if self.language and language not in self.language:
					entry.fail(unicode('Chapter does not match required language.'))
				seriesname = h.unescape(soup.find('div', 'moderation_bar').find('a').text.replace(':','-'))
				chaptername = h.unescape(soup.find('select', {'name':'chapter_select'}).
					find('option', {'selected':'selected'}).text.replace(':','-'))
				pages = soup.find('select', {'name':'page_select'}).findAll('option')
			except (AttributeError, TypeError) as e:
				log.error('Encountered an error finding details on chapter page. Site could have been changed, ' +
					'plugin update may be required.')
				entry.fail(unicode('Error finding details.'))
				continue
			except Exception as e:
				entry.fail(unicode('Error finding details. ') + unicode(e))
				continue
			log.verbose(seriesname + ' ' + chaptername + ': ' + str(len(pages)) + ' pages')

			#customization a la set would be nice here.
			entry['filename'] = entry.get('filename', join(seriesname,chaptername))
			log.verbose('Saving to ' + entry.get('filename'))
			haveworked = True

			urls = []
			filenames = []

			#Prep pages for download
			if task.manager.options.test:
				log.info('Would prep pages of ' + seriesname + ' ' + chaptername)
				for page in pages:
					urls.append(page['value'])
			else:
				log.info('Prepping pages of ' + seriesname + ' ' + chaptername + ' - This might take a while!')
				try:
					for page in pages:
						r = requests.get(page['value'])
						if r.status_code != 200: raise PluginError(str(r.status_code) + ' error getting ' + str(r.url))
						soup = BeautifulSoup(r.text)
						image = requests.get(soup.find(id='comic_page')['src'])
						if image.status_code != 200: raise PluginError(str(image.status_code) + ' error getting ' +
							str(image.url))
						urls.append(image.url)
						filenames.append(basename(image.url).replace('img',''))
					entry['urls'] = urls
					entry['filenames'] = filenames
				except (AttributeError, TypeError) as e:
					log.error('Encountered an error finding page images in chapter. Site could have been changed, ' +
						'plugin update may be required.')
					entry.fail(unicode('Error finding page images.'))
					continue
				except Exception as e:
					entry.fail(unicode('Error finding page images. ') + unicode(e))
					continue
			entry['download_all'] = True
		else:
			if not haveworked: log.error('Encountered no batoto URLs.')

	def get_chapter(self, entry, r):
		"""Attempts to get a single chapter from a series page, respecting language settings. If a series parser is
			available, will look for a chapter matching 'title'. If not, it will get the most recent upload.

		:raises: PluginWarning for errors it handles, mostly to allow caller to except PluginWarning: continue
		"""

		if entry.get('series_parser'): log.verbose('URL looks like a series page. Attempting to get %s'
			% entry.get('title'))
		else: log.verbose('URL looks like a series page. Attempting to get most recent chapter.')
		try:
			soup = BeautifulSoup(r.text)
			seriesname = soup.find('h1', 'ipsType_pagetitle').text
			rows = soup.find('table', 'chapters_list').findAll('tr','chapter_row')
		except (AttributeError, TypeError) as e:
			log.error('Encountered an error finding chapters on series page. Site could have been changed, ' +
				'plugin update may be required.')
			entry.fail(unicode('Error finding chapters.'))
			raise PluginWarning('Error encountered while processing %s' % entry.get('title'))
		except Exception as e:
			entry.fail(unicode('Error finding chapters. ') + unicode(e))
			raise PluginWarning('Error encountered while processing %s' % entry.get('title'))
		parser = copy(entry.get('series_parser'))
		h = HTMLParser.HTMLParser()
		targetchapter = None
		targettime = None
		targetlanguage = None
		for row in rows:
			#Reject anything we can on language & series info
			if self.language:
				classes = row['class'].split(' ')
				language = [language for language in self.language if 'lang_' + language in classes]
				if not language: continue
				else:
					language = language[0]
					chapterlanguage = self.language.index(language)
			tds = row.findAll('td')
			if parser:
				clean_title = seriesname + ' ' + tds[0].text
				clean_title = h.unescape(clean_title)
				clean_title = re.sub('[_.,\[\]\(\):]', ' ', clean_title)
				parser.parse(clean_title)
				if parser.pack_identifier == entry.get('series_parser').pack_identifier:
					log.debug('Chapter match: %s' % clean_title)
				else: continue

			#See if anything left is a better match than we have
			chaptertime = self.string_to_time(tds[-1].text)
			if self.language:
				log.debug('Chapter language: %s, priority %s' % (language, chapterlanguage))
				if targetlanguage is not None: log.debug('Chapter conflict: %s(%s) vs %s(%s)'
					% (language, chapterlanguage, self.language[targetlanguage], targetlanguage))
				if targetlanguage is None or chapterlanguage < targetlanguage:
					#lower = listed sooner = higher priority
					targetlanguage = chapterlanguage
					targetchapter = row
					targettime = chaptertime
					continue
				elif chapterlanguage == targetlanguage: pass
				else: continue
			log.debug('Chapter time: %s' % chaptertime)
			if targettime is not None: log.debug('Chapter conflict: %s vs %s' % (chaptertime, targettime))
			if targettime is None or chaptertime > targettime:
				targetchapter = row
				targettime = chaptertime
				if self.language: targetlanguage = chapterlanguage
		if not targetchapter:
			exitstring = 'Unable to find chapter %s' % entry.get('title')
			if self.language:
				exitstring = exitstring + ' in %s' % self.language
				entry.reject(unicode(exitstring))
			else: entry.fail(unicode(exitstring))
			log.debug(self.updatewarning)
			raise PluginWarning(exitstring)
		else:
			try:
				url = targetchapter.find('a')['href']
				entry['url'] = url
				log.debug('Got url %s' % url)
				r = requests.get(url)
				if r.status_code != 200: raise PluginError(str(r.status_code) + ' error getting ' + str(r.url))
			except Exception as e:
				entry.fail(unicode(e))
				raise PluginWarning('Error encountered while processing %s' % entry.get('title'))
			return r

	def string_to_time(self, timestring):
		timestring = timestring.replace('[A]', '')
		if timestring.find('ago') != -1:
			value, unit, direction = timestring.split()
			if value.lower() == 'a' or value.lower() == 'an': value = float(1)
			else: value = float(value)
			if not unit.endswith('s'): unit = unit + 's'
			if direction == 'ago': value *= -1
			delta = timedelta(**{unit: value})
			actualtime = datetime.now() + delta
		else:
			timestring = timestring.replace('Today,', datetime.now().strftime('%d %B %Y -'))
			actualtime = datetime.strptime(timestring, '%d %B %Y - %H:%M %p')
		return actualtime

register_plugin(Batoto, 'batoto', api_ver=2)
