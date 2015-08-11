#!/usr/bin/env python
"""
Gerrit board

Usage:
    gerritboard.py [options]
    gerritboard.py [options] --html
    gerritboard.py (-h | --help)

Options:
    -h --help   Show this help
    --split     Generate a table per project
    -o --output PATH  Where to write tables
    --owner USER      Filter changes by change owner
    --project PROJECT Filter changes by project
                      Accept regex: ^integration/.*
    --batch CHUNK_SIZE  Number of changes to retrieve for each Gerrit query
                       [default: 100]
"""

# Python built-in
from collections import defaultdict
import codecs
from datetime import datetime
import os
import os.path
import operator
import sys

if sys.stdout.encoding is None:
    UTF8Writer = codecs.getwriter('utf8')
    sys.stdout = UTF8Writer(sys.stdout)

# Pypi (see requirements.txt
import ansicolor
from docopt import docopt
from pygerrit.rest import GerritRestAPI
import prettytable
from prettytable import PrettyTable

# Prevent prettytable from escaping our HTML fields
prettytable.escape = unicode
NOW_SECONDS = datetime.utcnow().replace(microsecond=0)


class GerritChangesFetcher(object):

    """Gerrit yields 500 changes at most"""
    MAX_BATCH_SIZE = 500

    def __init__(self, rest_url='https://gerrit.wikimedia.org/r',
                 batch_size=100):

        self.batch = int(batch_size)

        self.rest = GerritRestAPI('https://gerrit.wikimedia.org/r')

    def fetch(self, query={}):
        if not self._validate_batch_size(self.batch):
            raise Exception('Chunk size %s overflows Gerrit limit %s' % (
                            self.batch, self.MAX_BATCH_SIZE))

        search_operators = {'is': 'open',
                            'limit': str(self.batch),
                            }
        search_operators.update(query)

        sortkey = None
        while True:
            if sortkey is not None:
                search_operators['resume_sortkey'] = sortkey
            query = [':'.join(t) for t in search_operators.iteritems()]
            endpoint = '/changes/?o=LABELS&q=' + '%20'.join(query)
            ret = self.rest.get(endpoint)

            if not ret:
                return
            stderr("Retrieved chunk of %s changes\n" % len(ret))
            yield ret
            sortkey = ret[-1].get('_sortkey')

    def fetch_all(self, query={}):
        changes = []
        for change in self.fetch(query=query):
            changes.extend(change)
        return changes

    def _validate_batch_size(self, size):
        if size > self.MAX_BATCH_SIZE:
            stderr('Batch sizes should be less than 500 due to Gerrit '
                   'internal limit')
            return False
        return True


class GerritFormatter(object):

    blank = ''
    project_rows = defaultdict(list)
    stringifier = 'get_string'
    header = ''
    footer = ''

    def __init__(self, owner=None, split=False):
        headers = ['Change', 'Review', 'CI', 'merge']
        if owner is None:
            headers.append('owner')
        headers.extend(['age', 'updated'])
        self.table_headers = headers
        self.split = True if split else False

    def colorize(self, color, state):
        return getattr(ansicolor, color)(state)

    def generate(self):
        out = ''
        if self.split:
            for project in self.getProjects():
                out += "\n" + self.getProjectTable(project)
        else:
            out = self.getTable()
        return self.wrapBody(out)

    def wrapBody(self, content):
        return self.header + content + self.footer

    def addChanges(self, changes, owner=False):
        for change in changes:
            fields = []
            fields.append(self.Change(change['_number']))

            fields.extend(self.Labels(change['labels']))
            fields.append(self.Mergeable(change))

            if not owner:
                fields.append(change['owner']['name'])

            for date_field in ['created', 'updated']:
                fields.append(self.Age(change[date_field]))

            self.project_rows[change['project']].append(fields)

    def getProjects(self):
        return self.project_rows.keys()

    def getProjectTable(self, project):
        table = PrettyTable(self.table_headers)
        for row in self.project_rows[project]:
            table.add_row(row)
        return getattr(table, self.stringifier)()

    def getTable(self):

        table = PrettyTable(self.table_headers)

        project_old = None
        for (project, rows) in self.project_rows.iteritems():

            if project != project_old:
                # Insert project name as a row
                p_row = [project]
                p_row.extend([self.blank] * (len(self.table_headers) - 1))
                table.add_row(p_row)
            project_old = project

            for row in rows:
                table.add_row(row)

        return getattr(table, self.stringifier)()

    def Age(self, gerrit_date):
        gerrit_date = gerrit_date[:-10]

        age = NOW_SECONDS - datetime.strptime(gerrit_date, '%Y-%m-%d %H:%M:%S')
        if age.days:
            return ('%s days' % age.days)
        else:
            m, s = divmod(age.seconds, 60)
            h, m = divmod(m, 60)
            if h:
                return ("%d hours" % h)
            elif m:
                return ("%d mins" % m)
            else:
                return ("%d secs" % s)

    def Change(self, number):
        return number

    def Labels(self, labels):
        return (
            self.CodeReview(labels['Code-Review']),
            self.Verified(labels['Verified'])
        )

    def CodeReview(self, votes):
        # note precedence!
        if 'rejected' in votes:
            return self.colorize('red', 'rejected')
        elif 'approved' in votes:
            return self.colorize('green', 'approved')
        elif 'disliked' in votes:
            return self.colorize('yellow', 'disliked')
        elif 'recommended' in votes:
            return self.colorize('green', 'recommended')
        elif votes == {}:
            return self.blank
        else:
            return votes

    def Verified(self, votes):
        # note precedence!
        if 'rejected' in votes:
            return self.colorize('red', 'fails')
        elif 'approved' in votes:
            return self.colorize('green', 'ok')
        elif 'recommended' in votes:
            return self.colorize('yellow', 'need test')
        elif votes == {}:
            return self.blank
        else:
            return votes

    def Mergeable(self, change):
        if change['mergeable']:
            return self.colorize('cyan', 'mergeable')
        else:
            return self.colorize('red', 'conflict')


class HTMLGerritFormatter(GerritFormatter):

    blank = '&nbsp;'
    stringifier = 'get_html_string'

    def colorize(self, color, state):
        return '<div class="%(class)s">%(state)s</div>' % {
               'class': 'state-' + state.replace(' ', '-'),
               'state': state}

    def Change(self, number):
        return '<a href="https://gerrit.wikimedia.org/r/{0}">{0}</a>' \
               .format(number)


def html_header():
    return """<DOCTYPE html>
<html lang="en">
<head>
<style type="text/css">
<!-- From MediaWiki core-->
table {
    margin: 1em 0;
    background-color: #f9f9f9;
    border: 1px solid #aaa;
    border-collapse: collapse;
    color: black;
}

table > tr > th,
table > tr > td,
table > * > tr > th,
table > * > tr > td {
    border: 1px solid #aaa;
    padding: 0;
}

table > tr > th,
table > * > tr > th {
    background-color: #f2f2f2;
    text-align: center;
}

table > caption {
    font-weight: bold;
}
div {
    padding: 0em 1em;
    text-align: center;
}

/* Code-Review */
div.state-rejected { background-color: LightCoral; }
div.state-approved { background-color: Chartreuse; }
div.state-disliked { background-color: Khaki; }
div.state-recommended { background-color: LightGreen; }

/* Verified */
div.state-ok { background-color: LightGreen; }
div.state-fails { background-color: LightCoral; }
div.state-need-test { background-color: Khaki; }

/* Mergeable status */
div.state-mergeable { background-color: SkyBlue; }
div.state-conflict { background-color: LightCoral; }

</style>
</head>
<body>
<p>
%(gendate)s
</p>
""" % ({
        'gendate': datetime.utcnow().strftime(
            'Generated %Y-%m-%d %H:%M:%S UTC'),
    })


def stderr(message):
    sys.stderr.write(message)


class GerritBoard(object):

    changes = []
    file_suffix = '.txt'
    formatter = None
    gerrit_query = {}
    html = False
    output_dir = None

    def __init__(self, args):
        self.args = args

        # Gerrit search elements
        if args['--owner']:
            self.gerrit_query['owner'] = args['--owner']
        if args['--project']:
            self.gerrit_query['project'] = args['--project']

        # HTML/ANSI output formatter
        if args['--html']:
            self.html = True
            self.file_suffix = '.html'

            self.formatter = HTMLGerritFormatter(owner=args['--owner'],
                                                 split=args['--split'])
            self.formatter.header = html_header()
            self.formatter.footer = "</body>\n</html>"
        else:
            self.formatter = GerritFormatter(owner=args['--owner'],
                                             split=args['--split'])

        if args['--output']:
            self.output_dir = args['--output']

    def main(self):

        fetcher = GerritChangesFetcher(batch_size=self.args['--batch'])
        self.changes = fetcher.fetch_all(query=self.gerrit_query)
        self.changes.sort(key=operator.itemgetter('project', 'updated'))

        self.formatter.addChanges(self.changes, owner=self.args['--owner'])

        if self.output_dir is None:
            print self.formatter.generate()
            return 0

        if not os.path.exists(self.output_dir):
            print "Creating %s" % self.output_dir
            os.makedirs(self.output_dir)

        files = self.write_projects(self.output_dir)
        if self.html and self.args['--split']:
            self.write_index(self.output_dir, files)

    def write_projects(self, output_dir):
        files = []
        for p in self.formatter.getProjects():
            filename = p.replace('/', '-') + self.file_suffix
            full_name = os.path.join(output_dir, filename)

            with codecs.open(full_name, 'w', 'utf-8') as f:
                print "Writing %s" % filename
                f.write(self.formatter.wrapBody(
                    self.formatter.getProjectTable(p)))
                files.append(filename)
        return files

    def write_index(self, output_dir, files):
            index = '\n'.join(
                ['<a href="%(file)s">%(shortname)s</a><br>' % {
                    'file': fname,
                    'shortname': fname.rpartition('.')[0]}
                 for fname in sorted(files, key=unicode.lower)]
            )
            fname = os.path.join(output_dir, 'index.html')
            with codecs.open(fname, 'w', 'utf-8') as f:
                f.write(self.formatter.wrapBody(index))

if __name__ == '__main__':
    args = docopt(__doc__)
    gb = GerritBoard(args)
    gb.main()
