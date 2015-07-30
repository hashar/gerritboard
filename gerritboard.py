"""
Gerrit board

Usage:
    gerritboard.py [options]
    gerritboard.py (-h | --help)

Options:
    -h --help   Show this help.
    --split     Generate a table per project
    --owner USER  Only get change for the given Gerrit USER.
    --batch CHUNK_SIZE  Number of changes to retrieve for each Gerrit query
                       [default: 100]
"""

# Python built-in
from datetime import datetime
import operator
import sys
import time

# Pypi (see requirements.txt
from ansicolor import cyan
from ansicolor import green
from ansicolor import red
from ansicolor import yellow
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


def stderr(message):
    sys.stderr.write(message)


changes = []

gerrit_query = {}
if args['--owner']:
    gerrit_query['owner'] = args['--owner']

fetcher = GerritChangesFetcher(batch_size=args['--batch'])
start = int(time.time())
for change in fetcher.fetch(query=gerrit_query):
    changes.extend(change)
stderr("Retrieved %s changes in %.1g seconds.\n" % (
       len(changes), (time.time() - start)))

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

for change in changes:

    fields = []
    fields.append(change['_number'])

    code_review = change['labels']['Code-Review']
    # note precedence!
    if 'rejected' in code_review:
        fields.append(red('rejected'))
    elif 'approved' in code_review:
        fields.append(green('approved'))
    elif 'disliked' in code_review:
        fields.append(yellow('disliked'))
    elif 'recommended' in code_review:
        fields.append(green('recommended'))
    elif code_review == {}:
        fields.append('')
    else:
        fields.append(code_review)

    verified = change['labels']['Verified']
    # note precedence!
    if 'rejected' in verified:
        fields.append(red('fails', bold=True))
    elif 'approved' in verified:
        fields.append(green('ok'))
    elif 'recommended' in verified:
        fields.append(yellow('need test'))
    elif verified == {}:
        fields.append('')
    else:
        fields.append(verified)

    fields.append(cyan('mergeable')
                  if change['mergeable'] else red('conflict'))

    if not args['--owner']:
        fields.append(change['owner']['name'])

    for date_field in ['created', 'updated']:
        date = change[date_field][:-10]

        age = datetime.now() - datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        if not age.days:
            fields.append('%s secs' % age.seconds)
        else:
            fields.append('%s days' % age.days)

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
