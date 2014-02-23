import HTMLParser
import re
import logging
from os.path import basename, expanduser, splitext
from urlparse import urlsplit, urlunsplit
from datetime import datetime, timedelta
from copy import copy
import requests
from bs4 import BeautifulSoup
from flexget import plugin
from flexget.event import event
from flexget.utils.template import RenderError
from flexget.utils.titles import ID_TYPES, SeriesParser
from flexget.utils.tools import TimedDict
from flexget.plugin import get_plugin_by_name

log = logging.getLogger('batoto')

#The sequence regexp needed to properly handle batoto series if they don't have any *_regexps
seqregexp = 'Ch[\.\s](\d+)'

class Batoto(object):
    """
    Scrapes comics from batoto.net.

    Accepts either chapter pages (from myfollows_rss) and series pages (from recent_rss, via a url rewriter)..

    Adds a sequence_regexp to all series which have no other *regexps to enable parsing of batoto's titles. Strips the
    phrase 'read online' from `title` and `description`.

    Downloads pages of accepted comics to a directory `path`, supporting Jinja templating. Optionally filters releases
    by language, using the `language` setting. Provides the following fields in entries: language, batoto_series,
    chapter_id, chapter_title, volume_number, chapter_number, chapter_name, group, pages.
    language = language of release, batoto_series = series name as set on site, chapter_id = combined volume & chapter,
    chapter_name = combined chapter_id and chapter_title, group = release group, pages = number of pages.

    Page file names can be specified using the `filename` setting, which supports all the previous fields for jinja, as
    well as page_number and extension.

    `Path` is a required setting, `language` defaults to none (ie all languages accepted) and `filename` defaults to
    {{path_number}}{{extension}} if unspecified.

    Examples:
        batoto: '~/comics/{{batoto_series}}/{{chapter_name}}'

        batoto:
          path: '~/comics/{{batoto_series}}/{{chapter_name}}'
          language: english
          filename: '{{batoto_series}}-v{{volume_number}}-c{{chapter_number}}-p{{page_number}}{{extension}}'
    """

    schema = {'oneOf': [
                {'type': 'string', 'format': 'path'},
                {'type': 'object', 'properties': {
                    'path': {'type': 'string', 'format': 'path'},
                    'language': {'type': 'string'},
                    'filename': {'type': 'string'}
                    },
                'required': ['path'],
                'additionalProperties': False
                }
            ]}

    #This applies to all unexpected behaviour. Remember while troubleshooting.
    updatewarning = 'If this is unexpected, site may have changed. Plugin may require updating.'

    def __init__(self):
        self.cache = TimedDict(cache_time='1 hour')
        self.batotoloaded = False

    def on_task_start(self, task, config):
        newconfig = []
        if task.config.get('series'):
            log.debug('Doing identifier regex adjustments')
            for series in task.config.get('series'):
                if not isinstance(series, dict): series = {series: None}
                for seriesitem, properties in series.items():
                    if not isinstance(properties, dict): properties = {}
                    elif properties.get('from_group'):
                        #from_group breaks with batoto as nothing has group info at filtering.
                        #del properties['from_group']
                        #log.debug('Removed group requirement from series %s' % seriesitem)
                        log.warning(('\'from_group\' is set for series \'%s\': This will cause no batoto items to be ' +
                            'accepted for it.') % seriesitem)
                    if not any(properties.get(id_type + '_regexp') for id_type in ID_TYPES):
                        properties.update({'sequence_regexp': seqregexp})
                        if not 'identified_by' in properties:
                            properties.update({'identified_by': 'sequence'})
                        log.debug('Adding sequence regex to series \'%s\'' % seriesitem)
                    series[seriesitem] = properties
                newconfig.append(series)
            task.config['series'] = newconfig

        if isinstance(config, dict) and 'language' in config:
            self.language = config['language'].split(' ')
            self.language = [language.title() for language in self.language]
            if 'Any' in self.language or 'None' in self.language: self.language = None
        else:
            self.language = None
        log.debug('Language set to %s', self.language)

        #if we're supposed to be filtering by language, ensure rss is filtering if it can be.
        if self.language and task.config.get('rss'):
            if isinstance(task.config['rss'], dict):
                rssurl = task.config['rss'].get('url')
            elif isinstance(task.config['rss'], basestring):
                rssurl = task.config['rss']
            rssurl = urlsplit(rssurl)
            if rssurl.netloc == 'www.batoto.net' and rssurl.path == '/myfollows_rss':
                if rssurl.query and rssurl.query.find('l=') == -1:
                    log.debug('Adding language requirements to rss url')
                    query = rssurl.query + '&l=' + '%3B'.join(self.language)
                    rssurl = urlunsplit((rssurl.scheme, rssurl.netloc, rssurl.path, query, rssurl.fragment))
                    log.debug(rssurl)
                    if isinstance(task.config['rss'], dict):
                        task.config['rss']['url'] = rssurl
                    elif isinstance(task.config['rss'], basestring):
                        task.config['rss'] = rssurl

        self.batotoloaded = True
        self.pages = {}

    def on_task_exit(self, task, config):
        self.batotoloaded = False   #lets urlhandler tell if plugin is loaded for current task.
        del self.language
        self.pages = {}

    @plugin.priority(1)
    def on_task_input(self, task, config):
        log.debug('Cleaning titles & descriptions')
        for entry in task.entries:
            if entry.get('title'): entry['title'] = entry.get('title').replace('Read Online','').strip()
            entry['description'] = entry.get('title')

    def on_task_download(self, task, config):
        if isinstance(config, basestring): config = {'path': config}
        path = config['path']
        #Warn if path is static so will result in all images being dumped in one place?

        for entry in task.accepted:
            url = entry.get('url')
            if not urlsplit(url)[1].endswith('batoto.net'):
                log.warning('%s URL is not a batoto URL, ignoring.' % entry.get('title'))
                continue
            if urlsplit(url)[1].startswith('img'): continue    #image
            try:
                r = requests.get(url)
                if r.status_code != 200: raise plugin.PluginError(str(r.status_code) + ' error getting ' + str(r.url))
                #r.url or url? r.url can be redirect target.
            except Exception as e:
                entry.fail(unicode(e))
                continue

            #Are we on a chapter page?
            if not urlsplit(r.url)[2].startswith('/read/'):
                entry.fail(unicode('URL is not a chapter page.'))
                continue

            #Get chapter pages & info, attach to entry for jinja.
            h = HTMLParser.HTMLParser()
            try:
                soup = BeautifulSoup(r.text)
                entry['language'] = basename(soup.find('select', {'name':'group_select'}).find('option',
                    {'selected':'selected'})['value'])
                seriesname = h.unescape(soup.find('div', 'moderation_bar').find('a').text.replace(':','-'))
                entry['batoto_series'] = seriesname    #could use a better name.
                chaptername = h.unescape(soup.find('select', {'name':'chapter_select'}).
                    find('option', {'selected':'selected'}).text)
                chaptersplit = chaptername.split(':', 1)
                entry['chapter_id'] = chaptersplit[0].strip()
                if len(chaptersplit) > 1:
                    entry['chapter_title'] = chaptersplit[1].strip()
                else: entry['chapter_title'] = ''
                chaptersplit = entry['chapter_id'].split('Ch.', 1)
                entry['volume_number'] = chaptersplit[0].replace('Vol.', '').strip()
                entry['chapter_number'] = chaptersplit[1]
                chaptername = chaptername.replace(':','-')
                entry['chapter_name'] = chaptername
                entry['group'] = ' - '.join(soup.find('select', {'name':'group_select'}).find('option',
                    {'selected':'selected'}).text.split(' - ')[:-1])    #in case a group has ' - ' in name.
                pages = soup.find('select', {'name':'page_select'}).findAll('option')
                entry['pages'] = len(pages)
            except (AttributeError, TypeError) as e:
                log.error('Encountered an error finding details on chapter page. Site could have been changed, ' +
                    'plugin update may be required.')
                entry.fail(unicode('Error finding details.'))
                continue
            except Exception as e:
                entry.fail(unicode('Error finding details. ') + unicode(e))
                continue
            log.verbose(seriesname + ' ' + chaptername + ': ' + str(len(pages)) + ' pages')

            if not path in entry: entry['path'] = path
            log.verbose('Saving to ' + entry['path'])

            #Should test if filename varies per page, to avoid collsions?
            #At the moment, results in first page being downloaded to $filename then entry being failed with
            #$filename already exists and is not identical.

            #Prep pages for download
            if task.manager.options.test:
                log.info('Would prep pages of ' + seriesname + ' ' + chaptername)
                #log.debug(pages)
            else:
                log.info('Prepping pages of ' + seriesname + ' ' + chaptername + ' - This might take a while!')
                files = []
                download = get_plugin_by_name('download').instance
                try:
                    for page in pages:
                        #Avoid getting the first page twice if we can
                        if page['value'] != r.url + '/1':
                            r = requests.get(page['value'])
                            if r.status_code != 200: raise plugin.PluginError(str(r.status_code) + ' error getting ' +
                                str(r.url))
                        soup = BeautifulSoup(r.text)
                        image = soup.find(id='comic_page')['src']
                        filename = basename(image).replace('img','')

                        newentry = copy(entry)
                        newentry['title'] = entry['title'] + ' ' + filename
                        newentry['url'] = image
                        newentry['page_number'], newentry['extension'] = splitext(filename)
                        if not 'filename' in newentry:
                            if 'filename' in config:
                                newentry['filename'] = config['filename']
                            else:
                                newentry['filename'] = filename
                        if isinstance(newentry.get('filename'), basestring):
                            newentry['filename'] = newentry.render(newentry.get('filename'))

                        download.get_temp_file(task, newentry, fail_html=False)
                        file = newentry['file'], newentry['filename']
                        files.append(file)
                    self.pages[entry['title']] = files
                except (AttributeError, TypeError) as e:
                    log.error('Encountered an error finding page images in chapter. Site could have been changed, ' +
                        'plugin update may be required.')
                    log.error(e)
                    entry.fail(unicode('Error finding page images.'))
                    continue
                except Exception as e:
                    entry.fail(unicode('Error finding page images. ') + unicode(e))
                    log.error(e)
                    continue

    def on_task_output(self, task, config):
        download = get_plugin_by_name('download').instance
        for entry in task.accepted:
            # expand variables in path
            try:
                entry['path'] = expanduser(entry.render(entry['path']))
            except RenderError as e:
                entry.fail('Could not set path. Error during string replacement: %s' % e)
                continue
            if not task.options.test:
                pages = self.pages[entry['title']]
                log.debug('In output. Pages = %s' % pages)
                try:
                    for file, filename in pages:
                        newentry = copy(entry)
                        newentry['title'] = filename
                        newentry['file'] = file
                        newentry['filename'] = filename
                        download.output(task, newentry, {'path': entry.get('path')})
                        if 'output' in newentry:
                            log.debug('Saved %s to %s' % (newentry['filename'], newentry['output']))
                        if newentry.failed:
                            entry.fail(newentry['reason'])
                            break
                    entry['output'] = entry['path']
                except (plugin.PluginError, plugin.PluginWarning) as e:
                    log.error(e)
                    entry.fail(e)
            else:
                log.info('Would write %s to %s' % (entry['title'], entry.get('path')))

    def string_to_time(self, timestring):
        """
        Turns a fuzzy time ('x days ago', 'A week ago', etc) up to weeks into an absolute datetime.

        :raises: TypeError if given a unit larger than weeks. Weeks is the largest unit used on the website before
        switching to absolute time, so this should never happen.
        """

        timestring = timestring.replace(' [A]', '')
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

    def url_rewritable(self, task, entry):
        #Test batoto is loaded for this task.
        if not self.batotoloaded: return False
        url = urlsplit(entry.get('url'))
        return url[1].endswith('batoto.net') and url[2].startswith('/comic/_/comics/')

    def url_rewrite(self, task, entry):
        """
        Attempts to get a single chapter from a series page

        Respects language settings. If a series parser is available, will look for a chapter matching 'title'. If not,
        it will attempt to create a temporary parser and use that to match 'title'. Failing that, it will get the most
        recent upload.
        """

        # Reject if none of the desired languages are in the entry title.
        if self.language and not any(lang in entry['title'] for lang in self.language):
            entry.reject('Entry not in a desired language.')
            return

        #Grab the series page (filtered by language), soup it and grab details for all chapters.
        log.verbose('URL looks like a series page. Attempting to get %s' % entry.get('title'))
        if entry['url'] in self.cache and not task.options.nocache:
            log.verbose('Using cached page for %s' % entry['url'])
            text = self.cache[entry['url']]
        else:
            if not task.options.nocache: log.verbose('No cache exists for %s. Getting online.' % entry['url'])
            try:
                if self.language:
                    cookie = {'lang_option': '%3B'.join(self.language)}
                else:
                    cookie = None
                r = requests.get(entry['url'], cookies = cookie)
                if not urlsplit(r.url)[2].startswith('/comic/_/comics/'):
                    raise plugin.PluginError('Error getting page %s: Series may not exist at url.' % entry['url'])
            except Exception as e:
                entry.fail(unicode('Error finding chapters. ') + unicode(e))
                log.debug('Error: %s' % e)
                raise plugin.PluginWarning('Error encountered while processing %s' % entry.get('title'))
            self.cache[entry['url']] = r.text
            text = r.text
        try:
            soup = BeautifulSoup(text)
            seriesname = soup.find('h1', 'ipsType_pagetitle').text.strip()
            rows = soup.find('table', 'chapters_list').findAll('tr','chapter_row')
        except plugin.PluginError as e:
            entry.fail(unicode(e))
            raise
        except (AttributeError, TypeError) as e:
            log.error('Encountered an error finding chapters on series page. Site could have been changed, ' +
                'plugin update may be required.')
            entry.fail(unicode('Error finding chapters.'))
            raise plugin.PluginWarning('Error encountered while processing %s' % entry.get('title'))
        except Exception as e:
            entry.fail(unicode('Error finding chapters. ') + unicode(e))
            raise plugin.PluginWarning('Error encountered while processing %s' % entry.get('title'))

        #Try to get a SeriesParser to help us identify the right chapter. Copy series' one if exists, make one if not.
        temp_parser = False
        if entry.get('series_parser'): parser = copy(entry['series_parser'])
        else:
            name = entry.get('title').split(' ')[0]
            parser = SeriesParser(name=name, identified_by='sequence', sequence_regexps=[seqregexp])
            try: parser.parse(entry['title'], field='title')
            except Exception as e:
                parser = None
                log.error(e)
            if parser and parser.valid:
                entry['series_parser'] = copy(parser)
                temp_parser = True
            if parser and not parser.valid: parser = None
            log.debug('Parser = %s' % parser)
        if parser: log.debug('Looking for id: %s' % parser.pack_identifier)
        else: log.warning('Unable to create a parser. Getting most recent chapter instead.')

        #Try to find a chapter matching entry['title']. Default to most recent if >1 match or no parser.
        h = HTMLParser.HTMLParser()
        targetchapter = None
        targettime = None
        for row in rows:
            #Reject anything we can on series info
            tds = row.findAll('td')
            if parser:
                clean_title = seriesname + ' ' + tds[0].text.strip()
                clean_title = h.unescape(clean_title)
                clean_title = re.sub('[_.,\[\]\(\):]', ' ', clean_title)
                parser.parse(clean_title)
                #log.debug('Got id: %s' % parser.pack_identifier)
                if parser.pack_identifier == entry.get('series_parser').pack_identifier:
                    log.debug('Chapter match: %s' % clean_title)
                else: continue

            #See if anything left is a better match than we have
            chaptertime = self.string_to_time(tds[-1].text)
            log.debug('Chapter time: %s' % chaptertime)
            if targettime is not None: log.debug('Chapter conflict: %s vs %s' % (chaptertime, targettime))
            if targettime is None or chaptertime > targettime:
                targetchapter = row
                targettime = chaptertime

        #Clean up & return
        if temp_parser: del entry['series_parser']
        if not targetchapter:
            exitstring = 'Unable to find chapter %s' % entry.get('title')
            entry.fail(unicode(exitstring))
            raise plugin.PluginWarning(exitstring)
            log.debug(self.updatewarning)
        else:
            try:
                url = targetchapter.find('a')['href']
            except Exception as e:
                entry.fail(unicode(e))
                raise plugin.PluginWarning('Error encountered while processing %s' % entry.get('title'))
            entry['url'] = url
            entry['original_url'] = url

@event('plugin.register')
def register_plugin():
    plugin.register(Batoto, 'batoto', groups=['urlrewriter'], api_ver=2)
