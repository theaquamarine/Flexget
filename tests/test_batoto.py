from __future__ import unicode_literals, division, absolute_import
from datetime import datetime, timedelta
from itertools import izip_longest
from nose.plugins.attrib import attr
from tests import FlexGetBase
from flexget.utils.titles import ID_TYPES
from flexget.plugin import get_plugin_by_name
from flexget.plugins.download.batoto import seqregexp

class TestBatoto(FlexGetBase):

    __yaml__ = """
    tasks:
      chapterpages:
        set:
          path: 'adirectory'
        mock:
          - {title: 'Arakawa Under the Bridge Vol.8 Ch.X-8: Distant Thunder',
                url: 'http://www.batoto.net/read/_/62326/arakawa-under-the-bridge_v8_chx-8_by_slowmanga'}
          - {title: 'Arakawa Under the Bridge Vol.1 Ch.2: Bajo el puente de la Gran Estrella',
                url: 'http://www.batoto.net/read/_/167114/arakawa-under-the-bridge_v1_ch2_by_majo-no-fansub'}
        accept_all: yes
        batoto: English

      urltests:
        set:
          path: 'adirectory'
        mock:
          - {title: 'Invalid Chapter Entry',
                url: 'http://www.batoto.net/read/_/62334526623345265456345645645645645645633735'}
          - {title: 'Garbage URL Entry',
                url: 'http://www.batoto.net/sdfsdfsdfsdfsdfsdfasdfgarxcvsdf'}
          - {title: 'Irrelevant URL Entry',
                url: 'http://www.google.com'}
        accept_all: yes
        batoto: English
    """

    @attr(online=True)
    def test_get_chapter_pages(self):
        self.execute_task('chapterpages', options=dict(disable_phases=['output']))

        #Test language matching
        #Expected: accepts chapters matching language, fails others.
        assert self.task.find_entry(category='accepted',
            title='Arakawa Under the Bridge Vol.8 Ch.X-8- Distant Thunder page 000001.jpg'), (
            'Language which should have been accepted was not.')
        assert self.task.find_entry(category='rejected',
            description='Arakawa Under the Bridge Vol.1 Ch.2: Bajo el puente de la Gran Estrella'), (
            'Language which should have been rejected was not.')

        #Test collection of pages from chapter
        #Expected: Correct number of entries (ie correct number of pages)
        assert len(self.task.accepted) == 3, 'Incorrect number of page entries'
        #Could be either wrong number of pages found, extra entries accepted or page entries not being created correctly

        #Test finding urls of pages from chapter correctly
        #Expected: correct urls for each page
        pages = ('http://img.batoto.net/comics/2011/12/15/a/read4ee9e6d43f380/img000001.jpg',
                'http://img.batoto.net/comics/2011/12/15/a/read4ee9e6d43f380/img000002.jpg',
                'http://img.batoto.net/comics/2011/12/15/a/read4ee9e6d43f380/img000003.jpg')
        for entry, url in izip_longest(self.task.entries, pages):
            assert entry.get('url') == url

        #Test parsing chapter page to get series name
        #Expected: accurate series name

        #Test parsing chapter page to get chapter name
        #Expected: accurate chapter name

    @attr(online=True)
    def test_invalid_urls(self):
        self.execute_task('urltests', options=dict(disable_phases=['output']))

        #Test handling of an invalid chapter link
        #Expected: Entry fails, execution continues
        assert self.task.find_entry(category='failed',
            title='Invalid Chapter Entry'), (
            'Entry which should have failed did not.')

        #Test handling of invalid url
        #Expected: Entry fails, execution continues
        assert self.task.find_entry(category='failed',
            title='Garbage URL Entry'), (
            'Entry which should have failed did not.')

        #Test handling of a non-batoto url
        #Expected: Entry is skipped and left alone.
        assert self.task.find_entry(title='Irrelevant URL Entry'), (
            'Entry which should not have been modified was.')

class TestBatotoRewriter(FlexGetBase):

    __yaml__ = """
        tasks:
          match_parser:
            mock:
              - {title: 'Bartender - English - Vol.14 Ch.106: Undesirable Guests (Part 3)',
                    url: 'http://www.batoto.net/comic/_/comics/bartender-r198'}
            series:
              - bartender
            batoto: yes

          temp_parser:
            mock:
              - {title: 'Bartender - English - Vol.14 Ch.106: Undesirable Guests (Part 3)',
                    url: 'http://www.batoto.net/comic/_/comics/bartender-r198'}
            accept_all: yes
            batoto: yes

          no_parser:
            mock:
              - {title: 'WILDLY_INVALID_PARSER', url: 'http://www.batoto.net/comic/_/comics/bartender-r198'}
            accept_all: yes
            batoto: yes

          match_lang:
            mock:
              - {title: 'Nichijou Vol.1 Ch.1', url: 'http://www.batoto.net/comic/_/comics/nichijou-r188'}
            series:
              - nichijou
            batoto: German English Italian

          garbage_series:
            mock:
              - {title: 'Nichijou Vol.1 Ch.1', url: 'http://www.batoto.net/comic/_/comics/ddddddddddddddddddddddddddd'}
            accept_all: yes
    """

    @attr(online=True)
    def test_chapter_match_parser(self):
        #Test chapter matching when entry has a working series_parser.
        #Expected: chapter described in 'title' is accurately selected.
        self.execute_task('match_parser', options=dict(disable_phases=['download', 'output']))
        entry = self.task.find_entry(title='Bartender - English - Vol.14 Ch.106: Undesirable Guests (Part 3)')
        targeturl = 'http://www.batoto.net/read/_/215228/bartender_v14_ch106_by_cityshrimp'
        assert entry['url'] == targeturl, ('Entry url is %s and should be %s' % (entry['url'], targeturl))

    @attr(online=True)
    def test_chapter_match_temp_parser(self):
        #Test chapter matching when creating a temporary series_parser.
        #Expected: a temporary series parser is created and used to accurately pick chapter described in 'title'.
        self.execute_task('temp_parser', options=dict(disable_phases=['download', 'output']))
        entry = self.task.find_entry(title='Bartender - English - Vol.14 Ch.106: Undesirable Guests (Part 3)')
        targeturl = 'http://www.batoto.net/read/_/215228/bartender_v14_ch106_by_cityshrimp'
        assert entry['url'] == targeturl, ('Entry url is %s and should be %s' % (entry['url'], targeturl))

    @attr(online=True)
    def test_chapter_match_no_parser(self):
        #Test chapter matching when unable to create a temporary series_parser.
        #Expected: most recent upload is selected.
        self.execute_task('no_parser', options=dict(disable_phases=['download', 'output']))
        entry = self.task.find_entry(title='WILDLY_INVALID_PARSER')
        #This will break with time. Change it to a long-dead series.
        targeturl = 'http://www.batoto.net/read/_/215228/bartender_v14_ch106_by_cityshrimp'
        assert entry['url'] == targeturl, ('Entry url is %s and should be %s' % (entry['url'], targeturl))

    @attr(online=True)
    def test_chapter_match_multiple_id_match(self): pass
        #Test chapter matching when multiple chapters in target language match.
        #Expected: most recent upload is selected.

    @attr(online=True)
    def test_chapter_match_lang(self):
        #Test chapter matching when multiple chapters in different target languages match.
        #Expected: language priority handles, picks highest-priority language.
        self.execute_task('match_lang', options=dict(disable_phases=['download', 'output']))
        entry = self.task.find_entry(title='Nichijou Vol.1 Ch.1')
        targeturl = 'http://www.batoto.net/read/_/174891/nichijou_v1_ch1_by_kanjiku'
        assert entry['url'] == targeturl, ('Entry url is %s and should be %s' % (entry['url'], targeturl))

    @attr(online=True)
    def test_garbage_series(self):
        #Test attempting to find a chapter in a non-existent series
        #Expected: raise plugin.PluginError('Error getting page %s: Series may not exist at url.' % entry['url'])
        self.execute_task('garbage_series', options=dict(disable_phases=['download', 'output']))
        # assert_raises(TaskAbort, self.execute_task, 'garbage_series',
        #     options=dict(disable_phases=['download', 'output']))
        assert self.task.find_entry('failed', title='Nichijou Vol.1 Ch.1'), 'Entry should have failed.'

class TestBatotoSetup(FlexGetBase):

    __yaml__ = """
        templates:
          testdata:
            set:
              path: 'C:\'
            batoto: yes
            mock:
              - {title: 'Bartender - English - Vol.14 Ch.106: Undesirable Guests (Part 3)',
                    url: 'http://www.batoto.net/read/_/215228/bartender_v14_ch106'}
              - {title: 'Nichijou - English - Vol.6 Ch.94 Read Online',
                    url: 'http://www.batoto.net/read/_/216088/nichijou_v6_ch94'}

        tasks:
          regex_simple:
            template: testdata
            series:
              - bartender


          regex_complex:
            template: testdata
            series:
              - bartender

          regex_parse:
            template: testdata
            series:
              - bartender

          test_from_group:
            template: testdata
            series:
              - bartender: {from_group: 'CityShrimp'}

          regex_noclobber:
            template: testdata
            series:
              - nichijou: {id_regexp: 'Ch[\.\s](\d+(?:.*short \d+)?)'}

          language_bool:
            batoto: yes

          language_string:
            batoto: english french

          language_nullify:
            batoto: english french any
    """

    def test_load_regex_simple(self):
        #Test loading seqregexp into a series with no other configuration
        #Expected: seqregexp loaded correctly
        self.execute_task('regex_simple', options=dict(disable_phases=['download', 'output']))
        for series in self.task.config.get('series'):
            print series
            print type(series)
            assert isinstance(series, dict), 'Modified series should be a dict'
            for seriesitem, properties in series.items():
                assert isinstance(properties, dict), 'Series properties should be a dict'
                assert properties.has_key('sequence_regexp'), 'Modified series should have a `sequence_regexp` key.'
                assert properties.get('sequence_regexp') == seqregexp, '`sequence_regexp` is incorrect.'

    def test_load_regex_complex(self):
        #test loading seqregexp into a series with non-identifier configuration
        #Expected: seqregexp loaded correctly
        self.execute_task('regex_complex', options=dict(disable_phases=['download', 'output']))
        for series in self.task.config.get('series'):
            assert isinstance(series, dict), 'Complex or modified series should be dicts'
            for seriesitem, properties in series.items():
                assert isinstance(properties, dict), 'Series properties should be a dict'
                assert properties.has_key('sequence_regexp'), 'Modified series should have a `sequence_regexp` key.'
                assert properties.get('sequence_regexp') == seqregexp, '`sequence_regexp` is incorrect.'

    def test_load_regex_no_clobber(self):
        #Test loading seqregexp when series has other id_regexps
        #Expected: seqregexp is not loaded
        self.execute_task('regex_noclobber', options=dict(disable_phases=['download', 'output']))
        for series in self.task.config.get('series'):
            assert isinstance(series, dict), 'Complex or modified series should be dicts'
            for seriesitem, properties in series.items():
                print properties
                assert any(properties.get(id_type + '_regexp') for id_type in ID_TYPES), ('Series does not have any ' +
                    'identifier regexps')
                assert isinstance(properties, dict), 'Series properties should be a dict'
                assert properties.get('sequence_regexp') != seqregexp, '`sequence_regexp` should not be loaded.'

    # def test_from_group(self):
    #     #Test handling of series with 'from_group' set
    #     #Expected: issue warning message indicating this breaks batoto
    #     self.execute_task('test_from_group', options=dict(disable_phases=['download', 'output']))
    #     #TODO: actually test something...

    @attr(online=True)
    def test_regex_parsing(self): pass
        #Test seqregexp's ability to correctly parse 'normal' chapter titles
        #Expected: correct series_identifier
        #self.execute_task('regex_parse')
        #Offline version with stored title string?

    def test_language_bool(self):
        self.execute_task('language_bool', options=dict(disable_phases=['download', 'output']))
        batoto = get_plugin_by_name('batoto')
        expectedlanguages = None
        assert batoto.instance.language == expectedlanguages, ('Language should be set to \'None\' but is %s' %
                                                    batoto.instance.language)

    def test_language_string(self):
        self.execute_task('language_string', options=dict(disable_phases=['download', 'output']))
        batoto = get_plugin_by_name('batoto')
        expectedlanguages = ['English', 'French']
        assert batoto.instance.language == expectedlanguages, ('Language should be set to %s but is %s' %
                                                                (expectedlanguages, batoto.instance.language))

    def test_language_nullify(self):
        self.execute_task('language_nullify', options=dict(disable_phases=['download', 'output']))
        batoto = get_plugin_by_name('batoto')
        expectedlanguages = None
        assert batoto.instance.language == expectedlanguages, ('Language should be set to %s but is %s' %
                                                                (expectedlanguages, batoto.instance.language))

class TestStringtoTime(FlexGetBase):

    __yaml__ = """
        tasks:
          stringtotime:
            batoto: yes
    """

    def test_string_to_time(self):
        self.execute_task('stringtotime', options=dict(disable_phases=['download', 'output']))
        batoto = get_plugin_by_name('batoto').instance
        batoto.string_to_time('a second ago') == datetime.now() - timedelta(seconds=1)
        batoto.string_to_time('60 seconds ago') == datetime.now() - timedelta(seconds=60)
        batoto.string_to_time('a minute ago') == datetime.now() - timedelta(minutes=1)
        batoto.string_to_time('60 minutes ago') == datetime.now() - timedelta(minutes=60)
        batoto.string_to_time('a day ago') == datetime.now() - timedelta(days=1)
        batoto.string_to_time('7 days ago') == datetime.now() - timedelta(days=7)
        batoto.string_to_time('a week ago') == datetime.now() - timedelta(weeks=1)
        batoto.string_to_time('4 weeks ago') == datetime.now() - timedelta(weeks=4)
        batoto.string_to_time('Today, %s' % datetime.now().strftime('%H:%M %p')) == datetime.now()