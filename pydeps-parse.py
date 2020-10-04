'''

python3 pydeps-parse.py \
    -i /path/wmcore.dot \
    -l 2

'''

import pprint
import logging
import itertools
import copy
import json

from functools import total_ordering

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("-i","--input-file", \
  help="path to input dot graphviz file", \
  type=str, \
  required=True, \
  )
parser.add_argument("-l","--level", \
  help="how deep to group, from the top", \
  type=int, \
  required=False, \
  default=3
  )
args = parser.parse_args()

def remove_src_python(node):
    node = node.strip() # remove trailing spaces
    if node.startswith("src_python_"):
        node = node[len("src_python_"):] # remove "src_python"
    return node

def shorten(node):
    '''
    keep only u to the level-nth level
    '''
    level = args.level
    levels = [i for i in range(len(node)) if node[i] == "_"] # list of index of "_" char
    snode = node[:levels[level-1]] if level <= len(levels) else node
    return snode

def filter(lines):
    lines_f = []
    exclude_patterns = [
        "bson", "IPython", "markupsafe", "__main__",
        "jinja", "pymongo", "past", "zmq", "future",
        "cryptography", "OpenSSL"
    ]
    for line in lines:
        write = True
        for pattern in exclude_patterns:
            if pattern in line: 
                logging.debug('{} {}'.format(pattern, line))
                write = False
        if write:
            lines_f.append(line)
    return lines_f

def check(rules, schedule):
    end = True
    for k, vs in rules.items():
        for v in vs:
            if v not in schedule:
                end = False
    return end

def schedule_append(grules_r, schedule):
    l_sched = 0
    while not check(grules_r, schedule):
        l_sched = len(schedule)

        for k, vs in grules_r.items():
            satis = True
            for v in vs:
                if v not in schedule:
                    satis = False
            if satis: 
                if k not in schedule:
                    schedule.append(k)

        if l_sched == len(schedule):
            break
    return schedule

def schedule_append_reflective(grules_r, schedule):
    l_sched = 0
    while not check(grules_r, schedule):
        l_sched = len(schedule)

        for k, vs in grules_r.items():
            miss = []
            for v in vs:
                if v not in schedule:
                    miss.append(v)
            if len(miss) == 1:
                if miss[0] == k:
                    schedule.append(k)

        if l_sched == len(schedule):
            break
    return schedule

def set_default(obj):
    if isinstance(obj, set):
        return list(obj)
    raise TypeError

# @total_ordering
class WMCoreNode():
    '''
    high prio: required high, requires low
    low prio: required low, requires high

    __lt__: less-than === higher prio
    '''
    def __init__(self, name, grules_r, schedule):
        self.name = name
        self.required = self.required_by(grules_r, schedule, name)
        self.requires = self.requirements(grules_r, schedule, name)
        self.required_card = len(set(self.required))
        self.requires_card = len(set(self.requires))

    def required_by(self, grules_r, schedule, key):
        '''
        modules **that are not in schedule yet**
        depend on this (higher, higher prio)
        '''
        for k,vs in grules_r.items():
            if key in vs and k not in schedule:
                yield k

    def requirements(self, grules_r, schedule, key):
        '''
        list of modules **that are not in schedule yet**
        that need to be migrated before migrating this.
        len(set(requirements(...))): (higher, lower prio)
        '''
        for v in grules_r[key]:
            if v not in schedule:
                yield v
            self.requirements(grules_r, schedule, v)

    def __lt__ (self, other):
        if self.required_card > other.required_card: return True
        elif self.required_card < other.required_card: return False
        elif self.requires_card < other.requires_card: return True
        else: return False

    def __eq__(self, other):
        return (self.required_card == other.required_card) and (self.requires_card == other.requires_card)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    txt = open(args.input_file).read()

    lines = txt.split("\n")
    header = lines[:6]
    body = lines[6:-3]

    body = filter(body)

    rules = [line for line in body if '->' in line]
    rules_r = {} # reversed dependencies
    grules_r = {} # grouped reversed dependencies
    for rule in rules:
        arrow, fmt = rule.split("[")
        a, _, b = arrow.split()
        a, b = remove_src_python(a), remove_src_python(b)
        # fill reverse dep
        if a not in rules_r:
            rules_r[a] = set()
        if b in rules_r:
            rules_r[b].add(a)
        else:
            rules_r[b] = set()
            rules_r[b].add(a)
        # fill grouped reverse dep
        group_a = shorten(a)
        group_b = shorten(b)
        if group_a not in grules_r:
            grules_r[group_a] = set()
        if group_b in grules_r:
            grules_r[group_b].add(group_a)
        else:
            grules_r[group_b] = set()
            grules_r[group_b].add(group_a)

    # print the simplified diagram dictionary - json format
    with open(args.input_file[:-4] + "_group_l" + str(args.level) + ".txt", "w+") as f:
        pprint.pprint(grules_r, f)

    # convert set() into list before serializing
    # with open(args.input_file[:-4] + "_group_l" + str(args.level) + ".json", "w+") as f:
    #     json.dumps(grules_r, f, default=set_default)

    # print the simplified diagram dot file
    with open(args.input_file[:-4] + "_group_l" + str(args.level) + ".dot", "w+") as f:
        f.write('\n'.join(header))
        print('\n', file=f)
        # nodes
        nodes = set()
        for k, v in grules_r.items():
            nodes.add(k)
            for node in v:
                nodes.add(node)
        for node in nodes:
            print('    {} [label="{}"]'.format(node, node), file=f)
        # rules
        for k, v in grules_r.items():
            for node in v:
                print('    {} -> {}'.format(node, k), file=f)
        print('}', file=f)

    schedule = []
    logging.info(len(grules_r))
    # adding the directories with no dependencies
    for k, vs in grules_r.items():
        if len(vs) == 0:
            if k not in schedule:
                schedule.append(k)
    # adding the directories whose dependencies are satisfied
    schedule = schedule_append(grules_r, schedule)
    schedule = schedule_append_reflective(grules_r, schedule)

    pprint.pprint(schedule)

    node_list = []
    for k in grules_r:
        node = WMCoreNode(k, grules_r, schedule)
        node_list.append(node)
    node_list = sorted(node_list)
    for idx, node in enumerate(node_list):
        logging.info("%s %s %s", node.name, node.required_card, node.requires_card)

    # euristic_schedule = [
    #     "WMCore_Database",
    #     "WMCore_DAOFactory",
    #     "WMCore_Services",
    #     "WMCore_WorkerThreads",
    #     "WMCore_WMSpec",

    # ]

    # logging.info(len(schedule))

    # for k in euristic_schedule:
    #     for v in list(deps_list(grules_r, schedule, k)):
    #         if v not in schedule:
    #             schedule.append(v)

    # logging.info(len(schedule))

    # cyclic dependencies - now we try to brute-force the result
    # this can and should be improved
    left_nodes = (set(grules_r.keys())).difference(set(schedule))
    logging.info("left nodes: %s" % len(left_nodes))
    for n in range(10, len(left_nodes)):
        kcombs = itertools.combinations( left_nodes , n)
        logging.info("n %s" % n)
        # logging.info("%s", len(list(kcombs)))
        for kcomb in kcombs:
            schedule_try = set(schedule) | set(kcomb) # set.union
            satis = True
            for k in kcomb:
                for v in grules_r[k]:
                    if v not in schedule_try:
                        satis = False
            if satis: logging.info(kcomb)

## docker run -it python:3.8 python
# from math import comb
# def comblist(n):
#   for i in range(n):
#     print(i, comb(n,i))

# comblist(39)

# 0 1
# 1 39
# 2 741
# 3 9139
# 4 82251
# 5 575757
# 6 3262623
# 7 15380937
# 8 61523748
# 9 211915132
# 10 635745396
# 11 1676056044
# 12 3910797436
# 13 8122425444
# 14 15084504396
# 15 25140840660
# 16 37711260990
# 17 51021117810
# 18 62359143990
# 19 68923264410
# 20 68923264410
# 21 62359143990
# 22 51021117810
# 23 37711260990
# 24 25140840660
# 25 15084504396
# 26 8122425444
# 27 3910797436
# 28 1676056044
# 29 635745396
# 30 211915132
# 31 61523748
# 32 15380937
# 33 3262623
# 34 575757
# 35 82251
# 36 9139
# 37 741
# 38 39

# 10
# ('WMComponent_RetryManager', 'WMComponent_JobStatusLite', 'WMComponent_JobArchiver', 'WMQuality_TestInitCouchApp', 'WMComponent_PhEDExInjector', 'WMCore_WMInit', 'WMQuality_TestInit', 'WMCore_WMConnectionBase', 'WMCore_BossAir', 'WMCore_JobStateMachine')
