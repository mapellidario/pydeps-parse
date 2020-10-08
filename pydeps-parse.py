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
    required=False)
args = parser.parse_args()

masks = ["Utils", "PSetTweaks"]
separator = "_"

def parse_pydeps_modulename(node):
    node = node.strip() # remove trailing spaces
    if node.startswith("src_python_"):
        node = node[len("src_python_"):] # remove "src_python"
    return node

def shorten(node):
    '''
    keep only up to the level-nth level.
    
    An exception to this rule are the nodes whose name start with an element
    of mask, i.e. the files in the directories specified by masks. 
    In this case the nodes are grouped at the level specified by the mask
    no matter what `args.level` is
    '''
    for mask in masks:
        if node.startswith(mask + "_"):
            logger.debug(node)
            return mask
    level = args.level
    levels = [i for i in range(len(node)) if node[i] == "_"] 
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
        "cryptography", "OpenSSL", "ipykernel_embed"
    ]
    for line in lines:
        keep = True
        for pattern in exclude_patterns:
            if pattern in line: 
                logger.debug('{} {}'.format(pattern, line))
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
    This class has a concept of ordering that would allow a list of nodes/modules
    to be sorted from high priorityof migration to low priority.
    The priority is estimated from two scores
    1. how many other modules import the current module: `required_card`.
    2. how many other modules need to be migrated before migrating this
      module : `required_card`

    A module with 
    * high prio: high `required_card`, low `requires_card`
    * low prio: low `required_card`, high `requires_card`

    The function __lt__: less-than means higher priority

    Example on how to sort such modules
    ```python
    node_list = []
    for k in grules_r:
        node = WMCoreNode(k, grules_r, schedule, args.directory)
        node_list.append(node)
    node_list = sorted(node_list)

    This class has a concept of length, which is the number of files .py in 
    the directory `self.name`.
    ```
    '''
    def __init__(self):
        self.name = ""
        self.len = 0
        self.lines = 0

    def init(self, name, grules_r, schedule, wmcore_dir):
        self.name = name
        # graph
        self._required = self._required_by(grules_r, schedule, name)
        self._requires = self._requirements(grules_r, schedule, name)
        self.required_card = len(set(self._required))
        self.requires_card = len(set(self._requires))
        # stats
        self._wmcore_dir = os.path.join(wmcore_dir, "src", "python")
        self._module_dir = os.path.join(self._wmcore_dir, "/".join(name.split(separator)))
        self._get_files()
        self.len = self._len()
        self.lines = self._lines()

    def _required_by(self, grules_r, schedule, key):
        '''
        modules **that are not in schedule yet**
        depend on this (higher, higher prio)
        '''
        for k,vs in grules_r.items():
            if key in vs and k not in schedule:
                yield k

    def _requirements(self, grules_r, schedule, key):
        '''
        list of modules **that are not in schedule yet**
        that need to be migrated before migrating this.
        len(set(_requirements(...))): (higher, lower prio)
        '''
        for v in grules_r[key]:
            if v not in schedule:
                yield v
            self._requirements(grules_r, schedule, v)

    def __lt__ (self, other):
        if self.required_card > other.required_card: return True
        elif self.required_card < other.required_card: return False
        elif self.requires_card < other.requires_card: return True
        else: return False

    def __eq__(self, other):
        return (self.required_card == other.required_card) and (self.requires_card == other.requires_card)

    def _get_files(self):
        self._files = []
        if os.path.exists(self._module_dir + ".py"):
            self._files = [self._module_dir + ".py"]
        elif (self.name in masks) or (self.name.count(separator) == args.level - 1):
            self._files = [os.path.join(root, name) 
                            for root, _, names in os.walk(self._module_dir) 
                            for name in names 
                            if os.path.isfile(os.path.join(root, name)) 
                            and name.endswith(".py")
                            and name != "__init__.py" ] # 
        elif self.name.count(separator) < args.level - 1 :
            self._files = [os.path.join(root, name) 
                           for root, _, names in os.walk(self._module_dir) 
                           for name in names 
                           if os.path.isfile(os.path.join(root, name)) 
                           and name.endswith(".py") 
                           and name != "__init__.py" 
                           and name.count(separator) >= args.level ] # 
        for file in self._files:
            if self.name == "WMCore_REST":
                logger.debug("{0} {1}".format(self.name, file))


    def _len(self):
        '''
        number of .py files in the directory of the module
        '''
        return len(self._files)

    def __len__(self): return self.len

    def _lines(self):
        '''
        Total number of lines of code in all the files in the directory of the
        module `self.name`
        '''
        return sum( [sum(1 for line in open( file )) for file in self._files] )

    def __add__(self, other):
        temp = WMCoreNode()
        temp.len = 0
        temp.name = self.name
        if self.len == 0: temp.len += 1
        else: temp.len += self.len
        if other.len == 0: temp.len += 1
        else: temp.len += other.len
        temp.lines = self.lines + other.lines
        return temp

    def __repr__(self):
        return "{0} {1} {2}".format( self.name, self.len, self.lines)

def dependency_dict(rules, nodes):
    '''
    Build 
    * dictionary with the reversed dependencies, i.e. key depends on val (`key.py` has line `import val`)
    * dictionary with the reversed dependencies, grouped at the desired level

    '''
    rules_r = {} # reversed dependencies
    grules_r = {} # grouped reversed dependencies
    for rule in rules:
        arrow, _ = rule.split("[")
        a, _, b = arrow.split()
        a, b = parse_pydeps_modulename(a), parse_pydeps_modulename(b)
        # Exclude directories in root to avoid double counting
        if (a not in masks) and (b not in masks) and args.level > 1: 
            if (separator not in a) or (separator not in b) : 
                continue 
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
    for node in nodes:
        nodename = node.split("[")[0]
        nodename.strip()
        nodename = parse_pydeps_modulename(nodename)
        group_nodename = shorten(nodename)
        if group_nodename not in rules_r:
            rules_r [group_nodename] = set()
        if group_nodename not in grules_r:
            grules_r [group_nodename] = set()
            logger.debug(group_nodename)
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

def depgraph_write_dot(revdep_dict, filename, header):
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
    # logging.basicConfig(level=logging.INFO)
    # logging.basicConfig(level=logging.DEBUG)

    # create logger
    logger = logging.getLogger('simple_example')
    logger.setLevel(logging.DEBUG)
    # create file handler 
    fh = logging.FileHandler(filename="log/log.txt", mode="w")
    fh.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(message)s')
    fh.setFormatter(formatter)
    # create console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(levelname)s:%(message)s')
    ch.setFormatter(formatter)
    # add fh to logger
    logger.addHandler(ch)
    logger.addHandler(fh)

    txt = open(args.input_dotfile).read()
    lines = txt.split("\n")
    header = lines[:6]
    body = lines[6:-1]
    body = filter(body)

    ################################
    # Get simplified dependency graph
    nodes = [line for line in body if 'label' in line]
    rules = [line for line in body if '->' in line]
    logger.debug(rules)
    rules_r, grules_r = dependency_dict(rules, nodes)
    logger.debug(rules_r)
    logger.debug(grules_r)
    depgraph_write_json(
        grules_r,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".txt", 
        )
    depgraph_write_dot(
        grules_r,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".dot",
        header
    )

    ################################
    # Compute a possible schedule for gradual migration
    # based on the simplified graph

    logger.info("Number of directories/modules: %s" % len(grules_r))

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
    logger.info("len schedule: %s" % len(schedule))

    # Manually select a few modules that in the dependency diagram 
    # present a lot of outgoing arrows, which means that they are required by
    # many other modules.
    # these are intended to be migrated all at once at the same time!

    if args.level == 2:
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

    logger.info("len schedule: %s" % len(schedule))
    logger.debug(schedule)
    logger.debug(set(grules_r.keys()).difference(set(schedule)))

    ################################
    # After having a schedule, gather some informations about the modules
    if args.directory:
        node_dict = {}
        for k in grules_r:
            node = WMCoreNode()
            node.init(k, grules_r, schedule, args.directory)
            node_dict[node.name] = node

        total_number_files = sum([ len(node) for node in node_dict.values() ])
        total_number_loc = sum([ node.lines for node in node_dict.values() ])
        current_loc = 0
        for idx, name in enumerate(schedule):
            current_loc += node_dict[name].lines
            logger.warning("| {0: <34} | {1: >4} | {2: >6} | {3: >1.3f} |".format(
                name, 
                len(node_dict[name]), 
                node_dict[name].lines,
                current_loc / total_number_loc
                ))
    
        logger.info("Total .py files: %s" % total_number_files )
        ## compare total_number_loc with the following
        ## cd dmwm/WMCore/src/python
        ## find . | grep -v ".pyc" | grep ".py" | grep -v "__init__.py" | xargs -n 1 cat | wc -l
        logger.info("Total LOC in .py files: %s" % total_number_loc )

        # test_sum = node_dict["Utils_MemoryCache"] + node_dict["Utils_Utilities"]
        # logger.info("| {0: <34} | {1: >3} | {2: >5} |".format(
        #         test_sum.name, 
        #         len(test_sum), 
        #         test_sum.lines,
        #         ))
        # masks = ["Utils"]
        # for mask in masks:
        #     temp_mask = WMCoreNode()
        #     temp_mask.name = mask
        #     logger.debug(temp_mask)
        #     pop_cache = []
        #     for name, node in node_dict.items():
        #         if name.startswith(mask + "_"):
        #             temp_mask += node
        #             logger.debug(name)
        #             logger.debug(temp_mask)
        #             pop_cache.append(name)
        #     for name in pop_cache:
        #         node_dict.pop(name)
        #     node_dict[mask] = temp_mask
        #     logger.debug(temp_mask)
        # for node in node_dict.values():
        #     logger.info("| {0: <34} | {1: >3} | {2: >5} |".format(
        #         node.name, 
        #         len(node), 
        #         node.lines,
        #         ))