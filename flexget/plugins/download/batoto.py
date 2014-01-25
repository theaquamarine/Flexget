import HTMLParser
import re
import logging
from os.path import basename, join
from urlparse import urlparse
from datetime import datetime, timedelta
from copy import copy
import requests
from bs4 import BeautifulSoup
from flexget import plugin
from flexget.event import event
from flexget.utils.titles import ID_TYPES, SeriesParser
from flexget.utils.tools import TimedDict
from flexget.plugin import get_plugin_by_name

log = logging.getLogger('batoto')

#The sequence regexp needed to properly handle batoto series if they don't have any *_regexps
seqregexp = 'Ch[\.\s](\d+)'

class Batoto(object):
    """
    Scrapes comics from batoto.net.

    Accepts either chapter pages (from myfollows_rss) and series pages (from recent_rss).

    Adds a sequence_regexp to all series which have no other *regexps to enable parsing of batoto's titles. Strips the
    phrase 'read online' from `title` and `description`.

    Creates a pre-accepted entry for each page in accepted comics and removes the entry representing the entire comic,
    to prevent invalid entry failure errors when attempting to download the html chapter page.
    """

    schema = {'oneOf': [
                {'type': 'boolean', 'enum': [True]},
                {'title': 'language', 'type': 'string'}
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

        if isinstance(config, bool): self.language = None
        else:
            self.language = config.split(' ')
            self.language = [language.title() for language in self.language]
            if 'Any' in self.language or 'None' in self.language: self.language = None
        log.debug('Language set to %s', self.language)

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

    @plugin.priority(150)   #Needs to go before download@128
    def on_task_download(self, task, config):
        for entry in task.accepted:
            url = entry.get('url')
            if not urlparse(url)[1].endswith('batoto.net'):
                log.warning('%s URL is not a batoto URL, ignoring.' % entry.get('title'))
                continue
            if urlparse(url)[1].startswith('img'): continue    #image
            try:
                r = requests.get(url)
                if r.status_code != 200: raise plugin.PluginError(str(r.status_code) + ' error getting ' + str(r.url))
                #r.url or url? r.url can be redirect target.
            except Exception as e:
                entry.fail(unicode(e))
                continue

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
                    entry.reject(unicode('Chapter does not match required language.'))
                    continue
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
            chapterdir = entry.get('filename', join(seriesname, chaptername))
            log.verbose('Saving to ' + chapterdir)
            #Really don't like this- requires path to be set beforehand
            #Making path an argument for plugin doesn't really seem appropriate.
            #way to instruct download to append something to path?
            #TODO: GET A PATH
            entry['path'] = 'C:\\Users\\blue\\Work\\batoto'
            entry['path'] = join(entry.get('path'), chapterdir)
            #entry['subdir'] = chapterdir   #maybe?

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
            pages = self.pages[entry['title']]
            log.debug('In output. Pages = %s' % pages)
            try:
                for file, filename in pages:
                    newentry = copy(entry)
                    newentry['file'] = file
                    newentry['filename'] = filename
                    download.output(task, newentry, {'path': entry.get('path')})
                    log.debug(newentry['output'])
                entry['output'] = entry['path']
            except (plugin.PluginError, plugin.PluginWarning) as e:
                log.error(e)
                entry.fail(e)

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
        url = urlparse(entry.get('url'))
        return url[1].endswith('batoto.net') and url[2].startswith('/comic/_/comics/')

    def url_rewrite(self, task, entry):
        """
        Attempts to get a single chapter from a series page

        Respects language settings. If a series parser is available, will look for a chapter matching 'title'. If not,
        it will attempt to create a temporary parser and use that to match 'title'. Failing that, it will get the most
        recent upload.
        """

        log.verbose('URL looks like a series page. Attempting to get %s' % entry.get('title'))
        if entry['url'] in self.cache and not task.options.nocache:
            log.verbose('Using cached page for %s' % entry['url'])
            text = self.cache[entry['url']]
        else:
            if not task.options.nocache: log.verbose('No cache exists for %s. Getting online.' % entry['url'])
            try:
                r = requests.get(entry['url'])
                if not urlparse(r.url)[2].startswith('/comic/_/comics/'):
                    raise plugin.PluginError('Error getting page %s: Series may not exist at url.' % entry['url'])
            except Exception as e:
                entry.fail(unicode('Error finding chapters. ') + unicode(e))
                raise plugin.PluginWarning('Error encountered while processing %s' % entry.get('title'))
            self.cache[entry['url']] = r.text
            text = r.text
        try:
            soup = BeautifulSoup(text)
            seriesname = soup.find('h1', 'ipsType_pagetitle').text
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
        h = HTMLParser.HTMLParser()
        targetchapter = None
        targettime = None
        targetlanguage = None
        for row in rows:
            #Reject anything we can on language & series info
            if self.language:
                language = [language for language in self.language if 'lang_' + language in row['class']]
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
                #log.debug('Got id: %s' % parser.pack_identifier)
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
        if temp_parser: del entry['series_parser']
        if not targetchapter:
            exitstring = 'Unable to find chapter %s' % entry.get('title')
            if self.language:
                exitstring = exitstring + ' in %s' % self.language
                entry.reject(unicode(exitstring))
            else:
                #Since we're not checking languages, not finding a chapter here is likely an issue.
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
            del(entry['original_url'])

@event('plugin.register')
def register_plugin():
    plugin.register(Batoto, 'batoto', groups=['urlrewriter'], api_ver=2)
