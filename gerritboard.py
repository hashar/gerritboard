"""
Gerrit board

Usage:
    gerritboard.py [options]
    gerritboard.py [options] --html
    gerritboard.py (-h | --help)

Options:
    -h --help   Show this help.
    --split     Generate a table per project
    --owner USER      Filter changes by change owner
    --project PROJECT Filter changes by project.
                      Accept regex: ^integration/.*
    --batch CHUNK_SIZE  Number of changes to retrieve for each Gerrit query
                       [default: 100]
"""

# Python built-in
from datetime import datetime
import operator
import sys

# Pypi (see requirements.txt
import ansicolor
from docopt import docopt
from pygerrit.rest import GerritRestAPI
import prettytable
from prettytable import PrettyTable

# Prevent prettytable from escaping our HTML fields
prettytable.escape = str

args = docopt(__doc__)


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

    def _validate_batch_size(self, size):
        if size > self.MAX_BATCH_SIZE:
            stderr('Batch sizes should be less than 500 due to Gerrit '
                   'internal limit')
            return False
        return True


class GerritFormatter(object):

    """Either 'ansi' (default) or 'html' """
    FORMAT = 'ansi'
    BLANK = ''

    @staticmethod
    def setFormat(format):
        GerritFormatter.FORMAT = format.lower()
        if format == 'html':
            GerritFormatter.BLANK = '&nbsp;'
        else:
            GerritFormatter.BLANK = ''

    def vary_format(func):
        def wrapper(*args, **kwargs):
            res = func(*args, **kwargs)
            if type(res) is tuple and len(res) == 2:
                if GerritFormatter.FORMAT == 'html':
                    return '<div style="background-color: %s">%s</div>' % (
                           res[0], res[1])
                else:
                    return getattr(ansicolor, res[0])(res[1])
            if GerritFormatter.FORMAT == 'html':
                if res == '':
                    return '&nbsp'
            return res
        return wrapper

    @staticmethod
    def Labels(labels):
        return (
            GerritFormatter.CodeReview(labels['Code-Review']),
            GerritFormatter.Verified(labels['Verified'])
        )

    @staticmethod
    @vary_format
    def CodeReview(votes):
        # note precedence!
        if 'rejected' in votes:
            return ('red', 'rejected')
        elif 'approved' in votes:
            return ('green', 'approved')
        elif 'disliked' in votes:
            return ('yellow', 'disliked')
        elif 'recommended' in votes:
            return ('green', 'recommended')
        elif votes == {}:
            return ''
        else:
            return votes

    @staticmethod
    @vary_format
    def Verified(votes):
        # note precedence!
        if 'rejected' in votes:
            return ('red', 'fails')
        elif 'approved' in votes:
            return ('green', 'ok')
        elif 'recommended' in votes:
            return ('yellow', 'need test')
        elif votes == {}:
            return ''
        else:
            return votes

    @staticmethod
    @vary_format
    def Mergeable(merge_info):
        if change['mergeable']:
            return ('cyan', 'mergeable')
        else:
            return ('red', 'conflict')


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
</style>
</head>
<body>"""


def html_footer():
    return "</body>\n</html>"


def stderr(message):
    sys.stderr.write(message)


changes = []

gerrit_query = {}
if args['--owner']:
    gerrit_query['owner'] = args['--owner']
if args['--project']:
    gerrit_query['project'] = args['--project']
if args['--html']:
    GerritFormatter.setFormat('html')

fetcher = GerritChangesFetcher(batch_size=args['--batch'])
for change in fetcher.fetch(query=gerrit_query):
    changes.extend(change)

changes.sort(key=operator.itemgetter('project', 'updated'))


def dump_table(table, project_name=None):
    if project_name is not None:
        print "\nReviews for %s" % (prev_project)
    if args['--html']:
        table
        print table.get_html_string()
    else:
        print table

headers = ['Change', 'Review', 'CI', 'merge']
if not args['--owner']:
    headers.append('owner')
headers.extend(['age', 'updated'])
table = PrettyTable(headers)

prev_project = None
now_seconds = datetime.utcnow().replace(microsecond=0)

if args['--html']:
    print html_header()
for change in changes:

    fields = []
    fields.append(change['_number'])

    fields.extend(GerritFormatter.Labels(change['labels']))
    fields.append(GerritFormatter.Mergeable(change['mergeable']))

    if not args['--owner']:
        fields.append(change['owner']['name'])

    for date_field in ['created', 'updated']:
        date = change[date_field][:-10]

        age = now_seconds - datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        if age.days:
            fields.append('%s days' % age.days)
        else:
            m, s = divmod(age.seconds, 60)
            h, m = divmod(m, 60)
            if h:
                fields.append("%d hours" % h)
            elif m:
                fields.append("%d mins" % m)
            else:
                fields.append("%d secs" % s)

    if change['project'] != prev_project:

        if args['--split']:
            if prev_project is not None:
                dump_table(table, project_name=prev_project)
                table.clear_rows()
        else:
            project_row = [change['project']]
            project_row.extend([GerritFormatter.BLANK for x in
                                range(1, len(table.field_names))])
            table.add_row(project_row)

    prev_project = change['project']
    table.add_row(fields)

if args['--split']:
    dump_table(table, project_name=prev_project)
else:
    dump_table(table)

if args['--html']:
    print html_footer()
