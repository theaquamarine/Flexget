import requests
from BeautifulSoup import BeautifulSoup
from os.path import basename, join
from urlparse import urlparse
import logging
from flexget.plugin import register_plugin, priority
import HTMLParser
from datetime import datetime, timedelta
import re
from copy import copy

log = logging.getLogger('batoto')

class Batoto(object):

	schema = {'oneOf':[
				{'title': 'no options', 'type': 'boolean', 'enum': [True]},
	            {'title': 'options',
	                'type': 'object',
	            	'properties': {
	                    'language': {'type': 'string'},
	                    'path': {'type': 'string', 'format': 'path'},
                    }
				}]}

	def makesoup(self, url):
		r = requests.get(url)
		if r.status_code != 200:
			log.error(str(r.status_code) + ' error getting ' + str(r.url))
			exit(1)
		return BeautifulSoup(r.text)

	def string_to_time(self, timestring):
		timestring = timestring.replace('[A]', '')
		if timestring.find('ago') != -1:
			value, unit, direction = timestring.split()
			if value.lower() == 'a': value = 1
			if not unit.endswith('s'): unit = unit + 's'
			if direction == 'ago': value *= -1
			delta = timedelta(**{unit: float(value)})
			actualtime = datetime.now() + delta
		else:
			timestring = timestring.replace('Today,', datetime.now().strftime('%d %B %Y -'))
			actualtime = datetime.strptime(timestring, '%d %B %Y - %H:%M %p')
		return actualtime

	def on_task_metainfo(self,task,config):
		if isinstance(config, bool): config = {}
		#Should language default to English or None? Unsure. Best option would be get from system locale.
		self.language = config.get('language', 'english')
		#self.language = config.get('language')
		if isinstance(self.language, basestring): self.language = self.language.title()
		if self.language == 'None': self.language = None
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
				log.warning('%s url is not a batoto url, ignoring.' % entry.get('title'))
				continue

			#confirm we're on a chapter page
			if urlparse(url)[2].startswith('/comic/_/comics/'):
				log.verbose('url looks like a series page. Getting most recent upload')
				soup = self.makesoup(url)
				seriesname = soup.find('h1', 'ipsType_pagetitle').text
				rows = soup.find('table', 'chapters_list').findAll('tr','chapter_row')
				targetchapter = None
				targettime = None
				for row in rows:
					if self.language and not 'lang_' + self.language in row['class'].split(' '): continue
					parser = copy(entry.get('series_parser'))	#Probably don't need?
					tds = row.findAll('td')
					h = HTMLParser.HTMLParser()
					clean_title = seriesname + ' ' + tds[0].text
					clean_title = h.unescape(clean_title)
					clean_title = re.sub('[_.,\[\]\(\):]', ' ', clean_title)
					parser.parse(clean_title)
					if parser.pack_identifier == entry.get('series_parser').pack_identifier:
						chaptertime = self.string_to_time(tds[-1].text)
						if not targettime or chaptertime > targettime:
							targetchapter = row
							targettime = chaptertime
				if not targetchapter:
					exitstring = 'Unable to find chapter %s' % entry.get('title')
					if self.language: exitstring = exitstring + ' in %s' % self.language
					entry.reject(exitstring)
					continue
				else:
					url = targetchapter.find('a')['href']
					entry['url'] = url
					log.debug('Got url %s' % url)

			if not urlparse(url)[2].startswith('/read/'):
				entry.reject('url is not a chapter page.')
				continue

			soup = self.makesoup(url)
			#try/catch errors here- if find()'s returning None, batoto's probably doing something weird.
			language = basename(soup.find('select', {'name':'group_select'}).find('option', {'selected':'selected'})['value'])
			if self.language and language != self.language: entry.fail('Chapter does not match required language.')
			h = HTMLParser.HTMLParser()
			seriesname = h.unescape(soup.find('div', 'moderation_bar').find('a').text.replace(':','-'))
			chaptername = h.unescape(soup.find('select', {'name':'chapter_select'}).find('option', {'selected':'selected'}).text.replace(':','-'))
			pages = soup.find('select', {'name':'page_select'}).findAll('option')
			log.verbose(seriesname + ' ' + chaptername + ': ' + str(len(pages)) + ' pages')

			#customization a la set would be nice here.
			entry['filename'] = entry.get('filename', join(seriesname,chaptername))
			log.verbose('Saving to ' + entry.get('filename'))
			haveworked = True

			urls = []
			filenames = []

			if task.manager.options.test:
				log.info('Would prep pages of ' + seriesname + ' ' + chaptername)
				for page in pages:
					urls.append(page['value'])
			else:
				log.info('Prepping pages of ' + seriesname + ' ' + chaptername + ' - This might take a while!')
				for page in pages:
					soup = self.makesoup(page['value'])
					r = requests.get(soup.find(id='comic_page')['src'])
					#soup.find(id='comic_page')['src'] can return TypeError if find(id='comic_page') has no results
					#requests.get() can return MissingSchema if invalid url
					urls.append(r.url)
					filenames.append(basename(r.url).replace('img',''))
				entry['urls'] = urls
				entry['filenames'] = filenames
			entry['download_all'] = True
		else:
			if not haveworked: log.error('Encountered no batoto urls.')

register_plugin(Batoto, 'batoto', api_ver=2)
