
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import abc
import collections
import copy


from ansible.errors import AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.config_normalizing.base import ConfigNormalizerBaseMerger, NormalizerBase, NormalizerNamed, DefaultSetterConstant, DefaultSetterOtherKey
from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  get_subdict, \
  merge_dicts, \
  setdefault_none, \
  SUBDICT_METAKEY_ANY

from ansible_collections.smabot.git.plugins.module_utils.plugins.config_normalizing.gitserver_norm_base import \
  GitServerBaseNormer


from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


## sorted from least to max
GITLAB_ROLE_LEVELS = [
   'guest', 'reporter', 'developer', 'maintainer', 'owner'
]

GITLAB_ROLE_LEVEL_MIN = GITLAB_ROLE_LEVELS[0]
GITLAB_ROLE_LEVEL_MAX = GITLAB_ROLE_LEVELS[-1]


class ConfigRootNormalizer(GitServerBaseNormer):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          AllGroupsNormer(pluginref),
          AllProjectsNormer(pluginref),
        ]

        super(ConfigRootNormalizer, self).__init__(pluginref, *args, **kwargs)


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        ## preset connection server type, as this module is specific
        ## for gitlab, server type is obviously gitlab
        setdefault_none(my_subcfg, 'connection', {}).update(
           {'type': 'gitlab'}
        )

        ## pre normalize group tree structure
        grps = setdefault_none(my_subcfg, 'groups', {}).get('groups', {})
        normed_grps = {}

        for k in list(grps.keys()):
            v = grps[k]

            if v is None:
                v = {}

            srcpath = v.get('fullpath', None) or v.get('path', None) or k
            ksplit = srcpath.split('/')

            if len(ksplit) == 1:
                ##
                ## toplvl grp has no implicit parents defined, grp
                ## subtree is already pre normed as much as we need
                ## it here, simply add it directly to result
                ##
                merge_dicts(normed_grps, {k: v})
                continue

            ##
            ## toplvl given group def has implicit parents defined,
            ## convert it to standard explicit subgrp structure
            ##
            cur_parent = normed_grps

            for x in ksplit[:-1]:
                pv = setdefault_none(cur_parent, x, {})

                orig_grpgen = v.get('grpgen', None) or {}
                setdefault_none(pv, 'implicit_basegroups', []).append(
                   { 'srcgrp': v,
                     'grpgen_method': orig_grpgen.get('base_method', None),
                   }
                )

                v['explicit_srcgroup'] = True
                cur_parent = setdefault_none(pv, 'subgroups', {})

            ## dont forget to readd original toplvl given
            ## group def to auto generated parent tree as child
            cur_parent[ksplit[-1]] = v

        if normed_grps:
            my_subcfg['groups']['groups'] = normed_grps

        return my_subcfg



class AllGroupAbleBaseNormer(NormalizerBase):

    @property
    @abc.abstractmethod
    def object_type_key(self):
        pass

    @property
    def object_type_recurse_down_key(self):
        return None


    def _fill_export_list_rec(self, grpmap, reslist=None, filterfn=None):
        if reslist is None:
            reslist = []

        for k, v in grpmap.items():
            if not filterfn or filterfn(v):
                reslist.append(v)

            if not self.object_type_recurse_down_key:
                continue

            self._fill_export_list_rec(v[self.object_type_recurse_down_key],
              reslist=reslist, filterfn=filterfn
            )

        return reslist


    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        ## make properly ordered flat list of groups suitable
        ## for loop based handling
        explst = []

        my_subcfg['_export_lists'] = {
          'basic': self._fill_export_list_rec(
             my_subcfg[self.object_type_key],
             filterfn=lambda x: x['basic_management']['enable']
          ),
          'members': self._fill_export_list_rec(
             my_subcfg[self.object_type_key],
             filterfn=lambda x: x['members']['enable']
          ),
        }

        return my_subcfg



class AllGroupsNormer(AllGroupAbleBaseNormer):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          TopLevelGroupInstNormer(pluginref),
        ]

        super(AllGroupsNormer, self).__init__(pluginref, *args, **kwargs)


    @property
    def config_path(self):
        return ['groups']

    @property
    def object_type_key(self):
        return 'groups'

    @property
    def object_type_recurse_down_key(self):
        return 'subgroups'



class AllProjectsNormer(AllGroupAbleBaseNormer):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          ProjectInstNormerBase(pluginref),
        ]

        super(AllProjectsNormer, self).__init__(pluginref, *args, **kwargs)


    @property
    def config_path(self):
        return ['projects']

    @property
    def object_type_key(self):
        return 'projects'



##
## group-able's are the common base thingy from groups and repos(projects)
##
## !!IMPORTANT!!: so that grpgen works properly and correctly for multilevel
##   group trees, default setters are not allowed for
##   GroupAbleInstNormerBase normers, default everything inside
##   _handle_specifics_presub method instead
##
class GroupAbleInstNormerBase(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms.insert(0,
          AllMembersNormer(pluginref),
        )

        subnorms.insert(0,
          GrpInstBaseManagementNormer(pluginref),
        )

        super(GroupAbleInstNormerBase, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def name_key(self):
        return 'path'

    @property
    def toplevel(self):
        return True

    @property
    def object_type(self):
        return 'group'


    def _handle_grpgen_method_defaulting(self,
        cfg, my_subcfg, cfgpath_abs, basegrp
    ):
        ##
        ## grpgen method defaulting means that
        ## implicit groups should be generated by
        ## applying standard norming defaults
        ## which we do anyway
        ##
        pass

    def _handle_grpgen_method_inheriting(self,
        cfg, my_subcfg, cfgpath_abs, basegrp
    ):
        ##
        ## for inheriting gen method some/all settings
        ## of generating explicit source groups are also
        ## auto-applied to implicit groups
        ##

        ##
        ## note: some keys simply make no sense to
        ##   inherit from base groups
        ##
        ## TODO: make this list modifyable by caller cfg??
        ##
        exclude_keys = [
           'grpgen', 'implicit_basegroups', 'members',
           'name', 'object_type', 'parent', 'parent_chain',
           'path', 'subgroups', 'toplvl', 'fullpath',
           'explicit_srcgroup',
        ]

        basemerge = {}

        for k, v in basegrp['srcgrp'].items():
            if k in exclude_keys:
                continue

            if isinstance(v, (collections.abc.Mapping, list)):
                v = copy.deepcopy(v)

            basemerge[k] = v

        merge_dicts(my_subcfg, basemerge)


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        setdefault_none(my_subcfg, 'explicit_srcgroup', False)

        path = my_subcfg['path'].split('/')
        path = path[-1]

        setdefault_none(my_subcfg, 'name', path)

        my_subcfg['toplvl'] = self.toplevel
        my_subcfg['object_type'] = self.object_type

        parent = None
        parent_chain = None

        basegrps = my_subcfg.get('implicit_basegroups', [])
        grpgen_by_parents = False

        if not basegrps and not self.toplevel \
           and not my_subcfg['explicit_srcgroup']:
               ## make my parents my basegrps
                grpgen_by_parents = True

        if self.toplevel:
            fullpath = path
        else:
            ## determine fullpath for subgroup
            fullpath = [path]

            i = 1

            while True:
                pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=i * 2)
                fullpath.append(pcfg['path'])

                if grpgen_by_parents:
                    tmp = pcfg.get('grpgen', None)

                    if tmp:
                        basegrps.append({ 'srcgrp': pcfg,
                           'grpgen_method': tmp.get('base_method', None),
                        })

                if pcfg['toplvl']:
                    break

                i += 1

            fullpath = list(reversed(fullpath))
            parent_chain = fullpath[:-1]
            parent = '/'.join(parent_chain)

            fullpath = '/'.join(fullpath)

            if grpgen_by_parents:
                ##
                ## note: the closer I get to the root toplvl group of a
                ##   group tree, the lower should it grpgen prio be,
                ##   so we need to reverse the list here
                ##
                basegrps = reversed(basegrps)

        my_subcfg['fullpath'] = fullpath
        my_subcfg['parent'] = parent
        my_subcfg['parent_chain'] = parent_chain

        ## current group was implicitly generated or a pr, time to apply grpgen
        for x in basegrps:
            if not x['grpgen_method']:
                x['grpgen_method'] = 'defaulting'

            tmp = getattr(self,
               '_handle_grpgen_method_' + x['grpgen_method'], None
            )

            ansible_assert(tmp,
               "unsupported grpgen method '{}'".format(x['grpgen_method'])
            )

            ## apply grpgen base method
            tmp(cfg, my_subcfg, cfgpath_abs, x)

            ## apply grpgen generic overwrites
            merge_dicts(my_subcfg,
               (x['srcgrp'].get('grpgen', None) or {}).get(
                  'overwrites_all', {}
               )
            )

            ## apply grpgen overwrites specific to this group
            ## TODO

        return my_subcfg



class AllMembersNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'enable', DefaultSetterConstant(None)
        )

        self._add_defaultsetter(kwargs, 
          'default_role', DefaultSetterConstant(GITLAB_ROLE_LEVEL_MIN)
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          MemberUsersNormer(pluginref),
          MemberGroupsNormer(pluginref),
        ]

        super(AllMembersNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['members']


    def _handle_specifics_presub_group(self, cfg, my_subcfg, cfgpath_abs):
        pcfg = self.get_parentcfg(cfg, cfgpath_abs)
        my_subcfg['config']['gitlab_group'] = pcfg['fullpath']


    def _handle_specifics_presub_project(self, cfg, my_subcfg, cfgpath_abs):
        ## TODO: path and fullpath currently worng for projects
        pcfg = self.get_parentcfg(cfg, cfgpath_abs)
        ##my_subcfg['project'] = pcfg['fullpath']
        my_subcfg['config']['project'] = pcfg['path']


    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        users = my_subcfg.get('users', {}).get('members', {})
        grps = my_subcfg.get('groups', {}).get('members', {})

        ena = setdefault_none(my_subcfg, 'enable', bool(users or grps))

        if not ena:
            return my_subcfg

        pcfg = self.get_parentcfg(cfg, cfgpath_abs)

        c = setdefault_none(my_subcfg, 'config', {})
        setdefault_none(c, 'state', 'present')

        getattr(self, '_handle_specifics_presub_' + pcfg['object_type'])(
           cfg, my_subcfg, cfgpath_abs
        )

        return my_subcfg


    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        if not my_subcfg['enable']:
            return my_subcfg

        def handle_exclusiveness(basemap, config, cfgkey):
            exclusive = basemap['exclusive']

            if not exclusive:
                return

            if isinstance(exclusive, bool):
                ## note: upstream modules support exclusion based
                ##   on role level, so we convert simple bool
                ##   exclusiveness to all levels
                exclusive = GITLAB_ROLE_LEVELS[:]

            c[cfgkey] = exclusive

        c = my_subcfg['config']

        ## TODO: support extended attributes fro projects
        c['gitlab_users_access' ] = my_subcfg['users' ]['_members_export_lst']

        if 'project' not in c:
            c['gitlab_groups_access'] = my_subcfg['groups']['_members_export_lst']

        handle_exclusiveness(my_subcfg['users'], c, 'purge_users')

        if 'project' not in c:
            handle_exclusiveness(my_subcfg['groups'], c, 'purge_groups')

        return my_subcfg


class MembersUsersGroupsBaseNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'exclusive', DefaultSetterConstant(False)
        )

        self._add_defaultsetter(kwargs, 
          'default_role', DefaultSetterConstant(None)
        )

        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          self.member_type(pluginref),
        ]

        super(MembersUsersGroupsBaseNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    @abc.abstractmethod
    def member_type(self):
        pass

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        pcfg = self.get_parentcfg(cfg, cfgpath_abs)

        ## if unset, inherit default role from parent
        setdefault_none(my_subcfg, 'default_role', pcfg['default_role'])
        return my_subcfg

    def _modify_member_export_map(self, exmap, memkey, memval):
        return exmap

    def _handle_specifics_postsub(self, cfg, my_subcfg, cfgpath_abs):
        members_export_lst = []

        for k, v in my_subcfg['members'].items():
            members_export_lst.append(self._modify_member_export_map(
               {'access_level': v['role']}, k, v
            ))

        my_subcfg['_members_export_lst'] = members_export_lst
        return my_subcfg


class MemberUsersNormer(MembersUsersGroupsBaseNormer):

    @property
    def member_type(self):
        return MemberInstUserNormer

    @property
    def config_path(self):
        return ['users']

    def _modify_member_export_map(self, exmap, memkey, memval):
        exmap['name'] = memval['id']
        return exmap


class MemberGroupsNormer(MembersUsersGroupsBaseNormer):

    @property
    def member_type(self):
        return MemberInstGroupNormer

    @property
    def config_path(self):
        return ['groups']

    def _modify_member_export_map(self, exmap, memkey, memval):
        exmap['id'] = memval['id']

        if exmap['access_level'] == 'inherit':
            ##
            ## 'inherit' is a custom logical value which basically
            ## means source group memberships are mapped 1:1 to
            ## target group memberships, practically you currently
            ## get this by setting exmap role to maximum
            ##
            exmap['access_level'] = GITLAB_ROLE_LEVEL_MAX

        return exmap


class MemberInstBaseNormer(NormalizerNamed):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'role', DefaultSetterConstant(None)
        )

        super(MemberInstBaseNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['members', SUBDICT_METAKEY_ANY]

    @property
    def simpleform_key(self):
        return 'role'

    @property
    def name_key(self):
        return 'id'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        pcfg = self.get_parentcfg(cfg, cfgpath_abs, level=2)
        setdefault_none(my_subcfg, 'role', pcfg['default_role'])
        return my_subcfg


class MemberInstUserNormer(MemberInstBaseNormer):
    pass

class MemberInstGroupNormer(MemberInstBaseNormer):
    pass


class GroupInstNormerBase(GroupAbleInstNormerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          SubGroupsAllMormer(pluginref),
        ]

        super(GroupInstNormerBase, self).__init__(
           pluginref, *args, **kwargs
        )


class ProjectInstNormerBase(GroupAbleInstNormerBase):

    @property
    def config_path(self):
        return ['projects', SUBDICT_METAKEY_ANY]

    @property
    def object_type(self):
        return 'project'


class GrpInstBaseManagementNormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        self._add_defaultsetter(kwargs, 
          'enable', DefaultSetterConstant(True)
        )

        super(GrpInstBaseManagementNormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['basic_management']

    @property
    def simpleform_key(self):
        return 'enable'

    def _handle_specifics_presub(self, cfg, my_subcfg, cfgpath_abs):
        if not my_subcfg['enable']:
            return my_subcfg

        pcfg = self.get_parentcfg(cfg, cfgpath_abs)

        grp_create_cfg = setdefault_none(my_subcfg, 'configs', {})
        grp_create_cfg = setdefault_none(grp_create_cfg, 'grp_create', {})

        grp_create_cfg['name'] = pcfg['name']
        grp_create_cfg['path'] = pcfg['path']

        if pcfg['parent']:
            grp_create_cfg['parent'] = pcfg['parent']

        setdefault_none(grp_create_cfg, 'state', 'present')
        return my_subcfg


class TopLevelGroupInstNormer(GroupInstNormerBase):

    @property
    def config_path(self):
        return ['groups', SUBDICT_METAKEY_ANY]


class SubGroupsAllMormer(NormalizerBase):

    def __init__(self, pluginref, *args, **kwargs):
        subnorms = kwargs.setdefault('sub_normalizers', [])
        subnorms += [
          (SubGroupInstNormer, True),
        ]

        super(SubGroupsAllMormer, self).__init__(
           pluginref, *args, **kwargs
        )

    @property
    def config_path(self):
        return ['subgroups']


class SubGroupInstNormer(GroupInstNormerBase):

    NORMER_CONFIG_PATH = [SUBDICT_METAKEY_ANY]

    @property
    def toplevel(self):
        return False

    @property
    def config_path(self):
        return self.NORMER_CONFIG_PATH



class ActionModule(ConfigNormalizerBaseMerger):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(ConfigRootNormalizer(self), 
            *args, ##default_merge_vars=['gitlab_cfg_defaults'], 
            ##extra_merge_vars_ans=['extra_gitlab_config_maps'], 
            **kwargs
        )

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def my_ansvar(self):
        return 'smabot_git_gitlab_manage_groups_and_repos_args'

