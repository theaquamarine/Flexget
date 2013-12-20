import requests
from BeautifulSoup import BeautifulSoup
from os.path import basename, join
from urlparse import urlparse
import logging
from flexget.plugin import register_plugin, priority
from time import strptime

log = logging.getLogger('batoto')

class Batoto(object):

	schema = {'oneOf':[
				{'title': 'no options', 'type': 'boolean', 'enum': [True]},
	            {'title': 'options',
	                'type': 'object',
                	'properties': {
                    'language': {'type': 'string'}
                    }
				}]}

	def makesoup(self, url):
		r = requests.get(url)
		if r.status_code != 200:
			log.error(r.status_code + ' error getting ' + r.url)
			exit(1)
		return BeautifulSoup(r.text)

	def on_task_metainfo(self,task,config):
		if isinstance(config, bool): config = {}
		#Should language default to English or None? Unsure. Best option would be get from system locale.
		#self.language = config.get('language', 'english')
		self.language = config.get('language')
		if isinstance(self.language, basestring): self.language = self.language.title()
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
				rows = soup.find('table', 'chapters_list').findAll('tr','row')
				newesttime = None
				newestchapter = None
				for row in rows:
				    if self.language and not 'lang_' + self.language in row['class'].split(' '): continue
				    chaptertime = strptime(row.findAll('td')[-1].text, '%d %B %Y - %H:%M %p')
				    if chaptertime > newesttime:
				    	newesttime = chaptertime
				    	newestchapter = row
				else: entry.fail('Unable to find any chapters matching language %s', self.lang)
				url = newestchapter.find('a')['href']
				log.debug('Got url %s' % url)

			if not urlparse(url)[2].startswith('/read/'): entry.fail('url is not a chapter page.')

			soup = self.makesoup(url)
			#try/catch errors here- if find()'s returning None, batoto's probably doing something weird.
			language = basename(soup.find('select', {'name':'group_select'}).find('option', {'selected':'selected'})['value'])
			if self.language and language != self.language: entry.fail('Chapter does not match required language.')
			seriesname = soup.find('div', 'moderation_bar').find('a').text.replace(':','-')
			chaptername = soup.find('select', {'name':'chapter_select'}).find('option', {'selected':'selected'}).text.replace(':','-')
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
