"""
Gerrit board

Usage:
    gerritboard.py [options]
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
from ansicolor import cyan
from ansicolor import red
from docopt import docopt
from pygerrit.rest import GerritRestAPI
from prettytable import PrettyTable

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
    VALID_FORMATS = ('ansi', 'html')

    def vary_format(func):
        def wrapper(*args, **kwargs):
            res = func(*args, **kwargs)
            if type(res) is tuple and len(res) == 2:
                if GerritFormatter.FORMAT.lower() == 'html':
                    return '<span style="background-color: %s">%s</span>' % (
                           res[0], res[1])
                else:
                    return getattr(ansicolor, res[0])(res[1])
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


def stderr(message):
    sys.stderr.write(message)


changes = []

gerrit_query = {}
if args['--owner']:
    gerrit_query['owner'] = args['--owner']
if args['--project']:
    gerrit_query['project'] = args['--project']

fetcher = GerritChangesFetcher(batch_size=args['--batch'])
for change in fetcher.fetch(query=gerrit_query):
    changes.extend(change)

changes.sort(key=operator.itemgetter('project', 'updated'))


def dump_table(table, project_name=None):
    if project_name is not None:
        print "\nReviews for %s" % (prev_project)
    print table

headers = ['Change', 'Review', 'CI', 'merge']
if not args['--owner']:
    headers.append('owner')
headers.extend(['age', 'updated'])
table = PrettyTable(headers)

prev_project = None
now_seconds = datetime.utcnow().replace(microsecond=0)

for change in changes:

    fields = []
    fields.append(change['_number'])

    fields.extend(GerritFormatter.Labels(change['labels']))
    fields.append(cyan('mergeable')
                  if change['mergeable'] else red('conflict'))

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
            project_row.extend(['' for x in range(1, len(table.field_names))])
            table.add_row(project_row)

    prev_project = change['project']
    table.add_row(fields)

if args['--split']:
    dump_table(table, project_name=prev_project)
else:
    dump_table(table)
