'''

python3 pydeps-parse.py \
    -i /path/wmcore.dot \
    -l 2 \
    -d /path/to/dmwm/WMCore

'''

import pprint
import logging
import itertools
import copy
import json
import os

from functools import total_ordering

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("-i","--input-dotfile", \
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
parser.add_argument("-d", "--directory", \
    help="dmwm/WMCore directory, relative or absolute path",
    type=str,
    required=True)
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
    '''
    exclude some directories that are not relevant for WMCore
    '''
    lines_filtered = []
    exclude_patterns = [
        "bson", "IPython", "markupsafe", "__main__",
        "jinja", "pymongo", "past", "zmq", "future",
        "cryptography", "OpenSSL"
    ]
    for line in lines:
        keep = True
        for pattern in exclude_patterns:
            if pattern in line: 
                logging.debug('{} {}'.format(pattern, line))
                keep = False
        if keep:
            lines_filtered.append(line)
    return lines_filtered

def check_complete_schedule(rules, schedule):
    end = True
    for k, vs in rules.items():
        if k not in schedule: 
            end = False
        for v in vs:
            if v not in schedule:
                end = False
    return end

def schedule_append(grules_r, schedule, log=False):
    '''
    Loop over all the modules. 
    if there is a module with satisfied dependencies, add it to the schedule
    When the schedule is complete or stops to grow, exit
    '''
    while not check_complete_schedule(grules_r, schedule):
        len_schedule = len(schedule)
        for k, vs in grules_r.items():
            satis = True
            for v in vs:
                if v not in schedule:
                    satis = False
            if satis: 
                if k not in schedule:
                    schedule.append(k)
        if len_schedule == len(schedule):
            break
    return schedule

def schedule_append_reflective(grules_r, schedule):
    '''
    Loop over all the modules. 
    if there is a module with unsatisfied dependencies, but the only
    missing dependency is itself, then add it to the schedule
    '''
    while not check_complete_schedule(grules_r, schedule):
        len_schedule = len(schedule)
        for k, vs in grules_r.items():
            miss = []
            for v in vs:
                if v not in schedule:
                    miss.append(v)
            if len(miss) == 1:
                if miss[0] == k:
                    schedule.append(k)
        if len_schedule == len(schedule):
            break
    return schedule

# def set_default(obj):
#     if isinstance(obj, set):
#         return list(obj)
#     raise TypeError

@total_ordering
class WMCoreNode():
    '''
    high prio: required high, requires low
    low prio: required low, requires high

    __lt__: less-than === higher prio
    '''
    def __init__(self, name, grules_r, schedule, wmcore_dir):
        self.name = name
        # graph
        self._required = self.required_by(grules_r, schedule, name)
        self._requires = self.requirements(grules_r, schedule, name)
        self.required_card = len(set(self._required))
        self.requires_card = len(set(self._requires))
        # stats
        self._wmcore_dir = os.path.join(wmcore_dir, "src", "python")
        self._module_dir = os.path.join(self._wmcore_dir, "/".join(name.split("_")))
        self.len = self._len()
        self.lines = self._lines()

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

    def _len(self):
        if os.path.exists(self._module_dir + ".py"):
            return 1
        else:
            mylist = [name for root, _, names in os.walk(self._module_dir) for name in names if (os.path.isfile(os.path.join(root, name)) and name.endswith(".py") and name != "__init__.py") ]
            # print(mylist)
            return len(mylist)

    def __len__(self): return self.len

    def _lines(self):
        filelist = []
        fileloc = []
        if os.path.exists(self._module_dir + ".py"):
            filelist.append(self._module_dir + ".py")
            fileloc.append( sum(1 for line in open(self._module_dir + ".py")) )
        else:
            filelist = [os.path.join(root,name) for root, _, names in os.walk(self._module_dir) for name in names if (os.path.isfile(os.path.join(root, name)) and name.endswith(".py") and name != "__init__.py") ]
            fileloc = [sum(1 for line in open( file )) for file in filelist]
        return sum(fileloc)

def dependency_dict(rules):
    '''
    Build 
    * dictionary with the reversed dependencies, i.e. key depends on val
    * dictionary with the reversed dependencies, grouped at the desired level

    '''
    rules_r = {} # reversed dependencies
    grules_r = {} # grouped reversed dependencies
    for rule in rules:
        arrow, _ = rule.split("[")
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
    return rules_r, grules_r

def depgraph_write_json(revdep_dict, filename):
    '''
    write to file the reversed dependency dictionary in json format.
    
    json.dumps(revdep_dict, f) # fais with set() is not JSON serializable

    Need to filter out empty set before serializing to json in the proper way.
    Try: default=set_default
    '''
    with open(filename, "w+") as f:
        # json.dumps(revdep_dict, f, default=set_default)
        pprint.pprint(revdep_dict, f)

def depgraph_write_dot(revdep_dict, filename):
    '''
    Write simplified reversed dependency graph to dot file
    '''
    with open(filename, "w+") as f:
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    txt = open(args.input_dotfile).read()
    lines = txt.split("\n")
    header = lines[:6]
    body = lines[6:-3]
    body = filter(body)

    ################################
    # Get simplified dependency graph
    rules = [line for line in body if '->' in line]
    rules_r, grules_r = dependency_dict(rules)
    depgraph_write_json(
        grules_r,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".txt", 
        )
    depgraph_write_dot(
        grules_r,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".dot"
    )

    ################################
    # Compute a possible schedule for gradual migration
    # based on the simplified graph

    logging.info("Number of directories/modules: %s" % len(grules_r))

    schedule = []
    # adding the directories with no dependencies
    for k, vs in grules_r.items():
        if len(vs) == 0:
            if k not in schedule:
                schedule.append(k)
    # adding the directories whose dependencies are easily satisfied
    schedule = schedule_append(grules_r, schedule)
    schedule = schedule_append_reflective(grules_r, schedule)

    # pprint.pprint(schedule)
    logging.info("len schedule: %s" % len(schedule))

    # Manually select a few modules that in the dependency diagram 
    # present a lot of outgoing arrows, which means that they are required by
    # many other modules.
    # these are intended to be migrated all at once at the same time!

    euristic_schedule = [
        "WMCore_Database",
        "WMCore_Services",
        "WMCore_WorkerThreads",
        "WMCore_WMSpec",
    ]
    for k in euristic_schedule:
        if k not in schedule:
            schedule.append(k) # 33
    schedule = schedule_append(grules_r, schedule) # len(schedule) 38
    schedule = schedule_append_reflective(grules_r, schedule) # len(schedule) 63
    schedule = schedule_append(grules_r, schedule) # len(schedule) 69
    schedule = schedule_append_reflective(grules_r, schedule) # len(schedule) 82
    schedule = schedule_append(grules_r, schedule) # len(schedule) 83

    logging.info("len schedule: %s" % len(schedule))
    logging.info(schedule)

    # # cyclic dependencies - brute forcing
    # # no longer necessary after the euristic schedule 
    # left_nodes = (set(grules_r.keys())).difference(set(schedule))
    # logging.info("left nodes: %s" % len(left_nodes))
    # for n in range(10, len(left_nodes)):
    #     kcombs = itertools.combinations( left_nodes , n)
    #     logging.info("n %s" % n)
    #     # logging.info("%s", len(list(kcombs)))
    #     for kcomb in kcombs:
    #         schedule_try = set(schedule) | set(kcomb) # set.union
    #         satis = True
    #         for k in kcomb:
    #             for v in grules_r[k]:
    #                 if v not in schedule_try:
    #                     satis = False
    #         if satis: logging.info(kcomb)

    ################################
    # After having a schedule, gather some informations about the modules
    node_list = []
    for k in grules_r:
        if args.level > 1: 
            if "_" not in k: 
                continue # Exclude directories in root to avoid double counting
        node = WMCoreNode(k, grules_r, schedule, args.directory)
        node_list.append(node)
    node_list = sorted(node_list)
    for idx, node in enumerate(node_list):
        logging.debug("%s %s %s - %s %s", node.name, node.required_card, node.requires_card, len(node), node.lines)
        ## this print is needed to check if the sum is correct
        ## python3 pydeps-parse.py \
        ##     -i /home/dario/docs/dmwm/docs/wmcore/deps-tree/wmcore_gznzsw1eblR.dot \
        ##     -l 2 \
        ##     -d /home/dario/docs/dmwm/github.com/WMCore | grep "_" | awk -F" " '{sum+=$5} END {print sum}'
        ## INFO:root:83
        ## 138235
        ## print(node.name, node.required_card, node.requires_card, len(node), node.lines)

    print("Total .py files:", sum([ len(node) for node in node_list ]) )

    ## cd dmwm/WMCore/src/python
    ## find . | grep -v ".pyc" | grep ".py" | grep -v "__init__.py" | xargs -n 1 cat | wc -l
    print("Total LOC in .py files", sum([ node.lines for node in node_list ]) )
