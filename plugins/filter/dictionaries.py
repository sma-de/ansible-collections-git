

ANSIBLE_METADATA = {
    'metadata_version': '1.0',
    'status': ['preview'],
    'supported_by': 'community'
}


import copy
import collections

from ansible.errors import AnsibleFilterError, AnsibleOptionsError
from ansible.module_utils.six import iteritems, string_types
from ansible.module_utils.common._collections_compat import MutableMapping
from ansible.module_utils._text import to_native

from ansible_collections.smabot.base.plugins.module_utils.plugins.plugin_base import MAGIC_ARGSPECKEY_META
from ansible_collections.smabot.base.plugins.module_utils.plugins.filter_base import FilterBase

from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  merge_dicts, \
  setdefault_none

from ansible.utils.display import Display


display = Display()


##
## Converts gitserver user secret store mapping into a format
## fitting upstream hashivault write role
##
## note: currently supported structure to save secrets is:
##
##   -> each user has its own secret path, but all user sub
##      secrets (pw, ssh keys are combined there as keys)
##
## possible alternativest support:
##
##   -> each sub secret gets its own path, (one for the pw, and different ones for each ssh key) [TODO]
##   -> all secrets for all users under one single secret path (TODO)
##
##
class ConvertHashiVaultCfgFilter(FilterBase):

    FILTER_ID = 'to_hashivault_cfg'

    @property
    def argspec(self):
        tmp = super(ConvertHashiVaultCfgFilter, self).argspec

        tmp.update({
          'format': (list(string_types), 'per_user', ['per_user']),
          'write': ([bool], False),
          'secrets': ([collections.abc.Mapping], {}),
        })

        return tmp


    def _handle_format_per_user(self, hcfg, secrets, params, indict):
        write_mode = self.get_taskparam('write')

        secrets_cfg = {}

        spath_tmplate = params['vault_path_template']
        def_sets = params['settings']['defaults']

        def_keys = ['all']

        if write_mode:
            def_keys.append('write')
        else:
            def_keys.append('read')

        def_sets_merged = {}

        for x in def_keys:
            merge_dicts(def_sets_merged, copy.deepcopy(def_sets.get(x, {})))

        for k, v in secrets.items():
            ## build secret path by templating with username
            spath = spath_tmplate.format(username=k)

            if write_mode:
                tmp = {'data': v}
            else:
                tmp = {'data_keys': list(v.keys())}

            tmp['path'] = spath

            ## apply user level defaults
            v = merge_dicts(copy.deepcopy(def_sets_merged), tmp)

            ## apply user specific overwrites (TODO)

            secrets_cfg[k] = v

        topkey = 'get_secrets'

        if write_mode:
            topkey = 'set_secrets'

        secrets_cfg = {
          topkey: {
            'secrets': secrets_cfg,
          }
        }

        if not write_mode:
            secrets_cfg[topkey]['return_layout'] = 'mirror_inputcfg'

        merge_dicts(hcfg, secrets_cfg)


    def run_specific(self, indict):
        if not isinstance(indict, MutableMapping):
            raise AnsibleOptionsError(
               "filter input must be a dictionary, but given value"\
               " '{}' has type '{}'".format(indict, type(indict))
            )

        conv_fmt = self.get_taskparam('format')

        hcfg = indict['config']
        params = indict['parameters']
        secrets = self.get_taskparam('secrets') or indict['secrets']

        tmp = getattr(self, '_handle_format_' + conv_fmt, None)

        if not tmp: 
            raise AnsibleOptionsError(
               "Unsupported conversion format '{}'".format(conv_fmt)
            )

        tmp(hcfg, secrets, params, indict)

        return hcfg



class ConvertIllegalMembershipsAbsentingCfgFilter(FilterBase):

    FILTER_ID = 'to_illegal_memberships_absenting_cfg'


    def _handle_memships_basic(self, srcmap, resmaps,
       mship_basekey=None, memtype=None, srcmap_idkey=None,
       get_subtype_fn=None,
    ):
        if not srcmap or not srcmap.get(mship_basekey, None):
            return  ## noop

        rmap_x = resmaps['smabot_git_gitlab_manage_groups_and_repos_args']

        for k, v in srcmap[mship_basekey].items():
            stype = get_subtype_fn(v)

            rx = setdefault_none(rmap_x, stype, {})
            rx = setdefault_none(rx, stype, {})

            rx = setdefault_none(rx, v['full_path'], {
              "basic_management": {
                 "enable": False,
              },

              "grpgen": {
                 "base_method": 'inheriting',
              }
            })

            rx = setdefault_none(rx, 'members', {})

            rxcfg = setdefault_none(rx, 'config', {})
            rxcfg['state'] = 'absent'

            rx = setdefault_none(rx, memtype, {})
            rx = setdefault_none(rx, 'members', {})

            rx[srcmap[srcmap_idkey]] = None


    def _handle_user_memberships(self, indict, resmaps):
        self._handle_memships_basic(indict.get('user_memberships', None),
           resmaps, mship_basekey='memberships', memtype='users',
           srcmap_idkey='username', get_subtype_fn=lambda v: v['type'] + 's',
        )


    def _handle_group_sharings(self, indict, resmaps):
        self._handle_memships_basic(indict.get('group_sharings', None),
           resmaps, mship_basekey='group_sharings', memtype='groups',
           srcmap_idkey='group', get_subtype_fn=lambda v: 'groups',
        )


    def run_specific(self, indict):
        if not isinstance(indict, MutableMapping):
            raise AnsibleOptionsError(
               "filter input must be a dictionary, but given value"\
               " '{}' has type '{}'".format(indict, type(indict))
            )

        resmaps = {
          'smabot_git_gitlab_manage_groups_and_repos_args': {},
        }

        self._handle_user_memberships(indict, resmaps)
        self._handle_group_sharings(indict, resmaps)

        return resmaps



# ---- Ansible filters ----
class FilterModule(object):
    ''' generic dictionary filters '''

    def filters(self):
        res = {}

        tmp = [
          ConvertHashiVaultCfgFilter,
          ConvertIllegalMembershipsAbsentingCfgFilter,
        ]

        for f in tmp:
            res[f.FILTER_ID] = f()

        return res

