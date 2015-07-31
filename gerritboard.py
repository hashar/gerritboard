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

if sys.stdout.encoding is None:
    import codecs
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

    blank = ''

    def __init__(self, owner=None):
        headers = ['Change', 'Review', 'CI', 'merge']
        if owner is None:
            headers.append('owner')
        headers.extend(['age', 'updated'])
        self.table = PrettyTable(headers)

    def colorize(self, color, state):
        return getattr(ansicolor, color)(state)

    def Age(self, gerrit_date):
        gerrit_date = gerrit_date[:-10]

        age = now_seconds - datetime.strptime(gerrit_date, '%Y-%m-%d %H:%M:%S')
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

    def Mergeable(self, merge_info):
        if change['mergeable']:
            return self.colorize('cyan', 'mergeable')
        else:
            return self.colorize('red', 'conflict')


class HTMLGerritFormatter(GerritFormatter):

    blank = '&nbsp;'

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
    formatter = HTMLGerritFormatter(owner=args['--owner'])
else:
    formatter = GerritFormatter(owner=args['--owner'])

fetcher = GerritChangesFetcher(batch_size=args['--batch'])
for change in fetcher.fetch(query=gerrit_query):
    changes.extend(change)

changes.sort(key=operator.itemgetter('project', 'updated'))


def get_table(table, project_name=None):
    out = ''
    if project_name is not None:
        out += "\n\nReviews for %s\n" % (prev_project)
    if args['--html']:
        out += table.get_html_string()
    else:
        out += table.get_string()
    return out

table = formatter.table

prev_project = None
now_seconds = datetime.utcnow().replace(microsecond=0)

out = ''
for change in changes:

    fields = []
    fields.append(formatter.Change(change['_number']))

    fields.extend(formatter.Labels(change['labels']))
    fields.append(formatter.Mergeable(change['mergeable']))

    if not args['--owner']:
        fields.append(change['owner']['name'])

    for date_field in ['created', 'updated']:
        fields.append(formatter.Age(change[date_field]))

    if change['project'] != prev_project:

        if args['--split']:
            if prev_project is not None:
                out += get_table(table, project_name=prev_project)
                table.clear_rows()
        else:
            project_row = [change['project']]
            project_row.extend([formatter.blank for x in
                                range(1, len(table.field_names))])
            table.add_row(project_row)

    prev_project = change['project']
    table.add_row(fields)

    # Last change
    if (len(changes) == changes.index(change) + 1):
        if args['--split']:
            out += get_table(table, project_name=prev_project)
        else:
            out += get_table(table)

if args['--html']:
    print html_header() + out + html_footer()
else:
    print out
