'''

python3 pydeps-parse.py \
    -i /path/wmcore.dot \
    -l 2 \
    -d /path/to/dmwm/WMCore

improve: 
* se docker3.8 (to quicly have estimation of missing )
* parallelize

'''

import pprint
import logging
import itertools
import copy
import json
import os
import datetime
import subprocess

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
parser.add_argument("-n","--n", \
  help="backtrack: stop when hitting this cyclic group size. bruteforce: test only this groupsize", \
  type=int, \
  required=False, \
  default=10
  )
future_parser = parser.add_mutually_exclusive_group(required=False)
future_parser.add_argument('--future', dest='future', action='store_true')
future_parser.add_argument('--no-future', dest='future', action='store_false')
parser.set_defaults(feature=False)
parser.add_argument("-v","--v", \
  help="futurize: docker volume bind", \
  type=str, \
  required=False, \
  default="/path/to/github.com/WMCore:/src"
  )

args = parser.parse_args()

## create logger
logger = logging.getLogger('simple_example')
logger.setLevel(logging.DEBUG)
# create console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(levelname)s:%(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)
# create file handler 
logfilename = "log/log_l{0}_n{1}_{2}.txt".format(
    args.level, args.n, datetime.datetime.utcnow().strftime("%s") )
fh = logging.FileHandler(filename=logfilename, mode="w")
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

# logger_pandas = logging.getLogger('logger_pandas')
# logger_pandas.setLevel(logging.DEBUG)
# logfilename_pandas = "log/pandas_{2}_l{0}_n{1}.txt".format(
#     args.level, args.n, datetime.datetime.utcnow().strftime("%s") )
# fh_pandas = logging.FileHandler(filename=logfilename_pandas, mode="w")
# fh_pandas.setLevel(logging.INFO)
# formatter = logging.Formatter('%(message)s')
# fh_pandas.setFormatter(formatter)
# logger_pandas.addHandler(fh_pandas)

masks = ["Utils", "PSetTweaks"]
separator = "_"

def parse_pydeps_modulename(node):
    '''
    any manipulation to the node name should be done here, such as changing
    the separator form "_" to something else
    '''
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
        if node.startswith(mask + separator):
            logger.debug(node)
            return mask
    level = args.level
    levels = [i for i in range(len(node)) if node[i] == separator] 
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

def schedule_append(rules_rev_group, schedule, log=False):
    '''
    Loop over all the modules. 
    if there is a module with satisfied dependencies, add it to the schedule
    When the schedule is complete or stops to grow, exit
    '''
    while not check_complete_schedule(rules_rev_group, schedule):
        len_schedule = len(schedule)
        for k, vs in rules_rev_group.items():
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

def schedule_append_reflective(rules_rev_group, schedule):
    '''
    Loop over all the modules. 
    if there is a module with unsatisfied dependencies, but the only
    missing dependency is itself, then add it to the schedule
    '''
    while not check_complete_schedule(rules_rev_group, schedule):
        len_schedule = len(schedule)
        for k, vs in rules_rev_group.items():
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

def scheduleaddition_isvalid(schedule_addition, schedule_try, rules_rev_group):
    '''
    check only a new portion of the schedule: 
    faster, but requires trust in the schedule so far
    '''
    for k in schedule_addition:
        for v in rules_rev_group[k]:
            if v not in schedule_try:
                return False
    return True

def schedule_isvalid(schedule, rules_rev, cyclic_interval):
    '''
    check if the schedule is valid. used only at the end of the program.
    We assume that there is only one cycle in the middle.
    when in the cycle, we have to check if all the depencies are inside
    the cycle as well, we have to increase idx to the last value of the interval!
    '''
    for idx, node in enumerate(schedule):
        if cyclic_interval[0] <= idx <= cyclic_interval[1]:
            idx = cyclic_interval[1]
        for v in rules_rev[node]:
            if v not in schedule[:idx+1]: # +!1 for reflective dependency
                logger.info(idx)
                logger.info(v)
                return False
    return True

def cyclic_bruteforce(rules_rev_group, schedule, euristic_schedule):
    left_nodes = (set(rules_rev_group.keys())).difference(set(schedule))
    logger.info("  left nodes: %s" % len(left_nodes))
    n = args.n if args.n < len(left_nodes) else left_nodes
    kcombs = itertools.combinations( left_nodes , n)
    logger.info("  n %s" % n)
    i=0
    for kcomb in kcombs:
        if i % 10000 == 0:
            print("n: ", i, end="\r")
        kcomb_heuristic = set(kcomb) | set(euristic_schedule) # set.union
        schedule_try = schedule + list(kcomb_heuristic)
        if scheduleaddition_isvalid(kcomb_heuristic, schedule_try, rules_rev_group): 
            logger.info(kcomb_heuristic)
            schedule = schedule_try
            break
        i += 1
    return schedule

def cyclic_backtrack(rules_rev_group, schedule, euristic_schedule):
    left_nodes = (set(rules_rev_group.keys())).difference(set(schedule))
    schedule_addition = left_nodes | euristic_schedule
    minlen = len(schedule_addition)
    minlen, schedule_cycle = cyclic_backtrack_helper(rules_rev_group, schedule, schedule_addition, minlen)
    return schedule_cycle

def cyclic_backtrack_helper(rules_rev_group, schedule, schedule_addition, minlen):
    schedul_temp = schedule_addition.copy()
    result = set()
    for node in schedul_temp:
        schedule_addition.discard(node)
        schedule_try = set(schedule) | set(schedule_addition)
        valid = scheduleaddition_isvalid(schedule_addition, schedule_try, rules_rev_group)
        # logger.info(valid)
        if valid:
            if len(schedule_addition) < minlen: 
                minlen = len(schedule_addition)
                logger.debug("DAJJE %s" % minlen)
                logger.debug(schedule_addition)
            if len(schedule_addition) == args.n:
                result = schedule_addition.copy()
                return len(schedule_addition), result
            minlen, _result = cyclic_backtrack_helper(rules_rev_group, schedule, schedule_addition, minlen)
            if len(_result) > 0:
                return len(_result), _result
        schedule_addition.add(node)
    return minlen, result

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
    for k in rules_rev_group:
        node = WMCoreNode(k, rules_rev_group, schedule, args.directory)
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

    def init(self, name, rules_rev_group, schedule, wmcore_dir):
        self.name = name
        # graph
        self._required = self._required_by(rules_rev_group, schedule, name)
        self._requires = self._requirements(rules_rev_group, schedule, name)
        self.required_card = len(set(self._required))
        self.requires_card = len(set(self._requires))
        # stats
        self._wmcore_dir = os.path.join(wmcore_dir, "src", "python")
        self._module_dir = os.path.join(self._wmcore_dir, "/".join(name.split(separator)))
        self._get_files()
        self.len = self._len()
        self.lines = self._lines()

    def _required_by(self, rules_rev_group, schedule, key):
        '''
        modules **that are not in schedule yet**
        depend on this (higher, higher prio)
        '''
        for k,vs in rules_rev_group.items():
            if key in vs and k not in schedule:
                yield k

    def _requirements(self, rules_rev_group, schedule, key):
        '''
        list of modules **that are not in schedule yet**
        that need to be migrated before migrating this.
        len(set(_requirements(...))): (higher, lower prio)
        '''
        for v in rules_rev_group[key]:
            if v not in schedule:
                yield v
            self._requirements(rules_rev_group, schedule, v)

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

    def _futurize_changes(self):
        self._adds = 0
        self._dels = 0
        for file in self._files:
            dockerfile = file.replace(self._wmcore_dir, "/src/src/python")
            cmd=[
                "docker",
                "run",
                "-it",
                "-v", 
                args.v,
                "python-docker_python-user-future",
                "futurize",
                dockerfile
            ]
            r = subprocess.run(cmd, capture_output=True)
            logger.debug(r.args)
            stdout = r.stdout.decode("utf-8")
            stdout = stdout.split("\n")
            self._adds += len([line for line in stdout if line.startswith("+") and not line.startswith("+++")])
            self._dels += len([line for line in stdout if line.startswith("-") and not line.startswith("---")])
        logger.debug(self._adds, self._dels)

def revdependency_dict(rules, nodes):
    '''
    Build 
    * dictionary with the reversed dependencies, i.e. key depends on val (`key.py` has line `import val`)
    * dictionary with the reversed dependencies, grouped at the desired level

    '''
    rules_rev = {} # reversed dependencies
    rules_rev_group = {} # grouped reversed dependencies
    for rule in rules:
        arrow, _ = rule.split("[")
        a, _, b = arrow.split()
        a, b = parse_pydeps_modulename(a), parse_pydeps_modulename(b)
        # Exclude directories in root to avoid double counting
        if (a not in masks) and (b not in masks) and args.level > 1: 
            if (separator not in a) or (separator not in b) : 
                continue 
        # fill reverse dep
        if a not in rules_rev:
            rules_rev[a] = set()
        if b in rules_rev:
            rules_rev[b].add(a)
        else:
            rules_rev[b] = set()
            rules_rev[b].add(a)
        # fill grouped reverse dep
        group_a = shorten(a)
        group_b = shorten(b)
        if group_a not in rules_rev_group:
            rules_rev_group[group_a] = set()
        if group_b in rules_rev_group:
            rules_rev_group[group_b].add(group_a)
        else:
            rules_rev_group[group_b] = set()
            rules_rev_group[group_b].add(group_a)
    for node in nodes:
        nodename = node.split("[")[0]
        nodename.strip()
        nodename = parse_pydeps_modulename(nodename)
        group_nodename = shorten(nodename)
        if group_nodename not in rules_rev:
            rules_rev [group_nodename] = set()
        if group_nodename not in rules_rev_group:
            rules_rev_group [group_nodename] = set()
            logger.debug(group_nodename)
    return rules_rev, rules_rev_group

def revdepgraph_write_json(revdep_dict, filename):
    '''
    write to file the reversed dependency dictionary in json format.
    
    json.dumps(revdep_dict, f) # fais with set() is not JSON serializable

    Need to filter out empty set before serializing to json in the proper way.
    Try: default=set_default
    '''
    with open(filename, "w+") as f:
        # json.dumps(revdep_dict, f, default=set_default)
        pprint.pprint(revdep_dict, f)

def revdepgraph_write_dot(revdep_dict, filename, header):
    '''
    Write simplified reversed dependency graph to dot file
    '''
    with open(filename, "w+") as f:
        f.write('\n'.join(header))
        print('\n', file=f)
        # nodes
        nodes = set()
        for k, v in revdep_dict.items():
            nodes.add(k)
            for node in v:
                nodes.add(node)
        for node in nodes:
            print('    {} [label="{}"]'.format(node, node), file=f)
        # rules
        for k, v in revdep_dict.items():
            for node in v:
                print('    {} -> {}'.format(node, k), file=f)
        print('}', file=f)

def depgraph_write_json(revdep_dict, filename):
    dep_dict = {}
    for k1 in revdep_dict:
        for k2, v in revdep_dict.items():
            if k1 in v:
                if k1 in dep_dict:
                    dep_dict[k1].append(k2)
                else:
                    dep_dict[k1] = [k2]
    with open(filename, "w+") as f:
        pprint.pprint(dep_dict, f)

def main():
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
    rules_rev, rules_rev_group = revdependency_dict(rules, nodes)
    logger.debug(rules_rev)
    logger.debug(rules_rev_group)
    logger.info("meta: nodes %s" % len(rules_rev_group))
    logger.info("meta: rules %s" % len([rule for rules in rules_rev_group.values() for rule in rules]))
    revdepgraph_write_json(
        rules_rev_group,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".txt", 
        )
    revdepgraph_write_dot(
        rules_rev_group,
        args.input_dotfile[:-4] + "_group_l" + str(args.level) + ".dot",
        header
        )
    depgraph_write_json(
        rules_rev_group,
        args.input_dotfile[:-4] + "_direct_group_l" + str(args.level) + ".txt", 
        )

    ################################
    # Compute a possible schedule for gradual migration
    # based on the simplified graph

    schedule = []
    # adding the directories with no dependencies
    for k, vs in rules_rev_group.items():
        if len(vs) == 0:
            if k not in schedule:
                schedule.append(k)
    # adding the directories whose dependencies are easily satisfied
    schedule = schedule_append(rules_rev_group, schedule)
    schedule = schedule_append_reflective(rules_rev_group, schedule)

    # pprint.pprint(schedule)
    idx_endgradual = len(schedule)
    logger.info("len schedule (gradual): %s" % len(schedule))

    # Manually select a few modules that in the dependency diagram 
    # present a lot of outgoing arrows, which means that they are required by
    # many other modules.
    # these are intended to be migrated all at once at the same time!
    euristic_schedule = set()
    if args.level == 2:
        euristic_schedule = set((
            "WMCore_Database",
            "WMCore_Services",
            "WMCore_WorkerThreads",
            "WMCore_WMSpec",
            ))
        ## Do not add the euristics brutally, 
        ## use them to add cyclic dependencies!
        # for k in euristic_schedule:
        #     if k not in schedule:
        #         schedule.append(k) # 33

    ##cyclic dependencies - now we try to brute-force the result
    ## FIXME this can and should be improved
    ## Example: l==2, 68 nodes, schedule long 54. 
    ## all combinations of 30 in group of 54: 1402659561581460 \simeq 1e15
    ## able to test 1e4 combinations per second -> 1e9 seconds -> 30y
    ## avoid at all costs!
    # schedule = cyclic_bruteforce(rules_rev_group, schedule, euristic_schedule)

    # cyclic dependencies: backtracking
    schedule += cyclic_backtrack(rules_rev_group, schedule, euristic_schedule)

    idx_restartgradual = len(schedule)
    logger.info("len schedule (backtrack): %s" % len(schedule))

    schedule = schedule_append(rules_rev_group, schedule)
    schedule = schedule_append_reflective(rules_rev_group, schedule)
    schedule = schedule_append(rules_rev_group, schedule)
    schedule = schedule_append_reflective(rules_rev_group, schedule)
    schedule = schedule_append(rules_rev_group, schedule)

    logger.info("len schedule (gradual2): %s" % len(schedule))
    logger.debug(schedule)
    missing = set(rules_rev_group.keys()).difference(set(schedule))
    logger.debug(" missing %s" % missing)

    logger.info("VALID? %s" % 
        schedule_isvalid(schedule, rules_rev_group, 
        (idx_endgradual, idx_restartgradual))
     )

    # # FIXME - JUST TO HAVE NICE PLOTS IN THE PRESENTATION!
    # for name in missing:
    #     schedule.append(name)

    ################################
    # After having a schedule, gather some informations about the modules
    if args.directory:
        node_dict = {}
        for k in rules_rev_group:
            node = WMCoreNode()
            node.init(k, rules_rev_group, schedule, args.directory)
            node_dict[node.name] = node

        total_number_files = sum([ len(node) for node in node_dict.values() ])
        total_number_loc = sum([ node.lines for node in node_dict.values() ])
        current_loc = 0
        for idx, name in enumerate(schedule):
            if len(node_dict[name]) > 0:
                current_loc += node_dict[name].lines
                logger.debug("| {0: <34} | {1: >4} | {2: >6} | {3: >1.3f} | {4:} | {5} | {6} |".format(
                    name, 
                    len(node_dict[name]), 
                    node_dict[name].lines,
                    current_loc / total_number_loc,
                    0 if idx_endgradual < idx < idx_restartgradual else 1,
                    rules_rev_group[name],
                    node_dict[name]._files
                    ))
                if args.future:
                    if not os.path.exists(args.v.split(":")[0]):
                        logger.warning("docker bind: non existing path!")
                        return
                    node_dict[name]._futurize_changes(),
                    logger.info("| {0} | {1} | {2} | {3}, {4} | {5} | {6} | {7} | {8} | ".format(
                        name, 
                        len(node_dict[name]), 
                        node_dict[name].lines,
                        node_dict[name]._adds, node_dict[name]._dels,
                        current_loc / total_number_loc,
                        0 if idx_endgradual < idx < idx_restartgradual else 1,
                        rules_rev_group[name],
                        node_dict[name]._files
                        ))
                # logger_pandas.warning("{0},{1},{2},{3},{4}".format(
                #     name, 
                #     len(node_dict[name]), 
                #     node_dict[name].lines,
                #     current_loc / total_number_loc,
                #     0 if idx_endgradual < idx < idx_restartgradual else 1
                #     ))

        logger.info("Total .py files: %s" % total_number_files )
        ## compare total_number_loc with the following
        ## cd dmwm/WMCore/src/python
        ## find . | grep -v ".pyc" | grep ".py" | grep -v "__init__.py" | xargs -n 1 cat | wc -l
        logger.info("Total LOC in .py files: %s" % total_number_loc )

if __name__ == "__main__":
    main()