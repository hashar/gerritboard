from datetime import datetime
import sys
import time

from ansicolor import cyan
from ansicolor import green
from ansicolor import red
from ansicolor import yellow
from pygerrit.rest import GerritRestAPI
from prettytable import PrettyTable


BATCH_SIZE = 450

rest = GerritRestAPI('https://gerrit.wikimedia.org/r')


def stderr(message):
    sys.stderr.write(message)


def fetch_chunk(size):
    search_operators = {'is': 'open',
                        'owner': 'hashar',
                        'limit': str(size),
                        }
    sortkey = None
    while True:
        if sortkey is not None:
            search_operators['resume_sortkey'] = sortkey
        query = [':'.join(t) for t in search_operators.iteritems()]
        endpoint = '/changes/?o=LABELS&q=' + '%20'.join(query)
        ret = rest.get(endpoint)

        if not ret:
            return
        stderr("Got chunk\n")
        yield ret
        sortkey = ret[-1].get('_sortkey')

if BATCH_SIZE > 500:
    stderr("Batch sizes should be less than 500 due to Gerrit internal limit")
    sys.exit(1)

changes = []

stderr("Gathering changes by chunks of %s changes\n" % str(BATCH_SIZE + 1))
start = int(time.time())
for change in fetch_chunk(size=BATCH_SIZE):
    changes.extend(change)
stderr("Got %s changes.\n" % len(changes))
stderr("Took: %.2f seconds\n" % (time.time() - start))

if False:
    for change in changes:
        """
        u'status': u'NEW',
        u'topic': u'updateregistration',
        u'kind': u'gerritcodereview#change',
        u'created': u'2015-07-29 15:09:10.000000000',
        u'change_id': u'I590098f296e78a92d8b9fa20f8ca80d77738ae82',
        u'labels': {u'Verified': {u'rejected': {u'name': u'jenkins-bot'}},
        u'Code-Review': {}},
        u'updated': u'2015-07-29 15:20:53.000000000',
        u'project': u'mediawiki/extensions/ProofreadPage',
        u'owner': {u'name': u'Niharika29'},
        u'mergeable': True,
        u'branch': u'master',
        u'_sortkey': u'0036c5180003798a',
        u'_number': 227722,
        u'id': u'mediawiki%2Fextensions%2FProofreadPage~master~I590098f296e78a92d8b9fa20f8ca80d77738ae82',
        u'subject': u'Update registration for ProofreadPage extension'
        """
        print '%(status)s %(created) %(updated)' % {change}

table = PrettyTable(['Change', 'Review', 'CI', 'merge', 'project', 'owner', 'age', 'updated'])
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

    fields.append(cyan('mergeable') if change['mergeable'] else red('conflict'))

    fields.append(change['project'])
    fields.append(change['owner']['name'])

    for date_field in ['created', 'updated']:
        date = change[date_field][:-10]

        age = datetime.now() - datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        if not age.days:
            fields.append('%s secs' % age.seconds)
        else:
            fields.append('%s days' % age.days)

    table.add_row(fields)
print table
stderr("Done.\n")
