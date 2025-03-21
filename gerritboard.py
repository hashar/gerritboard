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
    --cached  Reuse changes from 'gerritboard_cache'
"""

# Python built-in
from collections import defaultdict
import codecs
from datetime import datetime
import os
import os.path
import operator
import shelve
import sys

# Pypi (see requirements.txt
import ansicolor
from docopt import docopt
from pygerrit2.rest import GerritRestAPI
from prettytable import PrettyTable

if sys.stdout.encoding is None:
    UTF8Writer = codecs.getwriter('utf8')
    sys.stdout = UTF8Writer(sys.stdout)

NOW_SECONDS = datetime.utcnow().replace(microsecond=0)


def formatAccountInfo(acct):
    return acct.get('username', '<deleted #%s>' % acct['_account_id'])


class GerritChangesFetcher(object):

    """Gerrit yields 500 changes at most"""
    MAX_BATCH_SIZE = 500

    def __init__(self, rest_url='https://gerrit.wikimedia.org/r',
                 batch_size=100):

        self.batch = int(batch_size)

        self.rest = GerritRestAPI('https://gerrit.wikimedia.org/r')

    def fetch_chunks(self, query={}):
        if not self._validate_batch_size(self.batch):
            raise Exception('Chunk size %s overflows Gerrit limit %s' % (
                            self.batch, self.MAX_BATCH_SIZE))

        search_operators = {'is': 'open',
                            'limit': str(self.batch),
                            }
        search_operators.update(query)

        has_more_changes = False
        chunk_num = 0
        while True:
            query = [':'.join(t) for t in search_operators.items()]
            endpoint = '/changes/?' + '&'.join([
                'o=LABELS',
                'o=DETAILED_ACCOUNTS',
                'q=' + '%20'.join(query),
            ])
            if has_more_changes:
                endpoint += '&start=%s' % (chunk_num * self.batch)
            ret = self.rest.get(endpoint)

            if not ret:
                return
            chunk_num += 1
            stderr("Retrieved chunk #%s of %s changes\n" % (
                chunk_num, len(ret)))
            yield ret

            has_more_changes = ret[-1].get('_more_changes')
            if not has_more_changes:
                break

    def fetch_all(self, query={}):
        changes = []
        for chunk in self.fetch_chunks(query=query):
            changes.extend(chunk)
        return changes

    def _validate_batch_size(self, size):
        if size > self.MAX_BATCH_SIZE:
            stderr('Batch sizes should be less than 500 due to Gerrit '
                   'internal limit')
            return False
        return True


class AggregateStat(object):

    def __init__(self):
        self.min_created = '9999-12-31 23:59:59'
        self.max_created = ''
        self.min_updated = '9999-12-31 23:59:59'
        self.max_updated = ''
        self.num_changes = 0
        self.num_mergeables = 0
        self.num_conflicts = 0

    def aggregate(self, change):
        self.num_changes += 1

        self.min_created = min(self.min_created, change['created'][:-10])
        self.max_created = max(self.max_created, change['created'][:-10])

        self.min_updated = min(self.min_updated, change['updated'][:-10])
        self.max_updated = max(self.max_updated, change['updated'][:-10])

        if change['mergeable']:
            self.num_mergeables += 1
        else:
            self.num_conflicts += 1

    def __repr__(self):
        return (
            '<GerritStat: %(num_changes)s changes '
            '(%(num_mergeables)s mergeables). '
            'Oldest: %(min_created)s '
            'Last update: %(max_updated)s>'
            % self.__dict__)


class GerritStats(object):

    def __init__(self, changes):
        self.changes = changes

        self.general = AggregateStat()
        self.per_projects = defaultdict(AggregateStat)
        self.per_owners = defaultdict(AggregateStat)

        for change in self.changes:
            self.general.aggregate(change)
            self.per_projects[change['project']].aggregate(change)
            self.per_owners[
                formatAccountInfo(change['owner'])
            ].aggregate(change)


class GerritFormatter(object):

    blank = ''
    project_rows = defaultdict(list)
    stringifier = 'get_string'
    header = ''
    footer = ''
    file_suffix = '.txt'

    def __init__(self, owner=None, split=False):

        headers = ['Change', 'Review', 'CI', 'merge']
        if owner is None:
            headers.append('owner')
        headers.extend(['age', 'updated'])
        self.table_headers = headers

        self.stats_headers = ['Project', 'Open', 'Mergeable', 'Conflicts',
                              'Oldest', 'Last update']

        self.split = True if split else False

    def project_filename(self, project_name):
        return project_name.replace('/', '-') + self.file_suffix

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
                fields.append(formatAccountInfo(change['owner']))

            for date_field in ['created', 'updated']:
                fields.append(self.Age(change[date_field]))

            self.project_rows[change['project']].append(fields)

    def getProjects(self):
        return self.project_rows.keys()

    def getProjectTable(self, project):
        table = PrettyTable(self.table_headers)
        for row in self.project_rows[project]:
            table.add_row(row)
        return getattr(table, self.stringifier)(escape_data=False)

    def getStatsTable(self, changes):

        all_stats = GerritStats(changes)
        table = PrettyTable(self.stats_headers)

        for p in sorted(self.getProjects(), key=str.lower):
            stats = all_stats.per_projects[p]

            fname = self.project_filename(p)

            table.add_row([
                '<a href="%(file)s">%(shortname)s</a>' % {
                    'file': fname,
                    'shortname': fname.rpartition('.')[0],
                    },
                stats.num_changes,
                stats.num_mergeables,
                stats.num_conflicts,
                stats.min_created,
                stats.max_updated,
                ]
            )
        return getattr(table, self.stringifier)(escape_data=False)

    def getTable(self):

        table = PrettyTable(self.table_headers)

        project_old = None
        for (project, rows) in self.project_rows.items():

            if project != project_old:
                # Insert project name as a row
                p_row = [project]
                p_row.extend([self.blank] * (len(self.table_headers) - 1))
                table.add_row(p_row)
            project_old = project

            for row in rows:
                table.add_row(row)

        return getattr(table, self.stringifier)(escape_data=False)

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
        else:
            return self.blank

    def Verified(self, votes):
        # note precedence!
        if 'rejected' in votes:
            return self.colorize('red', 'fails')
        elif 'approved' in votes:
            return self.colorize('green', 'ok')
        elif 'recommended' in votes:
            return self.colorize('yellow', 'need test')
        elif 'all' in votes:
            return self.colorize('yellow', 'need test')
        else:
            return self.blank

    def Mergeable(self, change):
        if change['mergeable']:
            return self.colorize('cyan', 'mergeable')
        else:
            return self.colorize('red', 'conflict')


class HTMLGerritFormatter(GerritFormatter):

    blank = '&nbsp;'
    stringifier = 'get_html_string'
    file_suffix = '.html'

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

    cache_version = 'v1'
    changes = []
    formatter = None
    gerrit_query = {}
    html = False
    output_dir = None
    stats = None

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

        cache = shelve.open('gerritboard_cache')
        cache_key = '%s:owner:%s/project:%s' % (self.cache_version,
                                                args['--owner'],
                                                args['--project'])
        if args['--cached'] and cache_key in cache:
            self.changes = cache[cache_key]
        else:
            fetcher = GerritChangesFetcher(batch_size=self.args['--batch'])
            self.changes = fetcher.fetch_all(query=self.gerrit_query)
            self.changes.sort(key=operator.itemgetter('project', 'updated'))
            cache[cache_key] = self.changes
        cache.close()

        self.formatter.addChanges(self.changes, owner=self.args['--owner'])

        if self.output_dir is None:
            print(self.formatter.generate())
            return 0

        if not os.path.exists(self.output_dir):
            print("Creating %s" % self.output_dir)
            os.makedirs(self.output_dir)

        self.write_projects(self.output_dir)
        if self.html:
            self.write_index(self.output_dir)

    def write_projects(self, output_dir):
        for p in self.formatter.getProjects():
            filename = self.formatter.project_filename(p)
            full_name = os.path.join(output_dir, filename)

            with codecs.open(full_name, 'w', 'utf-8') as f:
                print("Writing %s" % filename)
                f.write(self.formatter.wrapBody(
                    self.formatter.getProjectTable(p)))

    def write_index(self, output_dir):
        fname = os.path.join(output_dir, 'index.html')
        with codecs.open(fname, 'w', 'utf-8') as f:
            print("Writing index.html")
            f.write(self.formatter.wrapBody(
                self.formatter.getStatsTable(self.changes)))


if __name__ == '__main__':
    args = docopt(__doc__)
    gb = GerritBoard(args)
    gb.main()
