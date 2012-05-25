import logging
from BeautifulSoup import BeautifulSoup
from flexget.utils import requests
from flexget.entry import Entry
from flexget import plugin

log = logging.getLogger('pogcal')

class InputPogDesign(plugin.Plugin):

    def validator(self):
        from flexget import validator
        config = validator.factory('dict')
        config.accept('text', key='username', required=True)
        config.accept('text', key='password', required=True)
        return config

    name_map = {'The Tonight Show [Leno]': 'The Tonight Show With Jay Leno',
                'Late Show [Letterman]': 'David Letterman'}

    def on_feed_input(self, feed, config):
        try:
            r = requests.post('http://www.pogdesign.co.uk/cat/', data={'username': config['username'], 'password': config['password'], 'sub_login': 'Account Login'}, allow_redirects=True)
            if 'U / P Invalid' in r.text:
                raise plugin.PluginError('Invalid username/password for pogdesign.')
            page = requests.get('http://www.pogdesign.co.uk/cat/showselect.php', cookies=r.cookies)
        except requests.RequestException, e:
            raise plugin.PluginError('Error retrieving source: %s' % e)
        soup = BeautifulSoup(page.text, convertEntities=BeautifulSoup.HTML_ENTITIES)
        entries = []
        for row in soup.findAll('label', {'class': 'label_check' }):
            if row.find(attrs={'checked': 'checked'}):
                t = row.text
                if t.endswith('[The]'): t='The ' + t[:-6]

                # Make certain names friendlier
                if t in self.name_map:
                    t = self.name_map[t]

                e = Entry()
                e['title'] = t
                url = row.findNext('a', {'class': 'selectsummary'})
                e['url'] = 'http://www.pogdesign.co.uk' + url['href']
                entries.append(e)
        return entries

plugin.register_plugin(InputPogDesign, 'pogcal', api_ver=2)
