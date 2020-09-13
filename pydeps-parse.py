'''

python3 pydeps-parse.py \
    -i /path/wmcore.dot \
    -l 2

'''

import pprint
import logging
import itertools
import copy

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
    with open(args.input_file[:-4] + "_group_l" + str(args.level) + ".json", "w+") as f:
        pprint.pprint(grules_r, f)

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

    # cyclic dependencies - now we try to brute-force the result
    # this can and should be improved
    while not check(grules_r, schedule):
        for n in range(2, len(grules_r)):
            kcombs = itertools.combinations( (set(grules_r.keys())).difference(set(schedule)) , n)
            logging.info("n " + str(n))
            for kcomb in kcombs:
                schedule_try = copy.deepcopy(schedule)
                for k in kcomb:
                    if k not in schedule_try:
                        schedule_try.append(k)
                satis = True
                for k in kcomb:
                    for v in grules_r[k]:
                        if v not in schedule_try:
                            satis = False
                if satis: logging.info(kcomb)

    logging.info(len(schedule))


